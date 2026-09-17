"""
Turn a WRDS TRACE Enhanced pull (trade-level, table trace_enhanced.trace_enhanced) into
data/trace_activity_raw.csv, the Step 6 input (schema in trace_utils.ACTIVITY_COLUMNS).

The WRDS query is filtered on cusip_id by data/lqd_cusips.txt (one CUSIP per line, built
from the N-PORT crosswalk) and returns 7 columns:
    cusip_id, trd_exctn_dt, bond_sym_id, company_symbol, trc_st, entrd_vol_qt, rptd_pr
The raw pull lives under data/raw/ (gitignored: WRDS data is licensed, not redistributable)
and is ~3.8M rows / 206 MB for a 3-month window; only the per-CUSIP aggregate is committed.

Cleaning (trace_utils.clean_wrds_enhanced): keep trc_st 'T'; each 'X' cancel / 'R' reversal
removes one attribute-identical original; 'C' corrections and 'Y' are dropped with the
original 'T' kept as the count. Volume is uncapped on WRDS but carries fat-finger entries
(10,000,000,000 par), so prints are winsorised at --volume-cap (default $100MM) and the
number of capped prints reported. The window is inferred from the data unless given.

Usage (from scripts/):
    python 00d_parse_wrds_trace_enhanced.py ../data/raw/wrds_trace_enhanced.csv \
        --crosswalk ../data/lqd_cusip_crosswalk.csv \
        --out ../data/trace_activity_raw.csv \
        --report ../output/step6/step6_trace_cleaning.json
"""
import argparse
import json
import sys

import pandas as pd

from trace_utils import ACTIVITY_COLUMNS, WRDS_COLUMNS, aggregate_trades, clean_wrds_enhanced

DEFAULT_VOLUME_CAP = 100_000_000.0   # per print, $ par; 99.99th pct of the Sep-Dec 2025 pull


def read_wrds(path):
    """cusip_id must stay a string (leading zeros, trailing check letters); the two numeric
    columns are parsed explicitly rather than left to inference."""
    return pd.read_csv(path, usecols=WRDS_COLUMNS, dtype={'cusip_id': str, 'trc_st': str, 'trd_exctn_dt': str})


def build_activity(path, crosswalk_cusips, window_start=None, window_end=None, volume_cap=DEFAULT_VOLUME_CAP):
    raw = read_wrds(path)
    n_raw = len(raw)
    raw['cusip_id'] = raw['cusip_id'].str.strip().str.upper()
    raw = raw[raw['cusip_id'].isin(crosswalk_cusips)]
    n_lqd = len(raw)
    prints, cleaning = clean_wrds_enhanced(raw)
    dates = pd.to_datetime(prints['tradeExecutionDate'], errors='coerce')
    ws = window_start or dates.min().strftime('%Y-%m-%d')
    we = window_end or dates.max().strftime('%Y-%m-%d')
    act = aggregate_trades(prints, ws, we, volume_cap=volume_cap).reindex(columns=ACTIVITY_COLUMNS)
    stats = {
        'source': 'WRDS TRACE Enhanced (trace_enhanced.trace_enhanced), filtered on cusip_id',
        'rows_read': int(n_raw),
        'rows_in_lqd_universe': int(n_lqd),
        'cleaning': cleaning,
        'window': {'start': ws, 'end': we},
        'volume_cap': volume_cap,
        'prints_at_or_above_cap': int((prints['reportedTradeVolume'] >= volume_cap).sum()) if volume_cap else 0,
        'cusips_with_activity': int(act['cusip'].nunique()),
        'cusips_in_crosswalk': int(len(crosswalk_cusips)),
        'distinct_trade_dates': int(dates.nunique()),
    }
    return act, stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('wrds_csv', help='trade-level WRDS TRACE Enhanced export (.csv)')
    ap.add_argument('--crosswalk', default='../data/lqd_cusip_crosswalk.csv')
    ap.add_argument('--window-start', default=None, help='YYYY-MM-DD; default: first execution date in the pull')
    ap.add_argument('--window-end', default=None, help='YYYY-MM-DD; default: last execution date in the pull')
    ap.add_argument('--volume-cap', type=float, default=DEFAULT_VOLUME_CAP, help='per-print winsorisation ceiling, $ par')
    ap.add_argument('--out', default='../data/trace_activity_raw.csv')
    ap.add_argument('--report', default='../output/step6/step6_trace_cleaning.json',
                    help='where to write the cleaning / coverage statistics (committed with the results)')
    args = ap.parse_args(argv)

    xw = pd.read_csv(args.crosswalk, dtype={'cusip': str})
    cusips = set(xw['cusip'].dropna().astype(str).str.strip().str.upper())
    act, stats = build_activity(args.wrds_csv, cusips, args.window_start, args.window_end, args.volume_cap)
    act.to_csv(args.out, index=False)
    with open(args.report, 'w') as f:
        json.dump(stats, f, indent=2)
    c = stats['cleaning']
    print(f"read {stats['rows_read']:,} rows; {stats['rows_in_lqd_universe']:,} in the LQD universe; "
          f"status counts {c['by_status']}")
    print(f"cleaning: {c['cancels_matched']:,}/{c['cancels_matched'] + c['cancels_unmatched']:,} cancels and "
          f"{c['reversals_matched']:,}/{c['reversals_matched'] + c['reversals_unmatched']:,} reversals matched to an original; "
          f"{c['corrections_dropped']:,} corrections and {c['other_status_dropped']:,} other-status rows dropped; "
          f"{c['rows_out']:,} prints kept")
    print(f"{stats['prints_at_or_above_cap']:,} prints winsorised at ${args.volume_cap:,.0f}")
    print(f"{stats['cusips_with_activity']:,} of {stats['cusips_in_crosswalk']:,} crosswalk CUSIPs traded in "
          f"{stats['window']['start']} .. {stats['window']['end']} ({stats['distinct_trade_dates']} distinct trade dates)")
    print(f'wrote {args.out} and {args.report}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
