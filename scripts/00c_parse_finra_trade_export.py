"""
Turn a manual export from FINRA's public "Corporate and Agency Trade Activity" page
into data/trace_activity_raw.csv (the Step 6 input; schema in trace_utils.ACTIVITY_COLUMNS).

Why manual: FINRA's Fixed Income Data user agreement forbids automated copying of the
site, so the trade prints are exported by hand (Export button on
finra.org/finra-data/fixed-income/corp-and-agency/trade with the date filter set) and
dropped into data/ -- never committed. This script only reads those files.

Usage (from scripts/):
    python 00c_parse_finra_trade_export.py ../data/trace_export/*.csv \
        --crosswalk ../data/lqd_cusip_crosswalk.csv \
        --window-start 2026-04-22 --window-end 2026-07-22 \
        --out ../data/trace_activity_raw.csv

Accepts .csv or .xlsx, any number of files (e.g. one per day or week if the export is
row-capped), with either the site's display headers ("CUSIP", "Date", "Quantity",
"Trade Status", "Contra Party Type", ...) or the API field names. Duplicate prints across
overlapping files are dropped. Only CUSIPs in the crosswalk are kept, so the output is
small even though the market-wide export is ~1M rows.
"""
import argparse
import os
import sys

import pandas as pd

from trace_utils import ACTIVITY_COLUMNS, aggregate_trades

# display header on the FINRA page -> API field name used by trace_utils
HEADER_MAP = {
    'cusip': 'cusip',
    'date': 'tradeExecutionDate', 'trade date': 'tradeExecutionDate', 'execution date': 'tradeExecutionDate',
    'quantity': 'reportedTradeVolume', 'volume': 'reportedTradeVolume', 'reported quantity': 'reportedTradeVolume',
    'trade status': 'tradeStatus', 'status': 'tradeStatus',
    'contra party type': 'contraPartyTypeCode', 'contra party': 'contraPartyTypeCode',
    'time': 'tradeExecutionTime', 'price': 'lastSalePrice', 'yield': 'lastSaleYield',
    'side': 'reportingPartySideCode', 'as-of': 'isAsof', 'as of': 'isAsof',
    'symbol': 'issueSymbolIdentifier', 'issuer name': 'issuerName',
    'message sequence number': 'messageSequenceNumber', 'sequence number': 'messageSequenceNumber',
}
REQUIRED = ['cusip', 'tradeExecutionDate', 'reportedTradeVolume']
DEDUP_KEYS = ['cusip', 'tradeExecutionDate', 'tradeExecutionTime', 'reportedTradeVolume',
              'lastSalePrice', 'reportingPartySideCode', 'contraPartyTypeCode', 'tradeStatus']


def normalise_columns(df):
    """Map display headers (case/space-insensitive) or API names onto API names."""
    api_names = set(HEADER_MAP.values())
    renamed = {}
    for c in df.columns:
        key = str(c).strip().lower()
        if c in api_names:
            continue
        if key in HEADER_MAP:
            renamed[c] = HEADER_MAP[key]
    out = df.rename(columns=renamed)
    missing = [c for c in REQUIRED if c not in out.columns]
    if missing:
        raise ValueError(f'export is missing columns {missing}; enable them under "Columns" on the FINRA page '
                         f'before exporting. Columns seen: {list(df.columns)}')
    return out


def read_export(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.xlsx', '.xls'):
        df = pd.read_excel(path, dtype=str)
    else:
        df = pd.read_csv(path, dtype=str)
    df = normalise_columns(df)
    df['cusip'] = df['cusip'].astype(str).str.strip().str.upper()
    # display values on the page are like "M-Trade" / "C-Contra party is a Customer"; keep the code letter
    for col in ('tradeStatus', 'contraPartyTypeCode', 'reportingPartySideCode', 'isAsof'):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().str.upper().str.replace(r'[^A-Z].*$', '', regex=True)
    return df


def build_activity(files, crosswalk_cusips, window_start, window_end):
    frames = [read_export(f) for f in files]
    trades = pd.concat(frames, ignore_index=True)
    n_raw = len(trades)
    # Overlapping exports repeat prints. FINRA's message sequence number is the print's
    # identity when the export carries it; otherwise fall back to the full attribute set
    # (which also collapses two genuinely identical prints -- rare, and noted in stats).
    if 'messageSequenceNumber' in trades.columns:
        keys = ['cusip', 'tradeExecutionDate', 'messageSequenceNumber']
    else:
        keys = [k for k in DEDUP_KEYS if k in trades.columns]
    trades = trades.drop_duplicates(subset=keys)
    n_dedup = len(trades)
    trades = trades[trades['cusip'].isin(crosswalk_cusips)]
    n_lqd = len(trades)
    act = aggregate_trades(trades, window_start, window_end)
    act = act.reindex(columns=ACTIVITY_COLUMNS)
    stats = {'rows_read': n_raw, 'rows_after_dedup': n_dedup, 'rows_in_lqd_universe': n_lqd,
             'cusips_with_activity': int(act['cusip'].nunique()),
             'cusips_in_crosswalk': int(len(crosswalk_cusips)),
             'dates_seen': sorted(pd.to_datetime(trades['tradeExecutionDate'], errors='coerce').dt.strftime('%Y-%m-%d').dropna().unique().tolist())}
    return act, stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('files', nargs='+', help='exported .csv/.xlsx files from the FINRA trade activity page')
    ap.add_argument('--crosswalk', default='../data/lqd_cusip_crosswalk.csv')
    ap.add_argument('--window-start', default='2026-04-22')
    ap.add_argument('--window-end', default='2026-07-22')
    ap.add_argument('--out', default='../data/trace_activity_raw.csv')
    args = ap.parse_args(argv)

    xw = pd.read_csv(args.crosswalk, dtype={'cusip': str})
    cusips = set(xw['cusip'].dropna().astype(str).str.strip().str.upper())
    act, stats = build_activity(args.files, cusips, args.window_start, args.window_end)
    act.to_csv(args.out, index=False)
    print(f"read {stats['rows_read']:,} prints from {len(args.files)} file(s); {stats['rows_after_dedup']:,} after de-dup; "
          f"{stats['rows_in_lqd_universe']:,} in the LQD universe")
    print(f"{stats['cusips_with_activity']:,} of {stats['cusips_in_crosswalk']:,} crosswalk CUSIPs traded in the window; "
          f"{len(stats['dates_seen'])} distinct trade dates ({stats['dates_seen'][0]} .. {stats['dates_seen'][-1]})"
          if stats['dates_seen'] else 'no dated prints found')
    print(f'wrote {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
