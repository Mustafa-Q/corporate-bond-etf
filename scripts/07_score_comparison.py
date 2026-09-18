"""
Step 7: Did the par-based liquidity tilt buy tradability, or just size?

Steps 3-5 were run twice: once optimising on the historical par-holding liquidity score
(--score par, output/liquidity/, step4/, step5/) and once on the TRACE-activity score
(--score trace, output/liquidity_trace/, step4_trace/, step5_trace/). This script
cross-evaluates every portfolio from BOTH sweeps under BOTH scores: what does the
par-optimised lambda=1 book score on real TRACE activity, and vice versa? Run from scripts/.

Outputs (output/step7/):
  step7_cross_evaluation.csv   every (optimised_on, N, lambda): weighted-avg par score,
                               weighted-avg TRACE score, imputed-weight share, and the
                               Step 4 mismatch dimensions
  step7_headline.csv           lambda=0 vs lambda=1 per book and N: score gains and costs
  step7_summary.json           headline numbers + plain verdict (rule fixed in the design
                               spec before the numbers were seen)
  charts/chart1_frontier_par_vs_trace.png   sector mismatch vs weighted TRACE score, both books
  charts/chart2_trace_gain_at_lambda1.png   TRACE-score gain at lambda=1, both books, per N
"""
import json
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from portfolio_utils import load_bonds, output_paths

OUTPUT_ROOT = os.path.join(os.path.dirname(__file__), '..', 'output')
SIZES = [50, 100, 200]
LAMBDAS = [0.0, 0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05, 0.06,
           0.075, 0.09, 0.1, 0.125, 0.15, 0.175, 0.2, 0.25, 0.3, 0.4, 0.5, 0.75, 1.0, 1.5, 2.0]
NOLIQ_LAMBDA, WITHLIQ_LAMBDA = 0.0, 1.0
BOOKS = {'par': 'par-optimised', 'trace': 'TRACE-optimised'}

# --- palette, consistent with Steps 4-6 ---
INK, MUTED, GRID = '#2b2a26', '#898781', '#e1e0d9'
BOOK_COLORS = {'par': '#6b7a8f', 'trace': '#b9762f'}
plt.rcParams.update({
    'font.family': 'DejaVu Sans', 'font.size': 10, 'text.color': INK,
    'axes.edgecolor': MUTED, 'axes.labelcolor': INK, 'xtick.color': MUTED,
    'ytick.color': MUTED, 'axes.titlecolor': INK, 'figure.facecolor': 'white',
    'axes.facecolor': 'white', 'axes.grid': True, 'grid.color': GRID,
    'axes.spines.top': False, 'axes.spines.right': False,
})


def cross_evaluate(bonds, weights_dir, sizes, lambdas, optimised_on):
    """Weighted-average par and TRACE scores (and the weight sitting in imputed-score bonds)
    of every saved portfolio in weights_dir, regardless of which score built it."""
    b = bonds.set_index('bond_id')
    par, trace = b['Liquidity_Score_Par'], b['Liquidity_Score_TRACE']
    imputed = b['Liquidity_Score_Source'].astype(str).str.startswith('imputed')
    rows = []
    for n in sizes:
        for lam in lambdas:
            p = pd.read_csv(os.path.join(weights_dir, f'optimization_liquidity_N{n}_lambda{lam}.csv'))
            w = p.set_index('bond_id')['Sampled_Weight']
            w = w / w.sum()
            rows.append({'optimised_on': optimised_on, 'target_n': n, 'lambda_liq': lam,
                         'wavg_par_score': float((par.loc[w.index] * w).sum()),
                         'wavg_trace_score': float((trace.loc[w.index] * w).sum()),
                         'imputed_weight_share': float((imputed.loc[w.index] * w).sum())})
    return pd.DataFrame(rows)


def verdict_for(gains):
    """gains: {N: (TRACE-score gain of the par-optimised book at lambda=1,
                   TRACE-score gain of the TRACE-optimised book at lambda=1)}.
    Rule from the Phase 2 design spec, fixed before the numbers were seen."""
    rel = {}
    for n, (par_gain, trace_gain) in gains.items():
        if par_gain != 0:
            rel[n] = (trace_gain - par_gain) / abs(par_gain)
        else:
            rel[n] = np.inf if trace_gain > 0 else (-np.inf if trace_gain < 0 else 0.0)
    detail = '; '.join(f'N={n}: par book +{g[0]:.3f}, TRACE book +{g[1]:.3f} TRACE score at lambda=1'
                       for n, g in sorted(gains.items()))
    if all(r > 0.5 for r in rel.values()):
        return ('bought size, not tradability: optimising on TRACE activity raises the book\'s real trading '
                f'activity by more than 50% more than the par tilt does at every N ({detail})')
    if all(abs(r) <= 0.25 for r in rel.values()):
        return f'captured most of the available tradability: the two tilts deliver TRACE-score gains within 25% at every N ({detail})'
    return f'mixed: the excess TRACE-score gain of the TRACE tilt over the par tilt is not consistent across N ({detail})'


def pareto_max_y_min_x(df, x, y):
    """Points not dominated by another with lower x and higher y."""
    d = df.sort_values(x).reset_index(drop=True)
    keep = [i for i, r in d.iterrows()
            if not ((d[x] <= r[x]) & (d[y] >= r[y]) & ((d[x] < r[x]) | (d[y] > r[y]))).any()]
    return d.loc[keep]


def main():
    out = os.path.join(OUTPUT_ROOT, 'step7')
    charts = os.path.join(out, 'charts')
    os.makedirs(charts, exist_ok=True)
    bonds = load_bonds('../data/lqd_holdings_raw.csv', include_rating=False, score='trace')

    # ---- cross-evaluation grid -----------------------------------------------------
    parts = []
    for score in BOOKS:
        paths = output_paths(score, root=OUTPUT_ROOT)
        ce = cross_evaluate(bonds, paths['weights_dir'], SIZES, LAMBDAS, score)
        p4 = pd.read_csv(os.path.join(paths['step4_dir'], 'step4_full_lambda_path.csv'))
        p4 = p4.rename(columns={'N': 'target_n', 'lambda': 'lambda_liq', 'ytm_%': 'ytm',
                                'top10_issuer_%': 'top10_issuer_pct', 'max_issuer_%': 'max_issuer_pct'})
        p4 = p4[['target_n', 'lambda_liq', 'sector_L1_pp', 'rating_L1_pp', 'maturity_L1_pp', 'duration_diff',
                 'ytm', 'ytm_diff_pp', 'top10_issuer_pct', 'issuer_hhi']]
        parts.append(ce.merge(p4, on=['target_n', 'lambda_liq'], how='left'))
    grid = pd.concat(parts, ignore_index=True)
    grid.to_csv(os.path.join(out, 'step7_cross_evaluation.csv'), index=False)

    # ---- headline: lambda=0 vs lambda=1 per book and N -------------------------------
    rows, gains = [], {}
    for n in SIZES:
        for score in BOOKS:
            g = grid[(grid.optimised_on == score) & (grid.target_n == n)].set_index('lambda_liq')
            a, b = g.loc[NOLIQ_LAMBDA], g.loc[WITHLIQ_LAMBDA]
            rows.append({
                'optimised_on': score, 'target_n': n,
                'trace_score_lambda0': round(a['wavg_trace_score'], 4), 'trace_score_lambda1': round(b['wavg_trace_score'], 4),
                'trace_score_gain': round(b['wavg_trace_score'] - a['wavg_trace_score'], 4),
                'par_score_lambda0': round(a['wavg_par_score'], 4), 'par_score_lambda1': round(b['wavg_par_score'], 4),
                'par_score_gain': round(b['wavg_par_score'] - a['wavg_par_score'], 4),
                'sector_L1_cost_pp': round(b['sector_L1_pp'] - a['sector_L1_pp'], 3),
                'top10_issuer_cost_pp': round(b['top10_issuer_pct'] - a['top10_issuer_pct'], 2),
                'ytm_change_pp': round(b['ytm'] - a['ytm'], 4),
                'imputed_weight_share_lambda1': round(b['imputed_weight_share'], 4),
            })
        hp = next(r for r in rows if r['optimised_on'] == 'par' and r['target_n'] == n)
        ht = next(r for r in rows if r['optimised_on'] == 'trace' and r['target_n'] == n)
        gains[n] = (hp['trace_score_gain'], ht['trace_score_gain'])
    headline = pd.DataFrame(rows)
    headline.to_csv(os.path.join(out, 'step7_headline.csv'), index=False)
    print(headline.to_string(index=False))

    verdict = verdict_for(gains)
    summary = {
        'comparison': {'without_liquidity_lambda': NOLIQ_LAMBDA, 'with_liquidity_lambda': WITHLIQ_LAMBDA},
        'scores': {'par': 'normalised log LQD par holding (Steps 3-5 as originally run)',
                   'trace': 'normalised log(1 + TRACE prints, 2025-09-04..2025-12-04), peers imputed for '
                            'bonds without full-window activity (portfolio_utils.build_trace_liquidity_score)'},
        'universe_score_sources': bonds['Liquidity_Score_Source'].value_counts().to_dict(),
        'benchmark_imputed_weight_share': round(float(bonds.loc[bonds['Liquidity_Score_Source'].str.startswith('imputed'),
                                                                   'Weight_Renorm'].sum()), 4),
        'spearman_par_vs_trace_score_universe': round(float(bonds['Liquidity_Score_Par'].corr(
            bonds['Liquidity_Score_TRACE'], method='spearman')), 4),
        'per_N': {int(n): {
            'par_book_trace_gain': gains[n][0], 'trace_book_trace_gain': gains[n][1],
            'par_book_sector_cost_pp': float(headline[(headline.optimised_on == 'par') & (headline.target_n == n)]['sector_L1_cost_pp'].iloc[0]),
            'trace_book_sector_cost_pp': float(headline[(headline.optimised_on == 'trace') & (headline.target_n == n)]['sector_L1_cost_pp'].iloc[0]),
            'par_book_top10_cost_pp': float(headline[(headline.optimised_on == 'par') & (headline.target_n == n)]['top10_issuer_cost_pp'].iloc[0]),
            'trace_book_top10_cost_pp': float(headline[(headline.optimised_on == 'trace') & (headline.target_n == n)]['top10_issuer_cost_pp'].iloc[0]),
        } for n in SIZES},
        'verdict': verdict,
    }
    with open(os.path.join(out, 'step7_summary.json'), 'w') as f:
        json.dump(summary, f, indent=2)
    print('\nVERDICT:', verdict)

    # ---- chart 1: sector mismatch vs weighted TRACE score, both books, per N ----------
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.8), sharey=True)
    for ax, n in zip(axes, SIZES):
        for score, label in BOOKS.items():
            g = grid[(grid.optimised_on == score) & (grid.target_n == n)]
            c = BOOK_COLORS[score]
            ax.scatter(g['sector_L1_pp'], g['wavg_trace_score'], s=16, color=c, alpha=0.35, edgecolor='none', zorder=2)
            eff = pareto_max_y_min_x(g, 'sector_L1_pp', 'wavg_trace_score')
            ax.plot(eff['sector_L1_pp'], eff['wavg_trace_score'], '-o', color=c, markersize=4, linewidth=2,
                    label=f'{label} (Pareto frontier)', zorder=3)
            for lam, marker in ((NOLIQ_LAMBDA, 's'), (WITHLIQ_LAMBDA, 'D')):
                r = g[g.lambda_liq == lam].iloc[0]
                ax.scatter(r['sector_L1_pp'], r['wavg_trace_score'], s=46, marker=marker, color='white',
                           edgecolor=c, linewidth=1.6, zorder=4)
        ax.set_title(f'N = {n}', fontsize=11)
        ax.set_xlabel('Sector L1 mismatch vs benchmark (pp)')
        ax.set_axisbelow(True)
    axes[0].set_ylabel('Weighted-avg TRACE activity score of the book')
    handles, labels = axes[0].get_legend_handles_labels()
    handles += [plt.Line2D([], [], marker='s', color='white', markeredgecolor=MUTED, linestyle='', markersize=7),
                plt.Line2D([], [], marker='D', color='white', markeredgecolor=MUTED, linestyle='', markersize=7)]
    labels += ['λ = 0', 'λ = 1']
    axes[0].legend(handles, labels, loc='lower right', frameon=False, fontsize=8.5)
    fig.suptitle('What each liquidity tilt buys in real trading activity, and what it costs in sector fidelity\n'
                 '(faint dots = full λ grid; squares/diamonds = the λ = 0 and λ = 1 books compared in Step 4)',
                 fontsize=12)
    fig.savefig(os.path.join(charts, 'chart1_frontier_par_vs_trace.png'), dpi=150, bbox_inches='tight')
    plt.close(fig)

    # ---- chart 2: TRACE-score gain at lambda=1, both books, per N ------------------
    fig, ax = plt.subplots(figsize=(7, 4.2))
    x = np.arange(len(SIZES))
    width = 0.36
    for k, (score, label) in enumerate(BOOKS.items()):
        vals = [gains[n][k] for n in SIZES]
        bars = ax.bar(x + (k - 0.5) * width, vals, width * 0.94, color=BOOK_COLORS[score], label=label)
        for b_, v in zip(bars, vals):
            ax.text(b_.get_x() + b_.get_width() / 2, b_.get_height() + (0.002 if v >= 0 else -0.006),
                    f'{v:+.3f}', ha='center', va='bottom', fontsize=9, color=INK)
    ax.axhline(0, color=MUTED, linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([f'N = {n}' for n in SIZES])
    ax.set_ylabel('TRACE score gain, λ = 1 vs λ = 0')
    ax.set_title('Real trading-activity gain from each liquidity tilt', fontsize=11)
    ax.grid(axis='x', visible=False)
    ax.legend(frameon=False, fontsize=9)
    fig.savefig(os.path.join(charts, 'chart2_trace_gain_at_lambda1.png'), dpi=150, bbox_inches='tight')
    plt.close(fig)

    print(f'\nWrote {os.path.abspath(out)}')


if __name__ == '__main__':
    main()
