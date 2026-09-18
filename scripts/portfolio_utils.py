"""
Shared utilities for benchmark target computation and portfolio evaluation.
Used by all sampling methods (Step 2) and later liquidity/trade-off analysis
(Steps 3-5) so every method is scored against exactly the same targets.
"""
import os

import numpy as np
import pandas as pd

AS_OF_DATE = pd.Timestamp('2026-07-22')
MATURITY_BINS = [0, 1, 2, 3, 5, 7, 10, 15, 20, 100]
MATURITY_LABELS = ['0-1yr', '1-2yr', '2-3yr', '3-5yr', '5-7yr', '7-10yr', '10-15yr', '15-20yr', '20yr+']

SCORES = ('par', 'trace')   # liquidity scores the Step 3-5 pipeline can optimise on


def output_paths(score, root='output'):
    """Where a Step 3-5 run lands. 'par' is the historical layout (normalised log par
    holding); 'trace' is the parallel layout for the TRACE-activity score, so both
    frontiers coexist and Step 7 can compare them."""
    if score not in SCORES:
        raise ValueError(f'score must be one of {SCORES}, got {score!r}')
    sfx = '' if score == 'par' else '_trace'
    return {'weights_dir': f'{root}/liquidity{sfx}', 'sweep_csv': f'{root}/step3_liquidity_sweep{sfx}.csv',
            'step4_dir': f'{root}/step4{sfx}', 'step5_dir': f'{root}/step5{sfx}'}


def _prep_rating_source(path):
    df = pd.read_csv(path)
    df = df[df['Asset Class'] == 'Fixed Income'].copy()
    for c in ['Coupon (%)']:
        df[c] = pd.to_numeric(df[c].astype(str).str.replace(',', ''), errors='coerce')
    df['Maturity'] = pd.to_datetime(df['Maturity'], errors='coerce')
    df['match_key'] = (df['Name'].str.strip().str.upper() + '|' +
                        df['Coupon (%)'].round(3).astype(str) + '|' +
                        df['Maturity'].dt.strftime('%Y-%m-%d'))
    return df


def compute_rating_buckets(bonds, qlta_path='data/qlta_holdings_raw.csv', lqdb_path='data/lqdb_holdings_raw.csv'):
    """Bond-level rating proxy sourced entirely from public data: matches each LQD
    bond (by issuer name + coupon + maturity) against the holdings of QLTA
    (Aaa-A rated corporates) and LQDB (BBB rated corporates), both run by
    BlackRock on closely related index methodologies. Bonds that don't
    directly match either fund (smaller, less-liquid names excluded from
    those funds' own screens) are inferred from whichever rating bucket
    their issuer's OTHER bonds fall into.

    Validated against LQD's official fact sheet rating distribution
    (AAA 1.03% + AA 9.05% + A 47.93% = 58.01% A-or-better, 40.47% BBB):
    this method recovers ~59.1% / 37.4%, with ~2.8% left Unknown/Ambiguous.
    """
    qlta = _prep_rating_source(qlta_path)
    lqdb = _prep_rating_source(lqdb_path)

    bonds = bonds.copy()
    bonds['match_key'] = (bonds['Name'].str.strip().str.upper() + '|' +
                           bonds['Coupon (%)'].round(3).astype(str) + '|' +
                           bonds['Maturity'].dt.strftime('%Y-%m-%d'))

    qlta_keys = set(qlta['match_key'])
    lqdb_keys = set(lqdb['match_key'])
    direct = np.where(bonds['match_key'].isin(qlta_keys), 'A-or-better',
                np.where(bonds['match_key'].isin(lqdb_keys), 'BBB', 'Unmatched'))
    bonds['_direct_match'] = direct

    bbb_issuers = set(bonds.loc[bonds._direct_match == 'BBB', 'Name'])
    a_issuers = set(bonds.loc[bonds._direct_match == 'A-or-better', 'Name'])

    def refine(row):
        if row['_direct_match'] != 'Unmatched':
            return row['_direct_match']
        in_bbb, in_a = row['Name'] in bbb_issuers, row['Name'] in a_issuers
        if in_bbb and not in_a:
            return 'BBB'
        if in_a and not in_bbb:
            return 'A-or-better'
        return 'Unknown/Ambiguous'

    bonds['Rating_Bucket'] = bonds.apply(refine, axis=1)
    bonds = bonds.drop(columns=['match_key', '_direct_match'])
    return bonds['Rating_Bucket']


def load_bonds(path='data/lqd_holdings_raw.csv', include_rating=True):
    """Load and prep the bond-level benchmark universe (excludes cash/money market)."""
    df = pd.read_csv(path, parse_dates=['Maturity', 'Accrual Date', 'Effective Date'])
    bonds = df[df['Asset Class'] == 'Fixed Income'].copy().reset_index(drop=True)

    bonds['Weight_Renorm'] = bonds['Weight (%)'] / bonds['Weight (%)'].sum()  # fraction, sums to 1
    bonds['Years_to_Maturity'] = (bonds['Maturity'] - AS_OF_DATE).dt.days / 365.25
    bonds['Maturity_Bucket'] = pd.cut(bonds['Years_to_Maturity'], bins=MATURITY_BINS, labels=MATURITY_LABELS)
    bonds['bond_id'] = bonds.index  # stable integer id used across sampling methods

    # --- Liquidity proxy ---
    # NOTE: the public iShares holdings file has no bond-level "amount outstanding"
    # field. We use the ETF's own Par Value (par amount of the position held) as a
    # proxy: larger ETF positions generally correspond to larger, more liquid bond
    # issues, since iBoxx-style indices weight constituents by issue size. This is
    # an approximation, not a direct liquidity measure (e.g. no bid-ask spread or
    # trade volume data is available) - documented here and in the write-up.
    bonds['Liquidity_Score_Raw'] = np.log(bonds['Par Value'].clip(lower=1))
    lo, hi = bonds['Liquidity_Score_Raw'].min(), bonds['Liquidity_Score_Raw'].max()
    bonds['Liquidity_Score'] = (bonds['Liquidity_Score_Raw'] - lo) / (hi - lo)  # 0-1, higher = more liquid

    if include_rating:
        try:
            bonds['Rating_Bucket'] = compute_rating_buckets(bonds)
        except FileNotFoundError:
            bonds['Rating_Bucket'] = 'Unknown/Ambiguous'

    return bonds


def sector_dummies(bonds):
    return pd.get_dummies(bonds['Sector'])


def rating_dummies(bonds):
    cats = ['A-or-better', 'BBB', 'Unknown/Ambiguous']
    return pd.get_dummies(bonds['Rating_Bucket']).reindex(columns=cats, fill_value=0)


def maturity_dummies(bonds):
    return pd.get_dummies(bonds['Maturity_Bucket'].astype(str)).reindex(columns=MATURITY_LABELS, fill_value=0)


def compute_benchmark_targets(bonds):
    """Aggregate characteristics of the full benchmark - the thing every sampled
    portfolio is trying to replicate."""
    w = bonds['Weight_Renorm'].values
    sec_dum = sector_dummies(bonds)
    mat_dum = maturity_dummies(bonds)
    rat_dum = rating_dummies(bonds)

    targets = {
        'sector_weights': dict(zip(sec_dum.columns, sec_dum.T.values @ w)),
        'maturity_weights': dict(zip(mat_dum.columns, mat_dum.T.values @ w)),
        'rating_weights': dict(zip(rat_dum.columns, rat_dum.T.values @ w)),
        'duration': float((bonds['Duration'] * w).sum()),
        'ytm': float((bonds['YTM (%)'] * w).sum()),
        'coupon': float((bonds['Coupon (%)'] * w).sum()),
        'years_to_maturity': float((bonds['Years_to_Maturity'] * w).sum()),
    }
    return targets


def evaluate_portfolio(bonds, weights, targets):
    """Given a full-length weight vector (0 for unselected bonds, sums to 1),
    compute its aggregate characteristics and deviation from benchmark targets."""
    w = np.asarray(weights)
    assert len(w) == len(bonds)

    sec_dum = sector_dummies(bonds)
    mat_dum = maturity_dummies(bonds)
    rat_dum = rating_dummies(bonds)

    port_sector = dict(zip(sec_dum.columns, sec_dum.T.values @ w))
    port_maturity = dict(zip(mat_dum.columns, mat_dum.T.values @ w))
    port_rating = dict(zip(rat_dum.columns, rat_dum.T.values @ w))
    port_duration = float((bonds['Duration'].values * w).sum())
    port_ytm = float((bonds['YTM (%)'].values * w).sum())
    port_coupon = float((bonds['Coupon (%)'].values * w).sum())
    port_ytmat = float((bonds['Years_to_Maturity'].values * w).sum())

    sector_dev = sum(abs(port_sector.get(k, 0) - v) for k, v in targets['sector_weights'].items())
    maturity_dev = sum(abs(port_maturity.get(k, 0) - v) for k, v in targets['maturity_weights'].items())
    rating_dev = sum(abs(port_rating.get(k, 0) - v) for k, v in targets['rating_weights'].items())

    issuer_weights = pd.Series(w, index=bonds['Name']).groupby(level=0).sum()
    issuer_weights = issuer_weights[issuer_weights > 1e-10]
    hhi = float((issuer_weights ** 2).sum() * 10000)  # scale to 0-10000 convention

    port_liquidity = float((bonds['Liquidity_Score'].values * w).sum()) if 'Liquidity_Score' in bonds.columns else None

    return {
        'n_holdings': int((w > 1e-10).sum()),
        'sector_weights': port_sector,
        'maturity_weights': port_maturity,
        'rating_weights': port_rating,
        'duration': port_duration,
        'ytm': port_ytm,
        'coupon': port_coupon,
        'years_to_maturity': port_ytmat,
        'sector_L1_deviation_pct': sector_dev * 100,
        'maturity_L1_deviation_pct': maturity_dev * 100,
        'rating_L1_deviation_pct': rating_dev * 100,
        'duration_abs_diff': abs(port_duration - targets['duration']),
        'ytm_abs_diff_pct': abs(port_ytm - targets['ytm']),
        'top10_issuer_weight_pct': float(issuer_weights.sort_values(ascending=False).head(10).sum() * 100),
        'issuer_hhi': hhi,
        'max_issuer_weight_pct': float(issuer_weights.max() * 100) if len(issuer_weights) else 0.0,
        'weighted_avg_liquidity_score': port_liquidity,
    }
