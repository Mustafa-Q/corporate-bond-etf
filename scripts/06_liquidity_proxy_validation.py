"""
Step 6: Does the par-value liquidity proxy measure tradability, or just issue size?

A fixed-income reviewer's critique of Steps 3-5: the liquidity score is normalised
log(par holding size), and an ETF's par holding is often just proportional to the
bond's amount outstanding. A large issue can sit on a bank balance sheet and rarely
trade, so "big" and "liquid" are not the same thing. This script tests that with
data instead of arguing about it. Run from inside scripts/ (paths are ../data, ../output).

The HEADLINE is the ACTIVITY read: Spearman rank correlation between LQD's par holding
and real FINRA TRACE trading activity per CUSIP -- distinct days traded, trade count and
(capped) volume -- over the window in data/trace_activity_raw.csv (WRDS TRACE Enhanced,
cleaned by 00d_parse_wrds_trace_enhanced.py; cleaning stats in step6_trace_cleaning.json).
The join is on cusip via data/lqd_cusip_crosswalk.csv.

Universe accounting (reported separately in step6_summary.json):
  3,143 LQD bonds  ->  3,062 with a CUSIP (crosswalk)  ->  2,756 that printed at least once.
Not every silent bond is illiquid. The holdings snapshot post-dates the TRACE window, so
the crosswalk contains bonds that were issued AFTER the window (they could not have
printed) and bonds issued INSIDE it (partial exposure). The iShares Effective Date sorts
each matched bond into full_window / issued_in_window / issued_after_window, and the
HEADLINE uses only bonds outstanding for the full window. Within that set a bond with no
prints IS maximally illiquid and enters every correlation at zero (a tie at the bottom);
a "traded-only" sensitivity row drops those ties, and a "naive" row shows what happens if
the post-window new issues are wrongly counted as zero-activity bonds. Bonds with no CUSIP
match are excluded and counted in the match report.

The SIZE-CONSISTENCY check (previously mislabelled as a proxy read) is the same bond's
position in QLTA or LQDB, a different BlackRock fund sizing the same issue. It says how
consistently two funds size a bond; it is NOT a liquidity validation and is kept only as
context.

The ISSUE-SIZE read (needs data/fisd_issue_size.csv from 00e_parse_fisd_issue_size.py) is the
direct test of "par holding ~ amount outstanding": Spearman of par vs true issue size (FISD
amount outstanding, else offering amount), and of issue size vs TRACE activity, so the three
quantities -- par, size, tradability -- can be read against each other. Skipped with a note
when the file is absent.

Timing caveat: TRACE Enhanced is embargoed ~6 months, so the activity window (Sep-Dec
2025) precedes the holdings snapshot (2026-07-22). Liquidity characteristics are persistent
over that horizon, but the two measures are not contemporaneous.

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
from pandas.tseries.holiday import USFederalHolidayCalendar
from pandas.tseries.offsets import CustomBusinessDay
from scipy.stats import spearmanr

from portfolio_utils import AS_OF_DATE, load_bonds
from trace_utils import ACTIVITY_COLUMNS

ACTIVITY_METRICS = ['n_trades', 'n_capped_trades', 'total_volume_floor', 'days_traded',
                    'n_customer_trades', 'n_dealer_trades', 'median_trade_size']
HEADLINE = [('days_traded', 'days traded'), ('n_trades', 'trade count'), ('total_volume_floor', 'volume (capped)')]

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


def trading_days_in_window(start, end):
    """US bond-market trading days: weekdays minus US federal holidays (SIFMA closes the
    bond market on Columbus Day and Veterans Day too, so the federal calendar fits)."""
    bday = CustomBusinessDay(calendar=USFederalHolidayCalendar())
    return int(len(pd.date_range(start, end, freq=bday)))


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
    """Returns (bond-level activity frame, match report, (window_start, window_end), note),
    or (None, None, None, note) when the inputs are absent. bonds needs bond_id and the
    iShares Effective Date (issue/settlement date).

    Matched bonds with no prints get zero for every metric the source provides; metrics
    the source does not carry at all (contra-party counts on a WRDS pull) stay NaN so they
    are skipped rather than correlated as fake zeros. window_exposure classifies each
    matched bond by whether it existed for the whole window (see module docstring)."""
    xw_path = os.path.join(data_dir, 'lqd_cusip_crosswalk.csv')
    act_path = os.path.join(data_dir, 'trace_activity_raw.csv')
    missing = [p for p in (xw_path, act_path) if not os.path.exists(p)]
    if missing:
        return None, None, None, f"TRACE inputs not present: {', '.join(os.path.basename(m) for m in missing)}"
    xw = pd.read_csv(xw_path, dtype={'cusip': str})
    act = pd.read_csv(act_path, dtype={'cusip': str})
    missing_cols = [c for c in ACTIVITY_COLUMNS if c not in act.columns]
    if missing_cols:
        raise ValueError(f'{act_path} lacks required columns {missing_cols}; see trace_utils.ACTIVITY_COLUMNS')
    window = (str(act['window_start'].iloc[0]), str(act['window_end'].iloc[0]))

    report = (xw['match_method'].value_counts().rename_axis('match_method')
              .reset_index(name='n_bonds'))
    report['share'] = report['n_bonds'] / len(bonds)

    merged = bonds[['bond_id', 'Effective Date']].merge(xw[['bond_id', 'cusip', 'match_method']], on='bond_id', how='left')
    merged = merged.merge(act.drop(columns=['window_start', 'window_end']), on='cusip', how='left')
    has_cusip = merged['cusip'].notna()
    provided = [c for c in ACTIVITY_METRICS if act[c].notna().any()]
    # matched but silent in the window -> zero activity, deliberately kept
    merged['zero_trade_bond'] = has_cusip & merged['n_trades'].isna()
    merged.loc[has_cusip, provided] = merged.loc[has_cusip, provided].fillna(0)
    eff = pd.to_datetime(merged['Effective Date'], errors='coerce')
    ws, we = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    exposure = pd.Series(np.where(eff > we, 'issued_after_window',
                                  np.where(eff >= ws, 'issued_in_window', 'full_window')), index=merged.index, dtype=object)
    merged['window_exposure'] = exposure.where(has_cusip, other=np.nan)
    merged = merged.drop(columns=['Effective Date'])
    full = merged['window_exposure'] == 'full_window'
    note = (f"{int(has_cusip.sum())}/{len(bonds)} bonds matched to a CUSIP: {int(full.sum())} outstanding for the full window, "
            f"{int((merged['window_exposure'] == 'issued_in_window').sum())} issued inside it, "
            f"{int((merged['window_exposure'] == 'issued_after_window').sum())} issued after the window (cannot have printed); "
            f"{int((merged['zero_trade_bond'] & full).sum())} matched bonds outstanding for the full window had zero prints "
            f"(kept at zero); {int((~has_cusip).sum())} unmatched bonds excluded from activity correlations")
    return merged, report, window, note


def load_issue_size(bonds, data_dir):
    """FISD issue size per bond, or None when data/fisd_issue_size.csv is absent. issue_size is
    amount outstanding when FISD has it (issue_size_basis='amount_outstanding'), else the
    offering amount. Bonds with no FISD row stay NaN (missing, not zero)."""
    path = os.path.join(data_dir, 'fisd_issue_size.csv')
    if not os.path.exists(path):
        return None
    f = pd.read_csv(path, dtype={'cusip': str})
    f['issue_size'] = f['amount_outstanding'].where(f['amount_outstanding'].notna(), f['offering_amt'])
    f['issue_size_basis'] = np.where(f['amount_outstanding'].notna(), 'amount_outstanding',
                                     np.where(f['offering_amt'].notna(), 'offering_amt', None))
    out = bonds[['bond_id']].merge(f[['bond_id', 'offering_amt', 'amount_outstanding', 'issue_size', 'issue_size_basis']],
                                   on='bond_id', how='left')
    return out


def spearman_table(df, pairs, group=''):
    rows = []
    for x, y, label in pairs:
        sub = df[[x, y]].dropna()
        if len(sub) < 10:
            rows.append({'group': group, 'comparison': label, 'x': x, 'y': y, 'n': len(sub),
                         'spearman_rho': np.nan, 'p_value': np.nan})
            continue
        rho, p = spearmanr(sub[x], sub[y])
        rows.append({'group': group, 'comparison': label, 'x': x, 'y': y, 'n': int(len(sub)),
                     'spearman_rho': round(float(rho), 4), 'p_value': float(p)})
    return pd.DataFrame(rows)


def rank_scatter(ax, x, y, xlabel, ylabel, rho, n, n_zero=0):
    rx, ry = x.rank(pct=True), y.rank(pct=True)
    ax.scatter(rx, ry, s=14, color=POINT, alpha=0.35, edgecolor='none', zorder=2)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_axisbelow(True)
    txt = f'Spearman ρ = {rho:.2f}\nn = {n:,}'
    if n_zero:
        txt += f'\n{n_zero:,} zero-trade bonds tied at the bottom'
    ax.text(0.03, 0.95, txt, transform=ax.transAxes, va='top', ha='left', fontsize=9.5, color=INK,
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

    trace, match_report, window, trace_note = load_trace(bonds, args.data_dir)
    if trace is not None:
        df = df.merge(trace, on='bond_id', how='left')
    print(trace_note)
    fisd = load_issue_size(bonds, args.data_dir)
    if fisd is not None:
        df = df.merge(fisd, on='bond_id', how='left')
        print(f"FISD issue size for {int(df['issue_size'].notna().sum())}/{len(df)} bonds "
              f"({int((df['issue_size_basis'] == 'amount_outstanding').sum())} with amount outstanding)")
    else:
        print('FISD issue size not present (data/fisd_issue_size.csv); issue-size read skipped')

    # ---- correlations -------------------------------------------------------------
    corr_parts = []
    if trace is not None:
        matched = df[df['cusip'].notna()]
        act = matched[matched['window_exposure'] == 'full_window']       # the headline universe
        traded = act[~act['zero_trade_bond']]
        headline = [('Par Value', col, f'par size vs {name}') for col, name in HEADLINE]
        if act['n_customer_trades'].notna().any():
            headline.append(('Par Value', 'n_customer_trades', 'par size vs customer trade count'))
        corr_parts.append(spearman_table(act, headline, group='headline: par holding vs TRACE activity, bonds outstanding for the full window'))
        corr_parts.append(spearman_table(traded, [('Par Value', col, f'par size vs {name}, traded bonds only')
                                                  for col, name in HEADLINE],
                                         group='sensitivity: full-window bonds with zero prints excluded'))
        corr_parts.append(spearman_table(matched, [('Par Value', col, f'par size vs {name}, all matched incl. new issues at zero')
                                                   for col, name in HEADLINE],
                                         group='naive: every matched bond, post-window new issues counted as zero activity'))
        corr_parts.append(spearman_table(act, [('Liquidity_Score', 'days_traded', 'current liquidity score vs days traded'),
                                               ('Liquidity_Score', 'n_trades', 'current liquidity score vs trade count')],
                                         group='current optimiser score vs activity'))
        corr_parts.append(spearman_table(act, [('xfund_par_share', col, f'cross-fund par share vs {name}')
                                               for col, name in HEADLINE],
                                         group='cross-fund size vs activity'))
    if fisd is not None:
        corr_parts.append(spearman_table(df, [('Par Value', 'issue_size', 'par size vs FISD issue size'),
                                              ('Par Value', 'offering_amt', 'par size vs FISD offering amount')],
                                         group='issue size (FISD): the direct size read'))
        if trace is not None:
            corr_parts.append(spearman_table(act, [('issue_size', col, f'FISD issue size vs {name}') for col, name in HEADLINE],
                                             group='issue size vs activity, bonds outstanding for the full window'))
    corr_parts.append(spearman_table(df, [('Par Value', 'xfund_par_share', 'par size vs same bond par share in QLTA/LQDB')],
                                     group='size consistency (not a liquidity read)'))
    # the size check fund by fund, so a QLTA-vs-LQDB scale difference can't masquerade as noise
    corr_parts += [spearman_table(df[df['xfund_source'] == fund],
                                  [('Par Value', 'xfund_par', f'par size vs same bond par, {fund} only')],
                                  group='size consistency (not a liquidity read)')
                   for fund in ('QLTA', 'LQDB')]
    corr = pd.concat(corr_parts, ignore_index=True)
    corr.to_csv(os.path.join(args.out_dir, 'step6_proxy_correlations.csv'), index=False)
    print(corr.to_string(index=False))

    def rho_of(label, group_prefix=''):
        r = corr[(corr['comparison'] == label) & corr['group'].str.startswith(group_prefix)]
        return None if r.empty or pd.isna(r['spearman_rho'].iloc[0]) else float(r['spearman_rho'].iloc[0])

    # ---- summary & verdict --------------------------------------------------------
    size_rho = rho_of('par size vs same bond par share in QLTA/LQDB')
    summary = {
        'holdings_as_of': AS_OF_DATE.strftime('%Y-%m-%d'),
        'trace_status': 'available' if trace is not None else 'missing',
        'trace_note': trace_note,
    }
    if trace is not None:
        n_days = trading_days_in_window(*window)
        top_decile = act[act['Par Value'] >= act['Par Value'].quantile(0.9)]
        bottom_decile = act[act['Par Value'] <= act['Par Value'].quantile(0.1)]
        parked = top_decile['days_traded'] < 0.5 * n_days
        rho_days, rho_trades, rho_vol = (rho_of(f'par size vs {name}', 'headline') for _, name in HEADLINE)
        # verdict keyed to the primary measures (days traded, trade count: robust to the volume
        # artefacts); volume is reported alongside as the secondary measure
        primary = max(v for v in (rho_days, rho_trades) if v is not None)
        exposure_counts = matched['window_exposure'].value_counts().to_dict()
        summary.update({
            'activity_window': {'start': window[0], 'end': window[1], 'trading_days': n_days,
                                'source': 'WRDS TRACE Enhanced; cleaning in step6_trace_cleaning.json'},
            'universe': {
                'n_bonds_lqd': int(len(df)),
                'n_bonds_with_cusip': int(len(matched)),
                'n_bonds_traded_in_window': int((~matched['zero_trade_bond']).sum()),
                'n_matched_zero_prints_any_reason': int(matched['zero_trade_bond'].sum()),
                'n_unmatched_bonds': int(df['cusip'].isna().sum()),
                'by_window_exposure': {k: int(exposure_counts.get(k, 0)) for k in
                                       ('full_window', 'issued_in_window', 'issued_after_window')},
                'headline_universe': 'full_window',
                'n_headline': int(len(act)),
                'n_headline_zero_trade': int(act['zero_trade_bond'].sum()),
                'note': ('A matched bond with no prints is only "maximally illiquid" if it existed for the '
                         'whole window; bonds issued after the window are excluded from the headline, '
                         'bonds issued inside it had partial exposure and are excluded too.'),
            },
            'headline': {
                'rho_par_vs_days_traded': rho_days,
                'rho_par_vs_n_trades': rho_trades,
                'rho_par_vs_volume_capped': rho_vol,
                'n': int(len(act)),
                'note': ('Spearman over CUSIP-matched bonds outstanding for the full window; the zero-print '
                         'bonds among them enter at zero (tied at the bottom)'),
            },
            'sensitivity_traded_only': {
                'rho_par_vs_days_traded': rho_of('par size vs days traded, traded bonds only'),
                'rho_par_vs_n_trades': rho_of('par size vs trade count, traded bonds only'),
                'rho_par_vs_volume_capped': rho_of('par size vs volume (capped), traded bonds only'),
                'n': int(len(traded)),
            },
            'sensitivity_naive_all_matched': {
                'rho_par_vs_days_traded': rho_of('par size vs days traded, all matched incl. new issues at zero'),
                'rho_par_vs_n_trades': rho_of('par size vs trade count, all matched incl. new issues at zero'),
                'rho_par_vs_volume_capped': rho_of('par size vs volume (capped), all matched incl. new issues at zero'),
                'n': int(len(matched)),
                'note': 'what the headline would read if post-window new issues were wrongly treated as zero-activity bonds',
            },
            'current_liquidity_score': {
                'rho_vs_days_traded': rho_of('current liquidity score vs days traded'),
                'rho_vs_n_trades': rho_of('current liquidity score vs trade count'),
            },
            'parked_issue_check': {
                'top_par_decile_share_trading_under_half_of_days': round(float(parked.mean()), 4),
                'median_days_traded_top_par_decile': float(top_decile['days_traded'].median()),
                'median_days_traded_bottom_par_decile': float(bottom_decile['days_traded'].median()),
                'median_trades_top_par_decile': float(top_decile['n_trades'].median()),
                'median_trades_bottom_par_decile': float(bottom_decile['n_trades'].median()),
            },
        })
    summary['size_consistency_check'] = {
        'rho_par_vs_cross_fund_par_share': size_rho,
        'rho_qlta_only': rho_of('par size vs same bond par, QLTA only'),
        'rho_lqdb_only': rho_of('par size vs same bond par, LQDB only'),
        'n': int(df['xfund_par'].notna().sum()),
        'note': ('LQD par vs the same bond\'s par share in QLTA/LQDB. A size-vs-size cross-fund '
                 'consistency check, NOT a liquidity or tradability read.'),
    }
    if fisd is not None:
        summary['issue_size_check'] = {
            'rho_par_vs_issue_size': rho_of('par size vs FISD issue size'),
            'rho_par_vs_offering_amt': rho_of('par size vs FISD offering amount'),
            'rho_issue_size_vs_days_traded': rho_of('FISD issue size vs days traded'),
            'rho_issue_size_vs_n_trades': rho_of('FISD issue size vs trade count'),
            'rho_issue_size_vs_volume_capped': rho_of('FISD issue size vs volume (capped)'),
            'n_bonds_with_issue_size': int(df['issue_size'].notna().sum()),
            'n_with_amount_outstanding': int((df['issue_size_basis'] == 'amount_outstanding').sum()),
            'note': 'FISD amount outstanding where known, else offering amount. The direct test of par holding ~ issue size.',
        }
    else:
        summary['issue_size_check'] = ('not available: data/fisd_issue_size.csv absent (see 00e_parse_fisd_issue_size.py). '
                                       'The N-PORT balance is the ETF holding, not issuance.')
    summary['limitations'] = [
        f'Activity window ends {window[1] if window else "n/a"}, ~7 months before the {summary["holdings_as_of"]} '
        'holdings snapshot (TRACE Enhanced embargo); liquidity is persistent over that horizon but the '
        'measures are not contemporaneous, and bonds issued after the window drop out of the headline.',
        'Cleaning: trc_st T kept; X cancels and R reversals remove one attribute-matched original each; '
        'C corrections and Y dropped with the original T kept (no msg_seq_nb in the pull). Volume winsorised '
        'per print at $100MM. See step6_trace_cleaning.json.',
        'days_traded saturates: most matched bonds print on nearly every trading day, so trade count '
        'discriminates better at the liquid end.',
        'No contra-party field in the WRDS pull, so customer vs dealer counts are unavailable.',
    ]
    if trace is not None:
        if primary < 0.3:
            strength, reading = 'weak', 'the reviewer\'s critique holds: par holding size measures issue size, not tradability'
        elif primary < 0.6:
            strength, reading = 'moderate', ('the reviewer\'s critique holds in substance: par holding size is a partial, '
                                             'noisy read of tradability')
        else:
            strength, reading = 'strong', 'par holding size is a reasonable activity proxy in this universe; the critique is weaker than expected'
        verdict = (f'Par holding size vs TRACE trading activity: Spearman ρ = {rho_days:.2f} (days traded), '
                   f'{rho_trades:.2f} (trade count) over {len(act):,} CUSIP-matched bonds outstanding for the full window '
                   f'({int(act["zero_trade_bond"].sum())} with zero prints, kept at zero); capped volume ρ = {rho_vol:.2f}. '
                   f'The association is {strength}, so {reading}. '
                   f'Among the top par decile, {parked.mean():.0%} of bonds traded on fewer than half of the {n_days} trading days. '
                   f'For context, LQD par agrees with another fund\'s sizing of the same bond at ρ = {size_rho:.2f} '
                   '(size consistency, not a liquidity read).')
        if fisd is not None and summary['issue_size_check']['rho_par_vs_issue_size'] is not None:
            isz = summary['issue_size_check']
            act_rho = isz['rho_issue_size_vs_n_trades']
            verdict += (f' Against true issue size (FISD, n={isz["n_bonds_with_issue_size"]:,}), par holding has '
                        f'ρ = {isz["rho_par_vs_issue_size"]:.2f}' +
                        (f' while issue size itself has ρ = {act_rho:.2f} with trade count.' if act_rho is not None else '.'))
    else:
        verdict = ('TRACE activity data not present; only the size-consistency check was computed '
                   f'(ρ={size_rho}), which is not a liquidity validation.')
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
                 'Same bond, share of QLTA/LQDB par (percentile rank)', size_rho or float('nan'), len(sub))
    ax.set_title('Size-consistency check: two funds\' positions in the same bond', fontsize=11)
    fig.savefig(os.path.join(charts, 'chart0_par_vs_cross_fund_par.png'), dpi=150, bbox_inches='tight')
    plt.close(fig)

    if trace is not None:
        n_zero = int(act['zero_trade_bond'].sum())
        fig, axes = plt.subplots(1, 3, figsize=(15, 4.8))
        for ax, (col, label, key) in zip(axes, [
                ('days_traded', 'Days traded in window (percentile rank)', 'rho_par_vs_days_traded'),
                ('n_trades', 'Trade count in window (percentile rank)', 'rho_par_vs_n_trades'),
                ('total_volume_floor', 'Volume in window, capped (percentile rank)', 'rho_par_vs_volume_capped')]):
            rank_scatter(ax, act['Par Value'], act[col], 'LQD par holding (percentile rank)', label,
                         summary['headline'][key] or float('nan'), len(act), n_zero)
        fig.suptitle('Headline: par holding size vs TRACE trading activity, bonds outstanding for the full window '
                     f'({window[0]} to {window[1]}, {n_days} trading days)', fontsize=12)
        fig.savefig(os.path.join(charts, 'chart1_par_vs_trace_activity.png'), dpi=150, bbox_inches='tight')
        plt.close(fig)

        # activity vs size side by side: the reviewer's comparison in one picture
        fig, ax = plt.subplots(figsize=(7, 4.2))
        labels = ['Days traded', 'Trade count', 'Volume (capped)', 'Same bond in\nQLTA/LQDB (size)']
        vals = [rho_days, rho_trades, rho_vol, size_rho]
        colors = [POINT] * 3 + [ACCENT]
        if fisd is not None and summary['issue_size_check']['rho_par_vs_issue_size'] is not None:
            labels.append('FISD issue size\n(amount outstanding)')
            vals.append(summary['issue_size_check']['rho_par_vs_issue_size'])
            colors.append(ACCENT)
        bars = ax.barh(labels[::-1], [v or 0 for v in vals][::-1], color=colors[::-1], height=0.55)
        for b, v in zip(bars, [v or 0 for v in vals][::-1]):
            ax.text(b.get_width() + 0.01, b.get_y() + b.get_height() / 2, f'{v:.2f}', va='center', fontsize=9.5)
        ax.set_xlim(0, 1)
        ax.tick_params(axis='y', colors=INK)
        ax.set_xlabel('Spearman ρ with LQD par holding size')
        ax.set_title('What does par holding size actually track? (TRACE activity vs a size check)', fontsize=11)
        ax.grid(axis='y', visible=False)
        fig.savefig(os.path.join(charts, 'chart2_size_vs_activity_rho.png'), dpi=150, bbox_inches='tight')
        plt.close(fig)

    print(f'\nWrote {args.out_dir}')


if __name__ == '__main__':
    main()
