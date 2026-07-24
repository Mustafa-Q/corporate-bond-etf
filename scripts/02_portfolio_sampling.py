"""
Step 2: Portfolio Sampling
Constructs reduced portfolios of N = 25, 50, 100, 200 bonds using three
methods, and scores each against the full-benchmark targets from Step 1.

Method 1 - Largest Holdings: top N bonds by benchmark weight, reweighted
    proportionally so weights sum to 1.

Method 2 - Stratified Sampling: strata = Sector x Maturity Bucket (rating
    omitted - not available in source data, see Step 1 notes). Holdings are
    allocated to strata proportional to benchmark weight (largest-remainder
    rounding), the largest bonds within each stratum are selected, and each
    selected bond is reweighted so its stratum's total weight matches the
    benchmark's stratum weight exactly.

Method 3 - Optimization-Based Sampling: a QP that minimizes a weighted sum
    of (a) sector-weight deviation, (b) maturity-bucket-weight deviation,
    (c) duration deviation, and (d) yield deviation from the benchmark,
    subject to: fully invested (sum w = 1), long-only (w >= 0), and a
    per-issuer concentration cap. Cardinality (exactly N holdings) is a
    combinatorial constraint that turns this into a MIQP, which is
    intractable to solve exactly at this universe size (3,143 bonds). We use
    the standard relax-and-round approach: solve a continuous QP over a
    stratified candidate universe (3x the target N, so the optimizer has
    room to choose from a diverse candidate set) with an iteratively
    reweighted L1 penalty that pushes small weights toward zero (Candes,
    Wakin & Boyd 2008), take the top-N resulting weights, then re-solve the
    QP restricted to exactly that support to sharpen the fit.
"""
import numpy as np
import pandas as pd
import cvxpy as cp
import json
import os

from portfolio_utils import (load_bonds, compute_benchmark_targets, evaluate_portfolio,
                              sector_dummies, maturity_dummies, rating_dummies)

SIZES = [25, 50, 100, 200]
MAX_ISSUER_WEIGHT = 0.03  # 3% per-issuer cap, applied in Method 3 and available for Method 1/2 reporting

os.makedirs('output/portfolios', exist_ok=True)


def method_largest_holdings(bonds, targets, n):
    top = bonds.nlargest(n, 'Weight_Renorm')
    w = np.zeros(len(bonds))
    w[top['bond_id'].values] = top['Weight_Renorm'].values
    w = w / w.sum()
    return w


def method_stratified(bonds, targets, n):
    bonds = bonds.copy()
    bonds['stratum'] = (bonds['Sector'].astype(str) + ' | ' +
                         bonds['Rating_Bucket'].astype(str) + ' | ' +
                         bonds['Maturity_Bucket'].astype(str))
    strata_weight = bonds.groupby('stratum')['Weight_Renorm'].sum().sort_values(ascending=False)
    strata_count = bonds.groupby('stratum').size()

    # largest-remainder allocation of n holdings across strata, proportional to weight
    raw_alloc = strata_weight * n
    floor_alloc = np.floor(raw_alloc).astype(int)
    # cap by number of bonds actually available in the stratum
    floor_alloc = floor_alloc.clip(upper=strata_count)
    remainder = n - floor_alloc.sum()
    fractional = (raw_alloc - floor_alloc).sort_values(ascending=False)
    for stratum in fractional.index:
        if remainder <= 0:
            break
        if floor_alloc[stratum] < strata_count[stratum]:
            floor_alloc[stratum] += 1
            remainder -= 1
    alloc = floor_alloc

    w = np.zeros(len(bonds))
    for stratum, k in alloc.items():
        if k == 0:
            continue
        sub = bonds[bonds['stratum'] == stratum]
        picked = sub.nlargest(int(k), 'Weight_Renorm')
        stratum_total_weight = sub['Weight_Renorm'].sum()  # full stratum weight, incl. unselected bonds
        picked_weight_sum = picked['Weight_Renorm'].sum()
        # reweight selected bonds proportionally so they carry the FULL stratum weight
        scaled = picked['Weight_Renorm'] / picked_weight_sum * stratum_total_weight
        w[picked['bond_id'].values] = scaled.values

    w = w / w.sum()
    return w


def method_optimization(bonds, targets, n, candidate_multiplier=3, n_reweight_iters=4):
    n_bonds = len(bonds)
    sec_dum = sector_dummies(bonds).values.astype(float)
    mat_dum = maturity_dummies(bonds).values.astype(float)
    rat_dum = rating_dummies(bonds).values.astype(float)
    duration = bonds['Duration'].values.astype(float)
    ytm = bonds['YTM (%)'].values.astype(float)

    sector_target = np.array([targets['sector_weights'].get(c, 0.0) for c in sector_dummies(bonds).columns])
    maturity_target = np.array([targets['maturity_weights'].get(c, 0.0) for c in maturity_dummies(bonds).columns])
    rating_target = np.array([targets['rating_weights'].get(c, 0.0) for c in rating_dummies(bonds).columns])
    dur_target = targets['duration']
    ytm_target = targets['ytm']

    # --- Build a diverse candidate universe via the stratified method (oversized) ---
    n_candidates_target = min(n_bonds, n * candidate_multiplier)
    candidate_w = method_stratified(bonds, targets, n_candidates_target)
    candidate_idx = np.where(candidate_w > 1e-10)[0]
    if len(candidate_idx) < n:
        candidate_idx = np.argsort(-bonds['Weight_Renorm'].values)[:max(n * 2, n_candidates_target)]

    issuer_ids = bonds['Name'].values
    unique_issuers, issuer_map = np.unique(issuer_ids[candidate_idx], return_inverse=True)
    n_issuers = len(unique_issuers)

    m = len(candidate_idx)
    sec_c = sec_dum[candidate_idx]
    mat_c = mat_dum[candidate_idx]
    rat_c = rat_dum[candidate_idx]
    dur_c = duration[candidate_idx]
    ytm_c = ytm[candidate_idx]

    def solve_qp(candidate_positions, weights_prior=None, equality_support=False, position_cap=None, position_floor=None):
        k = len(candidate_positions)
        w = cp.Variable(k, nonneg=True)

        sector_dev = sec_c[candidate_positions].T @ w - sector_target
        maturity_dev = mat_c[candidate_positions].T @ w - maturity_target
        rating_dev = rat_c[candidate_positions].T @ w - rating_target
        dur_dev = dur_c[candidate_positions] @ w - dur_target
        ytm_dev = ytm_c[candidate_positions] @ w - ytm_target

        # normalize each block's contribution (roughly comparable scales)
        objective = (
            5.0 * cp.sum_squares(sector_dev) +
            5.0 * cp.sum_squares(maturity_dev) +
            5.0 * cp.sum_squares(rating_dev) +
            1.0 * cp.square(dur_dev) / (dur_target ** 2) +
            1.0 * cp.square(ytm_dev) / (ytm_target ** 2)
        )

        if weights_prior is not None:
            eps = 1e-3
            reweight = 1.0 / (weights_prior + eps)
            objective = objective + 0.02 * (reweight @ w)

        constraints = [cp.sum(w) == 1]
        if not equality_support:
            local_issuer_ids = issuer_map[candidate_positions]
            n_unique_issuers = len(np.unique(local_issuer_ids))
            # cap must allow at least n_unique_issuers * cap >= 1, else the
            # problem is infeasible by construction (too few issuers in a
            # small candidate support to satisfy a strict cap)
            effective_cap = max(MAX_ISSUER_WEIGHT, 1.0 / n_unique_issuers)
            for iid in np.unique(local_issuer_ids):
                mask = local_issuer_ids == iid
                constraints.append(cp.sum(w[mask]) <= effective_cap)

        if position_cap is not None:
            constraints.append(w <= position_cap)
        if position_floor is not None:
            # forces every one of the k selected candidates to retain positive
            # weight, so "N holdings" is exactly N by construction
            constraints.append(w >= position_floor)

        prob = cp.Problem(cp.Minimize(objective), constraints)
        prob.solve(solver=cp.OSQP, verbose=False, max_iter=20000)
        if w.value is None:
            prob.solve(solver=cp.CLARABEL, verbose=False)
        if w.value is None:
            print(f"    WARNING: solver failed for k={k}, falling back to equal weights")
            return np.full(k, 1.0 / k)
        return np.clip(w.value, 0, None)

    # --- Stage A: reweighted-L1 QP over the full candidate set to induce sparsity ---
    positions = np.arange(m)
    w_prior = np.full(m, 1.0 / m)
    stage_a_cap = min(MAX_ISSUER_WEIGHT, 4.0 / n)
    for _ in range(n_reweight_iters):
        w_prior = solve_qp(positions, weights_prior=w_prior, position_cap=stage_a_cap)

    # --- Stage B: take the top-N candidates by weight, re-solve restricted QP ---
    top_n_local = np.argsort(-w_prior)[:n]
    stage_b_cap = min(MAX_ISSUER_WEIGHT, 4.0 / n)
    stage_b_floor = 0.2 / n
    w_final_local = solve_qp(top_n_local, weights_prior=None, equality_support=False,
                              position_cap=stage_b_cap, position_floor=stage_b_floor)

    w = np.zeros(n_bonds)
    chosen_global_idx = candidate_idx[top_n_local]
    w[chosen_global_idx] = w_final_local
    w = w / w.sum()
    return w


def run_all():
    bonds = load_bonds()
    targets = compute_benchmark_targets(bonds)
    with open('output/benchmark_targets.json', 'w') as f:
        json.dump(targets, f, indent=2, default=float)

    methods = {
        'largest_holdings': method_largest_holdings,
        'stratified': method_stratified,
        'optimization': method_optimization,
    }

    results = []
    for method_name, fn in methods.items():
        for n in SIZES:
            print(f"Running {method_name} N={n} ...")
            w = fn(bonds, targets, n)
            metrics = evaluate_portfolio(bonds, w, targets)
            metrics['method'] = method_name
            metrics['target_n'] = n
            results.append(metrics)

            out = bonds[['bond_id', 'Name', 'Sector', 'Rating_Bucket', 'Maturity_Bucket', 'Weight_Renorm']].copy()
            out['Sampled_Weight'] = w
            out = out[out['Sampled_Weight'] > 1e-10].sort_values('Sampled_Weight', ascending=False)
            out.to_csv(f'output/portfolios/{method_name}_N{n}.csv', index=False)

    summary = pd.json_normalize(results)
    cols_order = ['method', 'target_n', 'n_holdings', 'sector_L1_deviation_pct',
                  'maturity_L1_deviation_pct', 'rating_L1_deviation_pct', 'duration', 'duration_abs_diff',
                  'ytm', 'ytm_abs_diff_pct', 'top10_issuer_weight_pct',
                  'max_issuer_weight_pct', 'issuer_hhi']
    summary = summary[cols_order + [c for c in summary.columns if c not in cols_order]]
    summary.to_csv('output/step2_sampling_summary.csv', index=False)
    print("\nSaved output/step2_sampling_summary.csv")
    print(summary[cols_order].round(3).to_string(index=False))


if __name__ == '__main__':
    run_all()
