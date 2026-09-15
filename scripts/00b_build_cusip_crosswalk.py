"""
Build data/lqd_cusip_crosswalk.csv: one CUSIP per LQD bond, from public SEC data.

The iShares holdings download carries no CUSIP. The fund's own Form N-PORT filing on
EDGAR does (plus ISIN, par balance, coupon, maturity), and SEC filings are public-domain,
so this is the licence-clean identifier source. The N-PORT is dated at LQD's fiscal
quarter end (2026-05-31 for the 2026-07-22 holdings used here), so bonds bought after
that date have no CUSIP from this route and are reported as unmatched.

Getting the file: EDGAR full-text search for "iBoxx $ Investment Grade Corporate Bond ETF"
with form type NPORT-P -> the iShares Trust filing -> primary_doc.xml. Save it under
data/raw/ (not committed; ~4 MB). The parsed holdings CSV *is* committed.

Usage (from scripts/):
    python 00b_build_cusip_crosswalk.py ../data/raw/LQD_NPORT-P_2026-05-31_primary_doc.xml

Matching: exact coupon + maturity from the N-PORT vs the iShares row, then issuer-name
similarity (trace_utils.match_lqd_to_master). Because both files describe the *same
fund*, the N-PORT par balance should nearly equal the iShares Par Value on a correct
match; the script reports that agreement as a check on the matcher.
"""
import argparse
import os
import sys
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from portfolio_utils import load_bonds
from trace_utils import match_lqd_to_master

NPORT_COLUMNS = ['cusip', 'isin', 'name', 'title', 'balance', 'valUSD', 'pctVal',
                 'assetCat', 'issuerCat', 'maturityDt', 'annualizedRt', 'couponKind']


def _local(tag):
    return tag.rsplit('}', 1)[-1]


def parse_nport(path):
    """Holdings table from an N-PORT primary_doc.xml, namespace-agnostic. Returns
    (holdings DataFrame with NPORT_COLUMNS, metadata dict)."""
    root = ET.parse(path).getroot()

    def first_text(el, name):
        for e in el.iter():
            if _local(e.tag) == name:
                return (e.text or '').strip()
        return ''

    meta = {'seriesName': first_text(root, 'seriesName'), 'seriesId': first_text(root, 'seriesId'),
            'repPdDate': first_text(root, 'repPdDate')}
    rows = []
    for sec in (e for e in root.iter() if _local(e.tag) == 'invstOrSec'):
        rec = {c: '' for c in NPORT_COLUMNS}
        for e in sec.iter():
            t = _local(e.tag)
            if t in rec and t != 'isin':
                rec[t] = (e.text or '').strip()
            elif t == 'isin':
                rec['isin'] = e.get('value', '') or (e.text or '').strip()
        rows.append(rec)
    df = pd.DataFrame(rows, columns=NPORT_COLUMNS)
    for c in ('balance', 'valUSD', 'pctVal', 'annualizedRt'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df, meta


def build_crosswalk(bonds, nport):
    debt = nport[(nport['assetCat'] == 'DBT') & nport['cusip'].ne('')].copy()
    master = pd.DataFrame({
        'cusip': debt['cusip'].values,
        'issuerName': debt['name'].values,
        'couponRate': debt['annualizedRt'].values,
        'maturityDate': debt['maturityDt'].values,
        'nport_balance': debt['balance'].values,
        'nport_pctVal': debt['pctVal'].values,
        'isin': debt['isin'].values,
    })
    xw = match_lqd_to_master(bonds, master, size_cols=('Par Value', 'nport_balance'))
    xw = xw.drop(columns=['is_144a', 'moodys_rating', 'sp_rating'])   # not in N-PORT
    xw = xw.merge(master[['cusip', 'isin', 'nport_balance', 'nport_pctVal']], on='cusip', how='left')
    xw = xw.merge(bonds[['bond_id', 'Name', 'Par Value']], on='bond_id', how='left')
    matched = xw['cusip'].notna()
    # same fund, ~7 weeks apart: par balances should agree closely on a correct match
    ratio = xw.loc[matched, 'nport_balance'] / xw.loc[matched, 'Par Value']
    xw['par_ratio_nport_over_ishares'] = np.nan
    xw.loc[matched, 'par_ratio_nport_over_ishares'] = ratio.values
    return xw


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('nport_xml')
    ap.add_argument('--holdings', default='../data/lqd_holdings_raw.csv')
    ap.add_argument('--out-crosswalk', default='../data/lqd_cusip_crosswalk.csv')
    ap.add_argument('--out-nport-csv', default='../data/lqd_nport_holdings.csv')
    args = ap.parse_args(argv)

    nport, meta = parse_nport(args.nport_xml)
    print(f"N-PORT: {meta['seriesName']} ({meta['seriesId']}), period {meta['repPdDate']}, "
          f"{len(nport)} holdings, {int((nport['assetCat'] == 'DBT').sum())} debt")
    nport.to_csv(args.out_nport_csv, index=False)

    bonds = load_bonds(args.holdings, include_rating=False)
    xw = build_crosswalk(bonds, nport)
    xw.to_csv(args.out_crosswalk, index=False)

    n = len(xw)
    counts = xw['match_method'].value_counts()
    print('\nmatch report:')
    for k, v in counts.items():
        print(f'  {k:35s} {v:5d}  ({v / n:.1%})')
    matched = xw[xw['cusip'].notna()]
    r = matched['par_ratio_nport_over_ishares']
    print(f'\npar agreement on matched bonds (N-PORT balance / iShares Par Value): '
          f'median {r.median():.3f}, within 10%: {(r.sub(1).abs() <= 0.10).mean():.1%}, '
          f'ratio outside [0.5, 2]: {int(((r < 0.5) | (r > 2)).sum())}')
    dup = matched['cusip'].duplicated(keep=False)
    if dup.any():
        print(f'WARNING: {int(dup.sum())} LQD rows share a CUSIP:')
        print(matched.loc[dup, ['bond_id', 'Name', 'cusip', 'match_score']].to_string(index=False))
    unmatched = xw[xw['cusip'].isna()]
    if len(unmatched):
        print(f'\nunmatched ({len(unmatched)}), first 15:')
        print(unmatched[['bond_id', 'Name', 'match_method', 'match_score', 'n_candidates']].head(15).to_string(index=False))
    print(f'\nwrote {args.out_crosswalk} and {args.out_nport_csv}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
