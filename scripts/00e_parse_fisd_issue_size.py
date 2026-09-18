"""
Turn a WRDS Mergent FISD export into data/fisd_issue_size.csv: one row per LQD bond with the
bond's TRUE issue size, the direct test of the reviewer's "par holding ~ amount outstanding"
point that Step 6 could not run before.

What to pull on WRDS (Mergent FISD -> Bond Issues, "Mergent FISD Issue" / fisd_mergedissue):
  upload data/lqd_cusips.txt as the CUSIP list (variable COMPLETE_CUSIP), select at least
  COMPLETE_CUSIP and OFFERING_AMT (plus OFFERING_DATE, MATURITY, and AMOUNT_OUTSTANDING if the
  form offers it -- WRDS keeps a separate "Amount Outstanding" history table; if you pull that
  one too, its EFFECTIVE_DATE/AMOUNT_OUTSTANDING columns are understood here as well).
  Save as CSV under data/raw/ (gitignored: FISD is licensed).

FISD reports amounts in thousands of dollars; the script converts to dollars, auto-detecting
the unit from the median (LQD issues are $300MM+) unless --units is given. Duplicate CUSIPs
(amount-outstanding history rows) keep the latest by date. Only crosswalk CUSIPs are kept.
AMOUNT_OUTSTANDING == 0 (a FISD quirk on a few issues with a real OFFERING_AMT) is treated
as missing so Step 6 falls back to the offering amount instead of ranking them smallest.

Usage (from scripts/):
    python 00e_parse_fisd_issue_size.py ../data/raw/fisd_lqd.csv \
        --crosswalk ../data/lqd_cusip_crosswalk.csv \
        --out ../data/fisd_issue_size.csv --report ../output/step6/step6_fisd_coverage.json
"""
import argparse
import json
import sys

import pandas as pd

CUSIP_COLS = ['complete_cusip', 'cusip', 'cusip_id', 'cusip9']
AMOUNT_COLS = {'offering_amt': ['offering_amt', 'offering_amount', 'offer_amt'],
               'amount_outstanding': ['amount_outstanding', 'amt_outstanding', 'amount_out', 'amt_out']}
DATE_COLS = ['effective_date', 'offering_date', 'offer_date']
OUT_COLUMNS = ['bond_id', 'cusip', 'offering_amt', 'amount_outstanding', 'offering_date']


def _find(cols, candidates):
    return next((c for c in candidates if c in cols), None)


def build_issue_size(path, crosswalk_path, units='auto'):
    raw = pd.read_csv(path, dtype=str)
    raw.columns = [str(c).strip().lower() for c in raw.columns]
    cusip_col = _find(raw.columns, CUSIP_COLS)
    if cusip_col is None:
        raise ValueError(f'no CUSIP column among {list(raw.columns)}; expected one of {CUSIP_COLS} '
                         '(select COMPLETE_CUSIP on the WRDS form)')
    amt_cols = {k: _find(raw.columns, v) for k, v in AMOUNT_COLS.items()}
    if amt_cols['offering_amt'] is None and amt_cols['amount_outstanding'] is None:
        raise ValueError(f'no amount column among {list(raw.columns)}; expected one of {AMOUNT_COLS}')

    xw = pd.read_csv(crosswalk_path, dtype={'cusip': str})
    xw = xw[xw['cusip'].notna()][['bond_id', 'cusip']]
    xw['cusip'] = xw['cusip'].str.strip().str.upper()

    df = pd.DataFrame({'cusip': raw[cusip_col].astype(str).str.strip().str.upper()})
    for k, c in amt_cols.items():
        df[k] = pd.to_numeric(raw[c].astype(str).str.replace(',', ''), errors='coerce') if c else float('nan')
    # FISD quirk: a handful of issues carry AMOUNT_OUTSTANDING == 0 with a real OFFERING_AMT. A
    # literal zero would sort them as the smallest issues; treat it as missing so the offering
    # amount is the fallback downstream.
    zero_out = df['amount_outstanding'].eq(0)
    df.loc[zero_out, 'amount_outstanding'] = float('nan')
    date_col = _find(raw.columns, DATE_COLS)
    df['_date'] = pd.to_datetime(raw[date_col], errors='coerce') if date_col else pd.NaT
    off_col = _find(raw.columns, ['offering_date', 'offer_date'])
    df['offering_date'] = pd.to_datetime(raw[off_col], errors='coerce').dt.strftime('%Y-%m-%d') if off_col else None
    n_raw = len(df)
    df = df[df['cusip'].isin(set(xw['cusip']))]
    n_lqd = len(df)

    basis = df['offering_amt'] if amt_cols['offering_amt'] else df['amount_outstanding']
    if units == 'auto':
        med = float(basis.dropna().median()) if basis.notna().any() else 0.0
        units_detected = 'thousands' if med < 1e7 else 'dollars'
    else:
        units_detected = units
    if units_detected == 'thousands':
        df[['offering_amt', 'amount_outstanding']] = df[['offering_amt', 'amount_outstanding']] * 1_000.0

    # amount-outstanding history rows: the latest dated row is the current figure
    df = df.sort_values(['cusip', '_date'], na_position='first').drop_duplicates('cusip', keep='last')
    out = xw.merge(df.drop(columns=['_date']), on='cusip', how='inner').sort_values('bond_id')
    out = out.reindex(columns=OUT_COLUMNS).reset_index(drop=True)
    stats = {
        'source': 'WRDS Mergent FISD (fisd_mergedissue), filtered on complete_cusip',
        'rows_read': int(n_raw), 'rows_in_lqd_universe': int(n_lqd), 'units_detected': units_detected,
        'bonds_with_issue_size': int(len(out)), 'cusips_in_crosswalk': int(len(xw)),
        'bonds_with_amount_outstanding': int(out['amount_outstanding'].notna().sum()),
        'amount_outstanding_zero_treated_missing': int(zero_out.sum()),
        'columns_seen': list(raw.columns),
    }
    return out, stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('fisd_csv', help='WRDS Mergent FISD export (.csv)')
    ap.add_argument('--crosswalk', default='../data/lqd_cusip_crosswalk.csv')
    ap.add_argument('--units', choices=['auto', 'thousands', 'dollars'], default='auto')
    ap.add_argument('--out', default='../data/fisd_issue_size.csv')
    ap.add_argument('--report', default='../output/step6/step6_fisd_coverage.json')
    args = ap.parse_args(argv)
    out, stats = build_issue_size(args.fisd_csv, args.crosswalk, args.units)
    out.to_csv(args.out, index=False)
    with open(args.report, 'w') as f:
        json.dump(stats, f, indent=2)
    print(f"read {stats['rows_read']:,} FISD rows; {stats['rows_in_lqd_universe']:,} in the LQD universe; "
          f"amounts read as {stats['units_detected']}")
    print(f"{stats['bonds_with_issue_size']:,} of {stats['cusips_in_crosswalk']:,} crosswalk CUSIPs have an issue size; "
          f"{stats['bonds_with_amount_outstanding']:,} also have amount outstanding")
    print(f'wrote {args.out} and {args.report}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
