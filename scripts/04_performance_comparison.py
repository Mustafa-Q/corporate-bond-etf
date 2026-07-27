"""
Step 4 - Performance Comparison
================================
Head-to-head comparison of the WITHOUT-liquidity portfolio (pure characteristic
tracking, lambda = 0.0) vs. the WITH-liquidity portfolio (liquidity-aware,
lambda > 0) across every dimension named in the proposal:

    rating exposure, sector exposure, duration, yield,
    issuer concentration, liquidity profile, benchmark characteristic mismatch.

Both portfolios come from the SAME optimizer (Step 3), differing only in the
liquidity penalty. This isolates the effect of adding liquidity considerations.

Primary contrast is lambda=0.0 vs lambda=1.0 (a firmly liquidity-tilted book);
the full lambda path is also emitted so the trade-off is not cherry-picked.

Outputs (../output/step4/):
    step4_headline_comparison.csv     one row per (N, dimension) head-to-head
    step4_sector_exposure.csv         benchmark vs no-liq vs with-liq, per sector
    step4_rating_exposure.csv         benchmark vs no-liq vs with-liq, per rating
    step4_maturity_exposure.csv       benchmark vs no-liq vs with-liq, per bucket
    step4_full_lambda_path.csv        every dimension across all lambda, all N
    step4_summary.json                machine-readable headline deltas
"""
import sys, os, json, glob
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
from portfolio_utils import load_bonds, compute_benchmark_targets, evaluate_portfolio

OUT = os.path.join(os.path.dirname(__file__), '..', 'output', 'step4')
LIQ = os.path.join(os.path.dirname(__file__), '..', 'output', 'liquidity')
os.makedirs(OUT, exist_ok=True)

# lambda=0.0 is the "without liquidity" baseline; 1.0 the primary "with liquidity" book
NOLIQ_LAMBDA = 0.0
WITHLIQ_LAMBDA = 1.0
SIZES = [50, 100, 200]          # N=25 excluded: liquidity optimizer was infeasible there
ALL_LAMBDAS = [0.0, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0]


def load_weight_vector(bonds, n, lam):
    """Reconstruct a full-length (len==universe) weight vector from a saved file."""
    fname = f'optimization_liquidity_N{n}_lambda{lam}.csv'
    path = os.path.join(LIQ, fname)
    p = pd.read_csv(path)
    w = np.zeros(len(bonds))
    w[p['bond_id'].values] = p['Sampled_Weight'].values
    return w / w.sum()


def main():
    # load_bonds -> compute_rating_buckets uses default paths relative to CWD and
    # silently falls back to all-Unknown on FileNotFoundError. Build ratings with
    # explicit paths so the A-or-better / BBB split is populated regardless of CWD.
    from portfolio_utils import compute_rating_buckets
    bonds = load_bonds('../data/lqd_holdings_raw.csv', include_rating=False)
    bonds['Rating_Bucket'] = compute_rating_buckets(
        bonds, qlta_path='../data/qlta_holdings_raw.csv', lqdb_path='../data/lqdb_holdings_raw.csv')
    targets = compute_benchmark_targets(bonds)

    # ---- full evaluation of every (N, lambda) portfolio ----
    evals = {}
    for n in SIZES:
        for lam in ALL_LAMBDAS:
            w = load_weight_vector(bonds, n, lam)
            evals[(n, lam)] = evaluate_portfolio(bonds, w, targets)

    # ================================================================
    # 1. HEADLINE HEAD-TO-HEAD  (no-liq lambda=0.0  vs  with-liq lambda=1.0)
    # ================================================================
    dims = [
        ('Weighted-avg liquidity score', 'weighted_avg_liquidity_score', 'higher', 4),
        ('Yield to maturity (%)',        'ytm',                          'match',  4),
        ('YTM abs. diff vs benchmark (pp)', 'ytm_abs_diff_pct',          'lower',  4),
        ('Effective duration (yrs)',     'duration',                     'match',  3),
        ('Duration abs. diff vs benchmark (yrs)', 'duration_abs_diff',   'lower',  3),
        ('Sector L1 mismatch (pp)',      'sector_L1_deviation_pct',      'lower',  3),
        ('Rating L1 mismatch (pp)',      'rating_L1_deviation_pct',      'lower',  3),
        ('Maturity L1 mismatch (pp)',    'maturity_L1_deviation_pct',    'lower',  3),
        ('Top-10 issuer weight (%)',     'top10_issuer_weight_pct',      'lower',  2),
        ('Max single-issuer weight (%)', 'max_issuer_weight_pct',        'lower',  2),
        ('Issuer HHI',                   'issuer_hhi',                   'lower',  1),
    ]

    rows = []
    for n in SIZES:
        a = evals[(n, NOLIQ_LAMBDA)]
        b = evals[(n, WITHLIQ_LAMBDA)]
        for label, key, better, prec in dims:
            no = a[key]; wi = b[key]
            delta = wi - no
            if better == 'higher':
                verdict = 'liquidity better' if delta > 0 else ('tie' if abs(delta) < 1e-9 else 'liquidity worse')
            elif better == 'lower':
                verdict = 'liquidity better' if delta < 0 else ('tie' if abs(delta) < 1e-9 else 'liquidity worse')
            else:  # 'match' -> no directional verdict, level shown for reference
                verdict = 'reference level'
            rows.append({
                'N': n, 'dimension': label,
                'without_liquidity (lambda=0.0)': round(no, prec),
                'with_liquidity (lambda=1.0)':    round(wi, prec),
                'change': round(delta, prec),
                'objective': better,
                'verdict': verdict,
            })
    headline = pd.DataFrame(rows)
    headline.to_csv(os.path.join(OUT, 'step4_headline_comparison.csv'), index=False)

    # ================================================================
    # 2. SECTOR EXPOSURE  (benchmark vs no-liq vs with-liq)
    # ================================================================
    def exposure_frame(target_key, port_key):
        recs = []
        tgt = targets[target_key]
        for name, tval in sorted(tgt.items(), key=lambda kv: -kv[1]):
            rec = {'category': name, 'benchmark_%': round(tval * 100, 3)}
            for n in SIZES:
                rec[f'no_liq_N{n}_%'] = round(evals[(n, NOLIQ_LAMBDA)][port_key].get(name, 0) * 100, 3)
                rec[f'with_liq_N{n}_%'] = round(evals[(n, WITHLIQ_LAMBDA)][port_key].get(name, 0) * 100, 3)
            recs.append(rec)
        return pd.DataFrame(recs)

    sector = exposure_frame('sector_weights', 'sector_weights')
    rating = exposure_frame('rating_weights', 'rating_weights')
    maturity_order = ['0-1yr','1-2yr','2-3yr','3-5yr','5-7yr','7-10yr','10-15yr','15-20yr','20yr+']
    maturity = exposure_frame('maturity_weights', 'maturity_weights')
    maturity['__o'] = maturity['category'].map({m: i for i, m in enumerate(maturity_order)})
    maturity = maturity.sort_values('__o').drop(columns='__o')

    sector.to_csv(os.path.join(OUT, 'step4_sector_exposure.csv'), index=False)
    rating.to_csv(os.path.join(OUT, 'step4_rating_exposure.csv'), index=False)
    maturity.to_csv(os.path.join(OUT, 'step4_maturity_exposure.csv'), index=False)

    # ================================================================
    # 3. FULL LAMBDA PATH  (every dimension, every lambda, every N)
    # ================================================================
    path_rows = []
    for n in SIZES:
        for lam in ALL_LAMBDAS:
            e = evals[(n, lam)]
            path_rows.append({
                'N': n, 'lambda': lam,
                'liquidity_score': round(e['weighted_avg_liquidity_score'], 4),
                'ytm_%': round(e['ytm'], 4),
                'ytm_diff_pp': round(e['ytm_abs_diff_pct'], 4),
                'duration': round(e['duration'], 3),
                'duration_diff': round(e['duration_abs_diff'], 3),
                'sector_L1_pp': round(e['sector_L1_deviation_pct'], 3),
                'rating_L1_pp': round(e['rating_L1_deviation_pct'], 3),
                'maturity_L1_pp': round(e['maturity_L1_deviation_pct'], 3),
                'top10_issuer_%': round(e['top10_issuer_weight_pct'], 2),
                'max_issuer_%': round(e['max_issuer_weight_pct'], 2),
                'issuer_hhi': round(e['issuer_hhi'], 1),
            })
    path_df = pd.DataFrame(path_rows)
    path_df.to_csv(os.path.join(OUT, 'step4_full_lambda_path.csv'), index=False)

    # ================================================================
    # 4. SUMMARY JSON
    # ================================================================
    summary = {
        'comparison': {'without_liquidity_lambda': NOLIQ_LAMBDA, 'with_liquidity_lambda': WITHLIQ_LAMBDA},
        'sizes': SIZES,
        'benchmark': {'ytm_%': round(targets['ytm'], 4), 'duration': round(targets['duration'], 4)},
        'per_N': {},
    }
    for n in SIZES:
        a, b = evals[(n, NOLIQ_LAMBDA)], evals[(n, WITHLIQ_LAMBDA)]
        summary['per_N'][n] = {
            'liquidity_gain': round(b['weighted_avg_liquidity_score'] - a['weighted_avg_liquidity_score'], 4),
            'yield_change_pp': round(b['ytm'] - a['ytm'], 4),
            'sector_mismatch_change_pp': round(b['sector_L1_deviation_pct'] - a['sector_L1_deviation_pct'], 3),
            'rating_mismatch_change_pp': round(b['rating_L1_deviation_pct'] - a['rating_L1_deviation_pct'], 3),
            'duration_diff_change': round(b['duration_abs_diff'] - a['duration_abs_diff'], 3),
            'top10_issuer_change_pp': round(b['top10_issuer_weight_pct'] - a['top10_issuer_weight_pct'], 2),
        }
    with open(os.path.join(OUT, 'step4_summary.json'), 'w') as f:
        json.dump(summary, f, indent=2)

    print('Step 4 tables written to', os.path.abspath(OUT))
    print('\nHeadline (N=100):')
    print(headline[headline.N == 100][['dimension', 'without_liquidity (lambda=0.0)',
          'with_liquidity (lambda=1.0)', 'change', 'verdict']].to_string(index=False))
    return evals, targets, headline, sector, rating, maturity, path_df, summary


if __name__ == '__main__':
    main()
