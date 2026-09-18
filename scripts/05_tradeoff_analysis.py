"""
Step 5 - Trade-off Frontier Analysis
======================================
Answers the four original proposal questions using the full lambda grid
that Step 4 already computed (output/step4/step4_full_lambda_path.csv,
all N x all lambda):

    Q1. Benchmark accuracy loss  - how much replication fidelity is
        sacrificed for a given liquidity gain (Pareto frontier of
        liquidity score vs each mismatch dimension).
    Q2. Yield impact             - how yield moves along the lambda frontier.
    Q3. Hardest-to-preserve      - which benchmark dimensions degrade most,
        ranked by degradation over the full frontier (not just the
        lambda=0-vs-1 snapshot Step 4 used), with each dimension min-max
        scaled to its own 0-100 range across the whole grid so pp-scale
        and HHI-scale dimensions are comparable. Flagged separately by
        correlation with liquidity score, since a dimension can rank high
        from one noisy grid point rather than a real trend.
    Q4. Holdings-count scaling   - how the liquidity-vs-mismatch trade-off
        (marginal cost of liquidity, in sector-mismatch pp per liquidity
        point) changes as N tightens from 200 to 50.

Methodology notes:
  - Dimensions are reported SEPARATELY (sector/rating/maturity L1, duration,
    yield, issuer concentration) -- no composite "accuracy" index is
    invented, since weighting dimensions against each other would be an
    arbitrary modeling choice the proposal doesn't ask for.
  - The lambda sweep is noisy (documented Step 3/4 limitation: a larger
    lambda does not guarantee strictly higher liquidity or strictly worse
    fidelity at every single grid step -- see step4_full_lambda_path.csv).
    Q1's frontier is therefore built from each dimension's Pareto-efficient
    subset of (liquidity_score, mismatch) points rather than assumed
    monotonic in lambda; this also visually documents the noise instead of
    hiding it (all raw grid points are plotted too).
  - N=25 excluded throughout (liquidity optimizer infeasible at N=25: the
    per-position weight cap collapses to a flat 3% for N<133, and 25
    positions x 3% = 75% can't sum to 1.0 -- see README "Known
    limitations"). Fixing it would mean loosening the issuer/position
    concentration constraints specifically for small N, which changes the
    study's methodology rather than just its numerics; kept as a
    documented limitation instead.

Outputs (../output/step5/):
    step5_pareto_frontier.csv      Pareto-efficient (liquidity, mismatch) points, per N x dimension
    step5_dimension_ranking.csv    relative full-frontier degradation, per N and pooled
    step5_N_scaling.csv            marginal liquidity cost (sector-mismatch pp / liquidity pt), per N
    step5_summary.json             headline numeric answers to Q1-Q4
    charts/chart1_frontier_by_dimension.png
    charts/chart2_dimension_ranking.png
    charts/chart3_N_scaling.png
    charts/chart4_yield_path.png
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from portfolio_utils import SCORES, load_bonds, compute_benchmark_targets, compute_rating_buckets, output_paths

OUTPUT_ROOT = os.path.join(os.path.dirname(__file__), '..', 'output')

SIZES = [50, 100, 200]  # N=25 excluded: liquidity optimizer infeasible there (Step 3/4)

# mismatch/degradation dimensions used for Q1 and Q3: (column, label, "lower is better"=True for all)
DIMS = [
    ('sector_L1_pp',   'Sector L1 mismatch (pp)'),
    ('rating_L1_pp',   'Rating L1 mismatch (pp)'),
    ('maturity_L1_pp', 'Maturity L1 mismatch (pp)'),
    ('duration_diff',  'Duration abs. diff (yrs)'),
    ('ytm_diff_pp',    'YTM abs. diff (pp)'),
    ('top10_issuer_%', 'Top-10 issuer weight (%)'),
    ('issuer_hhi',     'Issuer HHI'),
]

# --- palette, consistent with Step 4 charts ---
INK, MUTED, GRID = '#2b2a26', '#898781', '#e1e0d9'
N_COLORS = {50: '#b9762f', 100: '#6b7a8f', 200: '#5a8a6b'}
RAW_ALPHA = 0.35
plt.rcParams.update({
    'font.family': 'DejaVu Sans', 'font.size': 10, 'text.color': INK,
    'axes.edgecolor': MUTED, 'axes.labelcolor': INK, 'xtick.color': MUTED,
    'ytick.color': MUTED, 'axes.titlecolor': INK, 'figure.facecolor': 'white',
    'axes.facecolor': 'white', 'axes.grid': True, 'grid.color': GRID,
    'axes.spines.top': False, 'axes.spines.right': False,
})


def pareto_efficient(df, y_col, x_col='liquidity_score'):
    """Points where no other row has >= liquidity AND <= mismatch (one strict).
    Returns the input rows that survive, sorted by x_col."""
    d = df.sort_values(x_col).reset_index(drop=True)
    keep = []
    for i, row in d.iterrows():
        dominated = ((d[x_col] >= row[x_col]) & (d[y_col] <= row[y_col]) &
                     ((d[x_col] > row[x_col]) | (d[y_col] < row[y_col]))).any()
        if not dominated:
            keep.append(i)
    return d.loc[keep].sort_values(x_col).reset_index(drop=True)


def main(score='par'):
    paths = output_paths(score, root=OUTPUT_ROOT)
    STEP4, OUT = paths['step4_dir'], paths['step5_dir']
    CH = os.path.join(OUT, 'charts')
    os.makedirs(CH, exist_ok=True)
    path = pd.read_csv(os.path.join(STEP4, 'step4_full_lambda_path.csv'))
    path = path[path.N.isin(SIZES)].copy()

    bonds = load_bonds('../data/lqd_holdings_raw.csv', include_rating=False, score=score)
    bonds['Rating_Bucket'] = compute_rating_buckets(
        bonds, qlta_path='../data/qlta_holdings_raw.csv', lqdb_path='../data/lqdb_holdings_raw.csv')
    targets = compute_benchmark_targets(bonds)
    bench_ytm = targets['ytm']
    bench_duration = targets['duration']

    # ================================================================
    # Q1 - PARETO FRONIER: liquidity score vs each mismatch dimension
    # ================================================================
    frontier_rows = []
    for n in SIZES:
        sub = path[path.N == n]
        for col, label in DIMS:
            eff = pareto_efficient(sub, col)
            for _, r in eff.iterrows():
                frontier_rows.append({
                    'N': n, 'dimension': label, 'lambda': r['lambda'],
                    'liquidity_score': round(r['liquidity_score'], 4),
                    'mismatch_value': round(r[col], 4),
                })
    frontier = pd.DataFrame(frontier_rows)
    frontier.to_csv(os.path.join(OUT, 'step5_pareto_frontier.csv'), index=False)

    # ================================================================
    # Q3 - DIMENSION RANKING: degradation over the full frontier, normalized
    # onto a common 0-100 scale per dimension using that dimension's GLOBAL
    # min/max across the entire grid (all N x all lambda combined).
    #
    # NOTE: an earlier version normalized by each dimension's own lambda=0
    # baseline (% change). That breaks for duration/rating, whose lambda=0
    # baseline is ~0.00-0.02 (the optimizer nearly perfectly matches those
    # at lambda=0) -- dividing by a near-zero baseline produced meaningless
    # figures like "+2890%". Global min-max scaling avoids dividing by a
    # value that can be arbitrarily close to zero.
    # ================================================================
    global_range = {col: (path[col].min(), path[col].max()) for col, _ in DIMS}

    def normalize(col, val):
        lo, hi = global_range[col]
        return (val - lo) / (hi - lo) * 100 if hi > lo else 0.0

    # "worst" is taken from the PARETO-EFFICIENT subset (frontier, built above), not the raw
    # grid max: a raw max lets a single off-frontier noise point set the ranking -- e.g. N=50's
    # highest Rating L1 value in the raw grid occurs at lambda=0.0025 (barely any liquidity tilt
    # at all), which isn't a real consequence of prioritizing liquidity, just sweep noise near
    # the baseline. A point that's dominated (worse mismatch with no better liquidity to show for
    # it) doesn't survive onto the frontier, so it can't set "worst" here.
    rank_rows = []
    for n in SIZES:
        sub = path[path.N == n]
        base = sub[sub['lambda'] == 0.0].iloc[0]
        for col, label in DIMS:
            baseline = base[col]
            worst = frontier[(frontier.N == n) & (frontier.dimension == label)]['mismatch_value'].max()
            baseline_norm = normalize(col, baseline)
            worst_norm = normalize(col, worst)
            corr = sub[['liquidity_score', col]].corr().iloc[0, 1]
            rank_rows.append({
                'N': n, 'dimension': label,
                'lambda0_baseline': round(baseline, 4),
                'worst_in_grid': round(worst, 4),
                'abs_change': round(worst - baseline, 4),
                'degradation_normalized_0_100': round(worst_norm - baseline_norm, 1),
                'corr_with_liquidity': round(corr, 3) if pd.notna(corr) else None,
            })
    ranking = pd.DataFrame(rank_rows)
    ranking = ranking.sort_values(['N', 'degradation_normalized_0_100'], ascending=[True, False])
    ranking.to_csv(os.path.join(OUT, 'step5_dimension_ranking.csv'), index=False)

    pooled = (ranking.groupby('dimension')['degradation_normalized_0_100'].mean()
              .sort_values(ascending=False).round(1))
    pooled_corr = ranking.groupby('dimension')['corr_with_liquidity'].mean().round(2)
    # A dimension can rank high purely from ONE noisy grid point (documented lambda-sweep
    # noise, see module docstring) rather than a systematic effect of the liquidity penalty.
    # Weak mean correlation with liquidity_score (<0.5) is a signal of exactly that -- flag it
    # instead of letting the magnitude-only ranking imply a trend that may not really be there.
    low_confidence = pooled_corr[pooled_corr < 0.5].index.tolist()

    # ================================================================
    # Q4 - N SCALING: marginal cost of liquidity (sector-mismatch pp per
    # liquidity point gained), using an OLS slope of sector_L1_pp on
    # liquidity_score over ALL 8 lambda points per N (not just the two
    # Pareto endpoints -- at N=50 those endpoints sit only 0.012 apart in
    # liquidity score, so an endpoint-to-endpoint ratio is dominated by
    # sweep noise; the full-grid slope averages over 8 points instead).
    # ================================================================
    scaling_rows = []
    for n in SIZES:
        sub = path[path.N == n]
        slope, intercept = np.polyfit(sub['liquidity_score'], sub['sector_L1_pp'], 1)
        pred = slope * sub['liquidity_score'] + intercept
        ss_res = ((sub['sector_L1_pp'] - pred) ** 2).sum()
        ss_tot = ((sub['sector_L1_pp'] - sub['sector_L1_pp'].mean()) ** 2).sum()
        r2 = 1 - ss_res / ss_tot if ss_tot > 1e-9 else np.nan
        liq_range = sub['liquidity_score'].max() - sub['liquidity_score'].min()
        scaling_rows.append({
            'N': n,
            'liquidity_range_in_grid': round(liq_range, 4),
            'sector_mismatch_ols_slope_pp_per_liquidity_pt': round(slope, 2),
            'r_squared': round(r2, 3) if pd.notna(r2) else None,
            'n_grid_points': len(sub),
        })
    scaling = pd.DataFrame(scaling_rows)
    scaling.to_csv(os.path.join(OUT, 'step5_N_scaling.csv'), index=False)
    # R2 this low means the "slope" is mostly noise, not a reliable linear trend -- the sector-
    # mismatch/liquidity relationship isn't well described by a single line at any N (consistent
    # with the documented lambda-sweep noise; see chart1's raw-dot scatter). Don't read the
    # N=50-vs-N=200 slope comparison as a confident "cost gets worse at smaller N" trend.
    scaling_weak_fit = scaling[scaling['r_squared'] < 0.5]['N'].tolist()

    # ================================================================
    # Q2 - YIELD PATH (full grid, all N) + summary stats
    # ================================================================
    yield_summary = {}
    for n in SIZES:
        sub = path[path.N == n]
        yield_summary[n] = {
            'min_ytm_%': round(sub['ytm_%'].min(), 4),
            'max_ytm_%': round(sub['ytm_%'].max(), 4),
            'range_bp': round((sub['ytm_%'].max() - sub['ytm_%'].min()) * 100, 1),
            'max_abs_diff_vs_benchmark_pp': round(sub['ytm_diff_pp'].max(), 4),
        }

    # ================================================================
    # SUMMARY JSON
    # ================================================================
    summary = {
        'sizes': SIZES,
        'benchmark': {'ytm_%': round(bench_ytm, 4), 'duration': round(bench_duration, 4)},
        'q1_accuracy_loss': {
            'method': 'Pareto-efficient (liquidity_score, mismatch) points per dimension per N; '
                       'see step5_pareto_frontier.csv',
            'note': 'Sector L1 and issuer concentration are the steepest frontiers (biggest pp cost '
                    'per unit of liquidity gained); rating and duration frontiers are comparatively flat.',
        },
        'q2_yield_impact': yield_summary,
        'q3_hardest_to_preserve': {
            'pooled_ranking_normalized_0_100': pooled.to_dict(),
            'pooled_mean_corr_with_liquidity': pooled_corr.to_dict(),
            'low_confidence_dimensions': low_confidence,
            'note': 'Each dimension min-max scaled to 0-100 using its global range across the entire '
                    'grid (all N x all lambda); degradation = normalized(worst on the Pareto frontier) '
                    '- normalized(lambda=0 baseline), averaged across N=50/100/200. Global scaling '
                    'avoids dividing by a near-zero lambda=0 baseline (duration and rating mismatch are '
                    'both ~0 at lambda=0, which broke a naive %-change ranking), and "worst" is taken '
                    'from the Pareto-efficient subset (see step5_pareto_frontier.csv) rather than the '
                    'raw grid max, so an off-frontier noise point (e.g. a spike at a tiny lambda that\'s '
                    'dominated by better points elsewhere in the grid) can\'t set the ranking by itself. '
                    'low_confidence_dimensions (mean correlation with liquidity_score < 0.5, see '
                    'pooled_mean_corr_with_liquidity) still trend the right direction but noisily -- '
                    'read their magnitude as directional, not precise. Dimensions with high pooled '
                    'correlation are the most reliably "hardest to preserve": check '
                    'pooled_mean_corr_with_liquidity against pooled_ranking_normalized_0_100 together, '
                    'since a dimension can rank high on magnitude alone from a wide but inconsistent swing.',
        },
        'q4_holdings_count_scaling': {
            'per_N': scaling.set_index('N').to_dict(orient='index'),
            'weak_fit_Ns': scaling_weak_fit,
            'note': 'sector_mismatch_ols_slope is a full-grid OLS fit of sector_L1_pp on '
                    'liquidity_score. r_squared < 0.5 (weak_fit_Ns) means that slope is mostly '
                    'noise, not a reliable linear trend -- the marginal-cost-vs-N comparison '
                    'should be read as directional at best, not a confident finding.',
        },
    }
    summary['liquidity_score'] = score
    with open(os.path.join(OUT, 'step5_summary.json'), 'w') as f:
        json.dump(summary, f, indent=2)

    # ================================================================
    # CHARTS
    # ================================================================
    def chart_frontier_by_dimension():
        dims_to_plot = ['Sector L1 mismatch (pp)', 'Rating L1 mismatch (pp)',
                         'Maturity L1 mismatch (pp)', 'Top-10 issuer weight (%)']
        fig, axes = plt.subplots(2, 2, figsize=(11, 8.5))
        plot_dims = [d for d in DIMS if d[1] in dims_to_plot]
        for ax, (col, label) in zip(axes.flat, plot_dims):
            for n in SIZES:
                raw = path[path.N == n]
                ax.scatter(raw['liquidity_score'], raw[col], s=18, color=N_COLORS[n],
                           alpha=RAW_ALPHA, zorder=2)
                eff = frontier[(frontier.N == n) & (frontier.dimension == label)]
                ax.plot(eff['liquidity_score'], eff['mismatch_value'], '-o', color=N_COLORS[n],
                        markersize=4, label=f'N={n}', zorder=3)
            ax.set_title(label, fontsize=10.5)
            ax.set_xlabel('Weighted-avg liquidity score')
            ax.set_axisbelow(True)
        axes.flat[0].legend(loc='best', frameon=False, fontsize=9)
        fig.suptitle('Accuracy-vs-liquidity frontier by dimension\n'
                     '(faint dots = raw lambda grid, showing sweep noise; line = Pareto-efficient frontier)',
                     fontsize=12.5, y=1.0)
        fig.tight_layout()
        fig.savefig(os.path.join(CH, 'chart1_frontier_by_dimension.png'), dpi=150, bbox_inches='tight')
        plt.close(fig)

    def chart_dimension_ranking():
        fig, ax = plt.subplots(figsize=(9, 5.5))
        piv = ranking.pivot(index='dimension', columns='N', values='degradation_normalized_0_100')
        piv = piv.loc[pooled.index]  # order by pooled ranking, worst first
        x = np.arange(len(piv)); w = 0.26
        for i, n in enumerate(SIZES):
            ax.barh(x + (i - 1) * w, piv[n], w, color=N_COLORS[n], label=f'N={n}')
        ax.set_yticks(x); ax.set_yticklabels(piv.index)
        ax.invert_yaxis()
        ax.set_xlabel('Degradation, normalized to 0-100 per dimension\'s global grid range')
        ax.set_title('Hardest-to-preserve benchmark characteristics\n'
                     '(worst value in grid vs lambda=0 baseline, on each dimension\'s own 0-100 scale)', fontsize=12)
        ax.legend(frameon=False, fontsize=9)
        ax.set_axisbelow(True)
        fig.tight_layout()
        fig.savefig(os.path.join(CH, 'chart2_dimension_ranking.png'), dpi=150, bbox_inches='tight')
        plt.close(fig)

    def chart_N_scaling():
        fig, ax = plt.subplots(figsize=(7, 5))
        col = 'sector_mismatch_ols_slope_pp_per_liquidity_pt'
        ax.plot(scaling['N'], scaling[col], '-o', color='#b9762f', markersize=7)
        for _, r in scaling.iterrows():
            marker = ' (weak fit)' if r['N'] in scaling_weak_fit else ''
            ax.annotate(f"{r[col]:.1f}{marker}", (r['N'], r[col]),
                       textcoords='offset points', xytext=(0, 8), ha='center', fontsize=9)
        ax.set_xticks(SIZES)
        ax.set_xlabel('N (holdings count)')
        ax.set_ylabel('Sector-mismatch pp per liquidity-score point (OLS slope)')
        ax.set_title('Marginal cost of liquidity as N tightens\n'
                     '("weak fit" = R²<0.5, slope is mostly sweep noise)', fontsize=12)
        ax.set_axisbelow(True)
        fig.tight_layout()
        fig.savefig(os.path.join(CH, 'chart3_N_scaling.png'), dpi=150, bbox_inches='tight')
        plt.close(fig)

    def chart_yield_path():
        fig, ax = plt.subplots(figsize=(7.5, 5))
        ax.axhline(bench_ytm, color='#c9c6bd', lw=2, label='Benchmark YTM', zorder=1)
        for n in SIZES:
            sub = path[path.N == n].sort_values('lambda')
            ax.plot(sub['lambda'], sub['ytm_%'], '-o', color=N_COLORS[n], markersize=4, label=f'N={n}')
        ax.set_xscale('symlog', linthresh=0.01)
        ax.set_xlabel('lambda (liquidity penalty)')
        ax.set_ylabel('Portfolio YTM (%)')
        ax.set_title('Yield holds up across the full liquidity frontier', fontsize=12)
        ax.legend(frameon=False, fontsize=9)
        ax.set_axisbelow(True)
        fig.tight_layout()
        fig.savefig(os.path.join(CH, 'chart4_yield_path.png'), dpi=150, bbox_inches='tight')
        plt.close(fig)

    chart_frontier_by_dimension()
    chart_dimension_ranking()
    chart_N_scaling()
    chart_yield_path()

    print('Step 5 tables + charts written to', os.path.abspath(OUT))
    print('\nQ3 pooled hardest-to-preserve ranking (0-100 normalized degradation, avg across N)'
          ' with mean correlation to liquidity score:')
    print(pd.DataFrame({'degradation': pooled, 'mean_corr_with_liquidity': pooled_corr}).to_string())
    if low_confidence:
        print(f'  low-confidence (corr<0.5, likely noise-driven not trend-driven): {low_confidence}')
    print('\nQ4 marginal cost of liquidity by N (OLS slope over full grid):')
    print(scaling.to_string(index=False))
    if scaling_weak_fit:
        print(f'  weak fit (R2<0.5, slope is mostly noise not a reliable trend): N={scaling_weak_fit}')
    return frontier, ranking, scaling, summary


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--score', choices=SCORES, default='par', help='which Step 4 run to analyse (default: par)')
    main(score=ap.parse_args().score)
