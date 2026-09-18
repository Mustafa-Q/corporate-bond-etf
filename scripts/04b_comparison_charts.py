"""
Step 4 - Comparison charts
Renders the head-to-head visuals for the with- vs without-liquidity portfolios.
Saves PNGs to ../output/step4/charts/.
"""
import argparse
import os, sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

from portfolio_utils import SCORES, output_paths

OUTPUT_ROOT = os.path.join(os.path.dirname(__file__), '..', 'output')

# --- palette (muted, consistent with the methodology doc) ---
INK      = '#2b2a26'
MUTED    = '#898781'
GRID     = '#e1e0d9'
NOLIQ    = '#6b7a8f'   # slate blue  = without liquidity
WITHLIQ  = '#b9762f'   # warm ochre  = with liquidity
BENCH    = '#c9c6bd'   # light putty = benchmark reference
plt.rcParams.update({
    'font.family': 'DejaVu Sans', 'font.size': 10, 'text.color': INK,
    'axes.edgecolor': MUTED, 'axes.labelcolor': INK, 'xtick.color': MUTED,
    'ytick.color': MUTED, 'axes.titlecolor': INK, 'figure.facecolor': 'white',
    'axes.facecolor': 'white', 'axes.grid': True, 'grid.color': GRID,
    'axes.spines.top': False, 'axes.spines.right': False,
})

# The chart functions read these module-level frames; main() fills them for the chosen score.
OUT = CH = None
head = sector = rating = maturity = path = None


def load_inputs(score):
    global OUT, CH, head, sector, rating, maturity, path
    OUT = output_paths(score, root=OUTPUT_ROOT)['step4_dir']
    CH = os.path.join(OUT, 'charts')
    os.makedirs(CH, exist_ok=True)
    head = pd.read_csv(os.path.join(OUT, 'step4_headline_comparison.csv'))
    sector = pd.read_csv(os.path.join(OUT, 'step4_sector_exposure.csv'))
    rating = pd.read_csv(os.path.join(OUT, 'step4_rating_exposure.csv'))
    maturity = pd.read_csv(os.path.join(OUT, 'step4_maturity_exposure.csv'))
    path = pd.read_csv(os.path.join(OUT, 'step4_full_lambda_path.csv'))


# ============================================================
# CHART 1 - Mismatch dimensions, no-liq vs with-liq, grouped by N
# ============================================================
def chart_mismatch():
    dims = ['Sector L1 mismatch (pp)', 'Rating L1 mismatch (pp)',
            'Maturity L1 mismatch (pp)', 'Duration abs. diff vs benchmark (yrs)',
            'YTM abs. diff vs benchmark (pp)']
    short = ['Sector', 'Rating', 'Maturity', 'Duration', 'Yield']
    Ns = [50, 100, 200]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2), sharey=False)
    for ax, n in zip(axes, Ns):
        sub = head[head.N == n].set_index('dimension')
        no = [sub.loc[d, 'without_liquidity (lambda=0.0)'] for d in dims]
        wi = [sub.loc[d, 'with_liquidity (lambda=1.0)'] for d in dims]
        x = np.arange(len(dims)); w = 0.38
        ax.bar(x - w/2, no, w, label='Without liquidity (\u03bb=0)', color=NOLIQ)
        ax.bar(x + w/2, wi, w, label='With liquidity (\u03bb=1)', color=WITHLIQ)
        ax.set_title(f'N = {n} holdings', fontsize=11, pad=8)
        ax.set_xticks(x); ax.set_xticklabels(short, rotation=30, ha='right')
        ax.set_axisbelow(True)
        if n == 50:
            ax.set_ylabel('Deviation from benchmark\n(pp, or yrs for duration)')
    axes[0].legend(loc='upper left', frameon=False, fontsize=9)
    fig.suptitle('Characteristic mismatch rises when liquidity is prioritized',
                 fontsize=13, y=1.02, x=0.5, ha='center')
    fig.tight_layout()
    fig.savefig(os.path.join(CH, 'chart1_mismatch_by_N.png'), dpi=150, bbox_inches='tight')
    plt.close(fig)


# ============================================================
# CHART 2 - Sector exposure fingerprint (N=100)
# ============================================================
def chart_sector():
    df = sector.copy().head(12)  # top 12 sectors by weight
    y = np.arange(len(df))[::-1]
    fig, ax = plt.subplots(figsize=(9, 6.2))
    ax.barh(y + 0.0, df['benchmark_%'], 0.8, color=BENCH, label='Benchmark (LQD)', zorder=1)
    ax.scatter(df['no_liq_N100_%'], y, s=42, color=NOLIQ, label='Without liquidity (\u03bb=0)', zorder=3)
    ax.scatter(df['with_liq_N100_%'], y, s=42, color=WITHLIQ, marker='D', label='With liquidity (\u03bb=1)', zorder=3)
    ax.set_yticks(y); ax.set_yticklabels(df['category'])
    ax.set_xlabel('Portfolio weight (%)')
    ax.set_title('Sector exposure vs benchmark (N = 100)\nliquidity tilt pulls several sectors off benchmark',
                 fontsize=12, pad=10)
    ax.legend(loc='lower right', frameon=False, fontsize=9)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(os.path.join(CH, 'chart2_sector_exposure.png'), dpi=150, bbox_inches='tight')
    plt.close(fig)


# ============================================================
# CHART 3 - Rating & maturity exposure (N=100), side by side
# ============================================================
def chart_rating_maturity():
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.6))

    # rating
    r = rating.copy()
    x = np.arange(len(r)); w = 0.26
    ax1.bar(x - w, r['benchmark_%'], w, color=BENCH, label='Benchmark')
    ax1.bar(x,     r['no_liq_N100_%'], w, color=NOLIQ, label='Without liq (\u03bb=0)')
    ax1.bar(x + w, r['with_liq_N100_%'], w, color=WITHLIQ, label='With liq (\u03bb=1)')
    ax1.set_xticks(x); ax1.set_xticklabels(r['category'], rotation=15, ha='right')
    ax1.set_ylabel('Weight (%)'); ax1.set_title('Rating exposure (N=100)', fontsize=11)
    ax1.legend(frameon=False, fontsize=8.5)
    ax1.set_axisbelow(True)

    # maturity
    m = maturity.copy()
    x = np.arange(len(m)); w = 0.26
    ax2.bar(x - w, m['benchmark_%'], w, color=BENCH, label='Benchmark')
    ax2.bar(x,     m['no_liq_N100_%'], w, color=NOLIQ, label='Without liq (\u03bb=0)')
    ax2.bar(x + w, m['with_liq_N100_%'], w, color=WITHLIQ, label='With liq (\u03bb=1)')
    ax2.set_xticks(x); ax2.set_xticklabels(m['category'], rotation=45, ha='right')
    ax2.set_ylabel('Weight (%)'); ax2.set_title('Maturity exposure (N=100)', fontsize=11)
    ax2.set_axisbelow(True)

    fig.tight_layout()
    fig.savefig(os.path.join(CH, 'chart3_rating_maturity.png'), dpi=150, bbox_inches='tight')
    plt.close(fig)


# ============================================================
# CHART 4 - The trade-off: liquidity gained vs mismatch cost across lambda
# ============================================================
def chart_tradeoff():
    Ns = [50, 100, 200]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2), sharex=True)
    for ax, n in zip(axes, Ns):
        sub = path[path.N == n].sort_values('lambda')
        ax2 = ax.twinx()
        l1 = ax.plot(sub['lambda'], sub['liquidity_score'], '-o', color=WITHLIQ,
                     markersize=4, label='Liquidity score (L)')
        l2 = ax2.plot(sub['lambda'], sub['sector_L1_pp'], '--s', color=NOLIQ,
                      markersize=4, label='Sector mismatch (R)')
        ax.set_title(f'N = {n}', fontsize=11)
        ax.set_xscale('symlog', linthresh=0.002)
        ax.set_xlabel('\u03bb (liquidity penalty)')
        ax.grid(True, color=GRID)
        ax2.grid(False)
        if n == 50:
            ax.set_ylabel('Weighted-avg liquidity score', color=WITHLIQ)
        if n == 200:
            ax2.set_ylabel('Sector L1 mismatch (pp)', color=NOLIQ)
        ax.tick_params(axis='y', colors=WITHLIQ)
        ax2.tick_params(axis='y', colors=NOLIQ)
    fig.suptitle('Liquidity is bought cheaply at first, then mismatch climbs',
                 fontsize=13, y=1.02)
    fig.tight_layout()
    fig.savefig(os.path.join(CH, 'chart4_tradeoff_frontier.png'), dpi=150, bbox_inches='tight')
    plt.close(fig)


# ============================================================
# CHART 5 - Issuer concentration & yield summary
# ============================================================
def chart_concentration_yield():
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.4))
    Ns = [50, 100, 200]

    # HHI
    no_hhi = [head[(head.N == n) & (head.dimension == 'Issuer HHI')]['without_liquidity (lambda=0.0)'].iloc[0] for n in Ns]
    wi_hhi = [head[(head.N == n) & (head.dimension == 'Issuer HHI')]['with_liquidity (lambda=1.0)'].iloc[0] for n in Ns]
    x = np.arange(len(Ns)); w = 0.38
    ax1.bar(x - w/2, no_hhi, w, color=NOLIQ, label='Without liquidity')
    ax1.bar(x + w/2, wi_hhi, w, color=WITHLIQ, label='With liquidity')
    ax1.set_xticks(x); ax1.set_xticklabels([f'N={n}' for n in Ns])
    ax1.set_ylabel('Issuer HHI (higher = more concentrated)')
    ax1.set_title('Issuer concentration', fontsize=11)
    ax1.legend(frameon=False, fontsize=9); ax1.set_axisbelow(True)

    # Yield vs benchmark line
    bench_ytm = 5.6054
    no_y = [head[(head.N == n) & (head.dimension == 'Yield to maturity (%)')]['without_liquidity (lambda=0.0)'].iloc[0] for n in Ns]
    wi_y = [head[(head.N == n) & (head.dimension == 'Yield to maturity (%)')]['with_liquidity (lambda=1.0)'].iloc[0] for n in Ns]
    ax2.axhline(bench_ytm, color=BENCH, lw=2, label='Benchmark YTM')
    ax2.plot(x, no_y, '-o', color=NOLIQ, label='Without liquidity')
    ax2.plot(x, wi_y, '-D', color=WITHLIQ, label='With liquidity')
    ax2.set_xticks(x); ax2.set_xticklabels([f'N={n}' for n in Ns])
    ax2.set_ylabel('Yield to maturity (%)')
    ax2.set_title('Portfolio yield holds up', fontsize=11)
    ax2.legend(frameon=False, fontsize=9); ax2.set_axisbelow(True)

    fig.tight_layout()
    fig.savefig(os.path.join(CH, 'chart5_concentration_yield.png'), dpi=150, bbox_inches='tight')
    plt.close(fig)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--score', choices=SCORES, default='par', help='which Step 4 run to chart (default: par)')
    load_inputs(ap.parse_args().score)
    chart_mismatch()
    chart_sector()
    chart_rating_maturity()
    chart_tradeoff()
    chart_concentration_yield()
    print('Charts written to', os.path.abspath(CH))
    for f in sorted(os.listdir(CH)):
        print(' ', f)
