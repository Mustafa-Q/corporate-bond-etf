"""
Step 3: Incorporating Liquidity

Extends the Step 2 optimization-based sampler with a soft liquidity penalty:
    objective = tracking_error(sector, maturity, duration, yield)
                - lambda_liq * weighted_avg_liquidity_score

lambda_liq is swept across a grid so we can trace out the accuracy/liquidity
trade-off frontier in Step 5. lambda_liq = 0 reproduces the Step 2 optimizer.

Liquidity score: --score par (default) is normalized log(Par Value), the historical
proxy -- see portfolio_utils.load_bonds for the caveat on what it does and doesn't
capture, and Step 6 for how weakly it tracks real trading activity. --score trace is the
TRACE-activity score (portfolio_utils.build_trace_liquidity_score); its outputs land in
the parallel output/liquidity_trace/ + output/step3_liquidity_sweep_trace.csv so both
frontiers coexist (Step 7 compares them). Run from the repo root.
"""
import argparse
import numpy as np
import pandas as pd
import cvxpy as cp
import os

from portfolio_utils import (SCORES, load_bonds, compute_benchmark_targets, evaluate_portfolio, output_paths,
                             sector_dummies, maturity_dummies, rating_dummies)

MAX_ISSUER_WEIGHT = 0.03
# Densified vs. the original 8-point grid (documented "noisy sweep" limitation). The "noise" is
# largely the relax-and-round heuristic's top_n_local = argsort(w_prior)[:n] step switching
# between DISCRETE candidate bond sets as lambda crosses certain thresholds -- not sampling
# noise around a smooth curve, so no grid density makes it perfectly continuous. What density
# does do: reveal the plateau/threshold structure (many nearby lambdas sharing one discrete
# selection) instead of a coarse grid landing on an unlucky single point that reads as an
# extreme outlier -- e.g. the original 8-point grid's N=50 Rating L1 spike to 4.06pp at
# lambda=1.0 is gone once 0.75/1.5 are added around it. Extra points concentrated at low
# lambda, where the trade-off moves fastest.
LAMBDA_GRID = [0.0, 0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05, 0.06,
               0.075, 0.09, 0.1, 0.125, 0.15, 0.175, 0.2, 0.25, 0.3, 0.4, 0.5, 0.75, 1.0, 1.5, 2.0]
SIZES_FOR_SWEEP = [50, 100, 200]  # N=25 excluded: liquidity optimizer infeasible there (see README)


def method_stratified(bonds, targets, n):
    """Reused from Step 2, needed here to build the candidate universe."""
    bonds = bonds.copy()
    bonds['stratum'] = (bonds['Sector'].astype(str) + ' | ' +
                         bonds['Rating_Bucket'].astype(str) + ' | ' +
                         bonds['Maturity_Bucket'].astype(str))
    strata_weight = bonds.groupby('stratum')['Weight_Renorm'].sum().sort_values(ascending=False)
    strata_count = bonds.groupby('stratum').size()

    raw_alloc = strata_weight * n
    floor_alloc = np.floor(raw_alloc).astype(int).clip(upper=strata_count)
    remainder = n - floor_alloc.sum()
    fractional = (raw_alloc - floor_alloc).sort_values(ascending=False)
    for stratum in fractional.index:
        if remainder <= 0:
            break
        if floor_alloc[stratum] < strata_count[stratum]:
            floor_alloc[stratum] += 1
            remainder -= 1

    w = np.zeros(len(bonds))
    for stratum, k in floor_alloc.items():
        if k == 0:
            continue
        sub = bonds[bonds['stratum'] == stratum]
        picked = sub.nlargest(int(k), 'Weight_Renorm')
        stratum_total_weight = sub['Weight_Renorm'].sum()
        scaled = picked['Weight_Renorm'] / picked['Weight_Renorm'].sum() * stratum_total_weight
        w[picked['bond_id'].values] = scaled.values
    return w / w.sum()


def method_optimization_liquidity(bonds, targets, n, lambda_liq, candidate_multiplier=3, n_reweight_iters=4):
    n_bonds = len(bonds)
    sec_dum = sector_dummies(bonds).values.astype(float)
    mat_dum = maturity_dummies(bonds).values.astype(float)
    rat_dum = rating_dummies(bonds).values.astype(float)
    duration = bonds['Duration'].values.astype(float)
    ytm = bonds['YTM (%)'].values.astype(float)
    liquidity = bonds['Liquidity_Score'].values.astype(float)

    sector_target = np.array([targets['sector_weights'].get(c, 0.0) for c in sector_dummies(bonds).columns])
    maturity_target = np.array([targets['maturity_weights'].get(c, 0.0) for c in maturity_dummies(bonds).columns])
    rating_target = np.array([targets['rating_weights'].get(c, 0.0) for c in rating_dummies(bonds).columns])
    dur_target = targets['duration']
    ytm_target = targets['ytm']

    n_candidates_target = min(n_bonds, n * candidate_multiplier)
    candidate_w = method_stratified(bonds, targets, n_candidates_target)
    candidate_idx = np.where(candidate_w > 1e-10)[0]
    if len(candidate_idx) < n:
        candidate_idx = np.argsort(-bonds['Weight_Renorm'].values)[:max(n * 2, n_candidates_target)]

    issuer_ids = bonds['Name'].values
    unique_issuers, issuer_map = np.unique(issuer_ids[candidate_idx], return_inverse=True)

    m = len(candidate_idx)
    sec_c = sec_dum[candidate_idx]
    mat_c = mat_dum[candidate_idx]
    rat_c = rat_dum[candidate_idx]
    dur_c = duration[candidate_idx]
    ytm_c = ytm[candidate_idx]
    liq_c = liquidity[candidate_idx]

    def solve_qp(candidate_positions, weights_prior=None, position_cap=None, position_floor=None):
        k = len(candidate_positions)
        w = cp.Variable(k, nonneg=True)

        sector_dev = sec_c[candidate_positions].T @ w - sector_target
        maturity_dev = mat_c[candidate_positions].T @ w - maturity_target
        rating_dev = rat_c[candidate_positions].T @ w - rating_target
        dur_dev = dur_c[candidate_positions] @ w - dur_target
        ytm_dev = ytm_c[candidate_positions] @ w - ytm_target
        avg_liquidity = liq_c[candidate_positions] @ w

        objective = (
            5.0 * cp.sum_squares(sector_dev) +
            5.0 * cp.sum_squares(maturity_dev) +
            5.0 * cp.sum_squares(rating_dev) +
            1.0 * cp.square(dur_dev) / (dur_target ** 2) +
            1.0 * cp.square(ytm_dev) / (ytm_target ** 2) -
            lambda_liq * avg_liquidity
        )

        if weights_prior is not None:
            eps = 1e-3
            reweight = 1.0 / (weights_prior + eps)
            objective = objective + 0.02 * (reweight @ w)

        local_issuer_ids = issuer_map[candidate_positions]
        n_unique_issuers = len(np.unique(local_issuer_ids))
        effective_cap = max(MAX_ISSUER_WEIGHT, 1.0 / n_unique_issuers)
        constraints = [cp.sum(w) == 1]
        for iid in np.unique(local_issuer_ids):
            mask = local_issuer_ids == iid
            constraints.append(cp.sum(w[mask]) <= effective_cap)

        if position_cap is not None:
            constraints.append(w <= position_cap)
        if position_floor is not None:
            # forces every one of the k selected candidates to retain positive
            # weight, so "N holdings" is exactly N by construction rather than
            # an emergent (and unreliable) property of the QP solution
            constraints.append(w >= position_floor)

        prob = cp.Problem(cp.Minimize(objective), constraints)
        prob.solve(solver=cp.OSQP, verbose=False, max_iter=20000)
        if w.value is None:
            prob.solve(solver=cp.CLARABEL, verbose=False)
        if w.value is None:
            return np.full(k, 1.0 / k)
        return np.clip(w.value, 0, None)

    positions = np.arange(m)
    w_prior = np.full(m, 1.0 / m)
    stage_a_cap = min(MAX_ISSUER_WEIGHT, 4.0 / n)  # loose cap, mainly to prevent early collapse
    for _ in range(n_reweight_iters):
        w_prior = solve_qp(positions, weights_prior=w_prior, position_cap=stage_a_cap)

    top_n_local = np.argsort(-w_prior)[:n]
    stage_b_cap = min(MAX_ISSUER_WEIGHT, 4.0 / n)      # each position <= 4x its equal-weight share
    stage_b_floor = 0.2 / n                             # each position >= 0.2x its equal-weight share
    w_final_local = solve_qp(top_n_local, weights_prior=None, position_cap=stage_b_cap, position_floor=stage_b_floor)

    w = np.zeros(n_bonds)
    w[candidate_idx[top_n_local]] = w_final_local
    return w / w.sum()


def run_sweep(score='par'):
    paths = output_paths(score)
    os.makedirs(paths['weights_dir'], exist_ok=True)
    bonds = load_bonds(score=score)
    targets = compute_benchmark_targets(bonds)

    rows = []
    for n in SIZES_FOR_SWEEP:
        for lam in LAMBDA_GRID:
            print(f"N={n}, lambda_liq={lam} ...")
            w = method_optimization_liquidity(bonds, targets, n, lam)
            metrics = evaluate_portfolio(bonds, w, targets)
            metrics['target_n'] = n
            metrics['lambda_liq'] = lam
            rows.append(metrics)

            cols = ['bond_id', 'Name', 'Sector', 'Maturity_Bucket', 'Par Value', 'Liquidity_Score', 'Weight_Renorm']
            if score == 'trace':
                cols += ['Liquidity_Score_Par', 'Liquidity_Score_Source']
            out = bonds[cols].copy()
            out['Sampled_Weight'] = w
            out = out[out['Sampled_Weight'] > 1e-10].sort_values('Sampled_Weight', ascending=False)
            out.to_csv(f"{paths['weights_dir']}/optimization_liquidity_N{n}_lambda{lam}.csv", index=False)

    summary = pd.json_normalize(rows)
    cols = ['target_n', 'lambda_liq', 'n_holdings', 'weighted_avg_liquidity_score',
            'sector_L1_deviation_pct', 'maturity_L1_deviation_pct', 'duration',
            'duration_abs_diff', 'ytm', 'ytm_abs_diff_pct', 'top10_issuer_weight_pct',
            'max_issuer_weight_pct', 'issuer_hhi']
    summary = summary[cols + [c for c in summary.columns if c not in cols]]
    summary.to_csv(paths['sweep_csv'], index=False)
    print(f"\nSaved {paths['sweep_csv']}")
    print(summary[cols].round(4).to_string(index=False))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--score', choices=SCORES, default='par', help='liquidity score to optimise on (default: par)')
    args = ap.parse_args()
    run_sweep(score=args.score)
