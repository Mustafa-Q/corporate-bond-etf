"""
Step 6: Does the par-value liquidity proxy measure tradability, or just issue size?

A fixed-income reviewer's critique of Steps 3-5: the liquidity score is normalised
log(par holding size), and an ETF's par holding is often just proportional to the
bond's amount outstanding. A large issue can sit on a bank balance sheet and rarely
trade, so "big" and "liquid" are not the same thing. This script tests that with
data instead of arguing about it. Run from inside scripts/ (paths are ../data, ../output).

Two independent reads, so the script always produces something:

  A. SIZE read (always available). The same bond's par holding in QLTA or LQDB --
     a different BlackRock fund sizing the same issue. If two funds' positions in a
     bond are far more rank-correlated with each other than either is with trading
     activity, the par proxy is tracking issue size. This is the stand-in for amount
     outstanding, which no free source provides (see the design doc).

  B. ACTIVITY read (needs TRACE data). Per-CUSIP trade count, distinct days traded,
     and (capped) volume over a trailing window, from data/trace_activity_raw.csv plus
     the LQD -> CUSIP crosswalk in data/lqd_cusip_crosswalk.csv. How those files are
     obtained is a licensing decision recorded in
     docs/superpowers/specs/2026-09-15-trace-liquidity-measure-design.md; the input
     schema is in trace_utils.ACTIVITY_COLUMNS.

Zero-trade bonds: a bond with a CUSIP but no prints in the window is NOT missing --
it is maximally illiquid -- and enters every correlation at zero. Bonds with no CUSIP
match are excluded from the activity correlations and counted in the match report.

Outputs (output/step6/): step6_proxy_correlations.csv, step6_match_report.csv,
step6_bond_level.csv, step6_summary.json, charts/.
"""
import argparse
import json
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from portfolio_utils import load_bonds
from trace_utils import ACTIVITY_COLUMNS

DEFAULT_WINDOW = ('2026-04-22', '2026-07-22')   # trailing ~3 months ending at the holdings date

# --- palette, consistent with Step 4/5 charts ---
INK, MUTED, GRID = '#2b2a26', '#898781', '#e1e0d9'
POINT, ACCENT = '#6b7a8f', '#b9762f'
plt.rcParams.update({
    'font.family': 'DejaVu Sans', 'font.size': 10, 'text.color': INK,
    'axes.edgecolor': MUTED, 'axes.labelcolor': INK, 'xtick.color': MUTED,
    'ytick.color': MUTED, 'axes.titlecolor': INK, 'figure.facecolor': 'white',
    'axes.facecolor': 'white', 'axes.grid': True, 'grid.color': GRID,
    'axes.spines.top': False, 'axes.spines.right': False,
})


def _ishares_match_key(df):
    """Same composite key portfolio_utils uses for the rating cross-match."""
    coupon = pd.to_numeric(df['Coupon (%)'].astype(str).str.replace(',', ''), errors='coerce')
    mat = pd.to_datetime(df['Maturity'], errors='coerce')
    return (df['Name'].str.strip().str.upper() + '|' + coupon.round(3).astype(str) + '|' +
            mat.dt.strftime('%Y-%m-%d'))


def load_cross_fund_par(bonds, data_dir):
    """The same bond's position in QLTA (Aaa-A) or LQDB (BBB) -- disjoint by design, so
    each LQD bond gets at most one value. Two columns: the raw par held (xfund_par) and
    the position as a share of that fund's par (xfund_par_share). The share is what the
    correlations use: QLTA and LQDB are different-sized funds, so raw par amounts are
    not rank-comparable across the two."""
    keys = _ishares_match_key(bonds)
    out = pd.DataFrame({'bond_id': bonds['bond_id'], 'xfund_par': np.nan, 'xfund_par_share': np.nan,
                        'xfund_source': pd.Series([None] * len(bonds), dtype=object)})
    for fund in ('qlta', 'lqdb'):
        path = os.path.join(data_dir, f'{fund}_holdings_raw.csv')
        if not os.path.exists(path):
            continue
        f = pd.read_csv(path)
        f = f[f['Asset Class'] == 'Fixed Income'].copy()
        f['key'] = _ishares_match_key(f)
        par = pd.to_numeric(f['Par Value'].astype(str).str.replace(',', ''), errors='coerce')
        lookup = pd.Series(par.values, index=f['key']).groupby(level=0).sum()
        hit = keys.map(lookup)
        fill = out['xfund_par'].isna() & hit.notna()
        out.loc[fill, 'xfund_par'] = hit[fill].values
        out.loc[fill, 'xfund_par_share'] = hit[fill].values / par.sum()
        out.loc[fill, 'xfund_source'] = fund.upper()
    return out


def load_trace(bonds, data_dir):
    """Returns (bond-level activity frame or None, match report frame or None, note)."""
    xw_path = os.path.join(data_dir, 'lqd_cusip_crosswalk.csv')
    act_path = os.path.join(data_dir, 'trace_activity_raw.csv')
    missing = [p for p in (xw_path, act_path) if not os.path.exists(p)]
    if missing:
        return None, None, f"TRACE inputs not present: {', '.join(os.path.basename(m) for m in missing)}"
    xw = pd.read_csv(xw_path, dtype={'cusip': str})
    act = pd.read_csv(act_path, dtype={'cusip': str})
    missing_cols = [c for c in ACTIVITY_COLUMNS if c not in act.columns]
    if missing_cols:
        raise ValueError(f'{act_path} lacks required columns {missing_cols}; see trace_utils.ACTIVITY_COLUMNS')

    report = (xw['match_method'].value_counts().rename_axis('match_method')
              .reset_index(name='n_bonds'))
    report['share'] = report['n_bonds'] / len(bonds)

    merged = bonds[['bond_id']].merge(xw, on='bond_id', how='left')
    merged = merged.merge(act.drop(columns=['window_start', 'window_end']), on='cusip', how='left')
    has_cusip = merged['cusip'].notna()
    metric_cols = ['n_trades', 'n_capped_trades', 'total_volume_floor', 'days_traded',
                   'n_customer_trades', 'n_dealer_trades']
    # matched but silent in the window -> zero activity, deliberately kept
    merged['zero_trade_bond'] = has_cusip & merged['n_trades'].isna()
    merged.loc[has_cusip, metric_cols] = merged.loc[has_cusip, metric_cols].fillna(0)
    note = (f"{int(has_cusip.sum())}/{len(bonds)} bonds matched to a CUSIP; "
            f"{int(merged['zero_trade_bond'].sum())} matched bonds had zero prints in the window "
            f"(kept at zero); {int((~has_cusip).sum())} unmatched bonds excluded from activity correlations")
    return merged, report, note


def spearman_table(df, pairs):
    rows = []
    for x, y, label in pairs:
        sub = df[[x, y]].dropna()
        if len(sub) < 10:
            rows.append({'comparison': label, 'x': x, 'y': y, 'n': len(sub), 'spearman_rho': np.nan, 'p_value': np.nan})
            continue
        rho, p = spearmanr(sub[x], sub[y])
        rows.append({'comparison': label, 'x': x, 'y': y, 'n': int(len(sub)),
                     'spearman_rho': round(float(rho), 4), 'p_value': float(p)})
    return pd.DataFrame(rows)


def rank_scatter(ax, x, y, xlabel, ylabel, rho, n):
    rx, ry = x.rank(pct=True), y.rank(pct=True)
    ax.scatter(rx, ry, s=14, color=POINT, alpha=0.35, edgecolor='none', zorder=2)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_axisbelow(True)
    ax.text(0.03, 0.95, f'Spearman ρ = {rho:.2f}\nn = {n:,}', transform=ax.transAxes,
            va='top', ha='left', fontsize=10, color=INK,
            bbox=dict(boxstyle='round,pad=0.35', facecolor='white', edgecolor=GRID))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data-dir', default='../data')
    ap.add_argument('--out-dir', default='../output/step6')
    args = ap.parse_args()
    charts = os.path.join(args.out_dir, 'charts')
    os.makedirs(charts, exist_ok=True)

    bonds = load_bonds(os.path.join(args.data_dir, 'lqd_holdings_raw.csv'), include_rating=False)
    df = bonds[['bond_id', 'Name', 'Sector', 'Coupon (%)', 'Maturity', 'Par Value',
                'Weight_Renorm', 'Liquidity_Score']].copy()
    df = df.merge(load_cross_fund_par(bonds, args.data_dir), on='bond_id')

    trace, match_report, trace_note = load_trace(bonds, args.data_dir)
    if trace is not None:
        df = df.merge(trace.drop(columns=['bond_id']).set_index(trace['bond_id']), left_on='bond_id', right_index=True, how='left')
    print(trace_note)

    # ---- correlations -------------------------------------------------------------
    pairs = [('Par Value', 'xfund_par_share', 'par size vs same bond par share in QLTA/LQDB (size read)')]
    if trace is not None:
        pairs += [
            ('Par Value', 'days_traded', 'par size vs days traded (activity)'),
            ('Par Value', 'n_trades', 'par size vs trade count (activity)'),
            ('Par Value', 'total_volume_floor', 'par size vs volume floor (activity)'),
            ('Par Value', 'n_customer_trades', 'par size vs customer trade count (activity)'),
            ('xfund_par_share', 'days_traded', 'size read vs days traded'),
            ('xfund_par_share', 'n_trades', 'size read vs trade count'),
            ('xfund_par_share', 'total_volume_floor', 'size read vs volume floor'),
            ('Liquidity_Score', 'days_traded', 'current liquidity score vs days traded'),
        ]
    corr = spearman_table(df, pairs)
    # the size read, fund by fund, so a QLTA-vs-LQDB scale difference can't masquerade as noise
    per_fund = pd.concat([spearman_table(df[df['xfund_source'] == fund],
                                         [('Par Value', 'xfund_par', f'par size vs same bond par, {fund} only')])
                          for fund in ('QLTA', 'LQDB')], ignore_index=True)
    corr = pd.concat([corr, per_fund], ignore_index=True)
    corr.to_csv(os.path.join(args.out_dir, 'step6_proxy_correlations.csv'), index=False)
    print(corr.to_string(index=False))

    # ---- headline numbers & verdict -----------------------------------------------
    def rho_of(label_prefix):
        r = corr[corr['comparison'].str.startswith(label_prefix)]
        return None if r.empty or pd.isna(r['spearman_rho'].iloc[0]) else float(r['spearman_rho'].iloc[0])

    summary = {
        'window': {'start': DEFAULT_WINDOW[0], 'end': DEFAULT_WINDOW[1]},
        'n_bonds_lqd': int(len(df)),
        'n_bonds_with_cross_fund_par': int(df['xfund_par'].notna().sum()),
        'rho_par_vs_size_read': rho_of('par size vs same bond par share'),
        'rho_par_vs_size_read_qlta_only': rho_of('par size vs same bond par, QLTA'),
        'rho_par_vs_size_read_lqdb_only': rho_of('par size vs same bond par, LQDB'),
        'trace_status': 'available' if trace is not None else 'missing',
        'trace_note': trace_note,
    }
    if trace is not None:
        act = df[df['cusip'].notna()]
        est_trading_days = int(act['days_traded'].max()) if len(act) else 0
        top_decile = act[act['Par Value'] >= act['Par Value'].quantile(0.9)]
        parked = top_decile['days_traded'] < 0.5 * est_trading_days
        rho_days, rho_trades, rho_vol = rho_of('par size vs days'), rho_of('par size vs trade count'), rho_of('par size vs volume')
        best_activity = max(v for v in (rho_days, rho_trades, rho_vol) if v is not None)
        size_rho = summary['rho_par_vs_size_read']
        summary.update({
            'trading_days_in_window_est': est_trading_days,
            'n_bonds_matched_cusip': int(len(act)),
            'n_zero_trade_bonds': int(act['zero_trade_bond'].sum()),
            'n_unmatched_bonds': int(df['cusip'].isna().sum()),
            'rho_par_vs_days_traded': rho_days,
            'rho_par_vs_n_trades': rho_trades,
            'rho_par_vs_volume_floor': rho_vol,
            'rho_size_read_vs_days_traded': rho_of('size read vs days'),
            'top_par_decile_share_trading_under_half_of_days': round(float(parked.mean()), 4) if len(top_decile) else None,
            'median_days_traded_top_par_decile': float(top_decile['days_traded'].median()) if len(top_decile) else None,
            'median_days_traded_bottom_par_decile': float(act[act['Par Value'] <= act['Par Value'].quantile(0.1)]['days_traded'].median()) if len(act) else None,
        })
        if size_rho is not None and size_rho - best_activity > 0.15:
            verdict = (f'Par size tracks issue size (ρ={size_rho:.2f} vs another fund\'s position in the same bond) '
                       f'much more closely than trading activity (best ρ={best_activity:.2f}). '
                       'The reviewer\'s critique holds: the proxy measures size, not tradability.')
        elif best_activity >= 0.6:
            verdict = (f'Par size is a reasonable activity proxy in this universe (best activity ρ={best_activity:.2f}); '
                       'the critique is weaker than expected here.')
        else:
            verdict = (f'Par size correlates only moderately with both size (ρ={size_rho}) and activity '
                       f'(best ρ={best_activity:.2f}); it is a noisy proxy for either.')
    else:
        verdict = ('TRACE activity data not yet available; only the size read was computed. '
                   'Par size vs the same bond\'s par in QLTA/LQDB: '
                   f"ρ={summary['rho_par_vs_size_read']} over {summary['n_bonds_with_cross_fund_par']} bonds.")
    summary['verdict'] = verdict
    with open(os.path.join(args.out_dir, 'step6_summary.json'), 'w') as f:
        json.dump(summary, f, indent=2)
    print('\nVERDICT:', verdict)

    # ---- tables -------------------------------------------------------------------
    if match_report is not None:
        match_report.to_csv(os.path.join(args.out_dir, 'step6_match_report.csv'), index=False)
    df.to_csv(os.path.join(args.out_dir, 'step6_bond_level.csv'), index=False)

    # ---- charts -------------------------------------------------------------------
    sub = df.dropna(subset=['xfund_par_share'])
    fig, ax = plt.subplots(figsize=(6, 5.2))
    rank_scatter(ax, sub['Par Value'], sub['xfund_par_share'], 'LQD par holding (percentile rank)',
                 'Same bond, share of QLTA/LQDB par (percentile rank)',
                 summary['rho_par_vs_size_read'] or float('nan'), len(sub))
    ax.set_title('Size read: two funds\' positions in the same bond', fontsize=11)
    fig.savefig(os.path.join(charts, 'chart0_par_vs_cross_fund_par.png'), dpi=150, bbox_inches='tight')
    plt.close(fig)

    if trace is not None:
        act = df[df['cusip'].notna()]
        fig, axes = plt.subplots(1, 3, figsize=(15, 4.8))
        for ax, (col, label, key) in zip(axes, [
                ('days_traded', 'Days traded in window (percentile rank)', 'rho_par_vs_days_traded'),
                ('n_trades', 'Trade count in window (percentile rank)', 'rho_par_vs_n_trades'),
                ('total_volume_floor', 'Volume floor in window (percentile rank)', 'rho_par_vs_volume_floor')]):
            rank_scatter(ax, act['Par Value'], act[col], 'LQD par holding (percentile rank)', label,
                         summary[key] or float('nan'), len(act))
        fig.suptitle('Activity read: par holding size vs TRACE trading activity '
                     f"({summary['window']['start']} to {summary['window']['end']})", fontsize=12)
        fig.savefig(os.path.join(charts, 'chart1_par_vs_trace_activity.png'), dpi=150, bbox_inches='tight')
        plt.close(fig)

        # size-vs-activity side by side: the reviewer's comparison in one picture
        fig, ax = plt.subplots(figsize=(7, 4.2))
        labels = ['Same bond in\nQLTA/LQDB (size)', 'Days traded', 'Trade count', 'Volume floor']
        vals = [summary['rho_par_vs_size_read'], summary['rho_par_vs_days_traded'],
                summary['rho_par_vs_n_trades'], summary['rho_par_vs_volume_floor']]
        colors = [ACCENT] + [POINT] * 3
        bars = ax.barh(labels[::-1], [v or 0 for v in vals][::-1], color=colors[::-1], height=0.55)
        for b, v in zip(bars, [v or 0 for v in vals][::-1]):
            ax.text(b.get_width() + 0.01, b.get_y() + b.get_height() / 2, f'{v:.2f}', va='center', fontsize=9.5)
        ax.set_xlim(0, 1)
        ax.tick_params(axis='y', colors=INK)
        ax.set_xlabel('Spearman ρ with LQD par holding size')
        ax.set_title('What does par holding size actually track?', fontsize=11)
        ax.grid(axis='y', visible=False)
        fig.savefig(os.path.join(charts, 'chart2_size_vs_activity_rho.png'), dpi=150, bbox_inches='tight')
        plt.close(fig)

    print(f'\nWrote {args.out_dir}')


if __name__ == '__main__':
    main()
