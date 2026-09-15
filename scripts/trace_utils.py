"""
Source-agnostic helpers for the TRACE liquidity workstream (Step 6+).

Two jobs, both independent of *where* the FINRA data comes from (paid TSAR, WRDS
TRACE Enhanced, or a manual export from FINRA's public Fixed Income Data site --
see docs/superpowers/specs/2026-09-15-trace-liquidity-measure-design.md):

1. Map LQD holdings (which carry no CUSIP) onto a corporate-bond security master
   using exact coupon + maturity plus issuer-name similarity.
2. Collapse trade-level TRACE prints into per-CUSIP activity metrics over a window,
   handling the public feed's volume caps ("5MM+" / "1MM+") and cancels.
"""
import re

import numpy as np
import pandas as pd

# Tokens that carry no identifying information about *which* issuer a name refers to.
# Includes iShares decorations ("/THE", "MTN", "(FXD-FRN)", "144A") and legal suffixes,
# which FINRA and iShares abbreviate differently ("COMPANIES" vs "COS").
_NOISE_TOKENS = {
    'INC', 'INCORPORATED', 'CORP', 'CORPORATION', 'CO', 'COS', 'COMPANY', 'COMPANIES',
    'LLC', 'LTD', 'LIMITED', 'PLC', 'LP', 'L P', 'SA', 'NV', 'AG', 'SE', 'DE', 'PTE',
    'HOLDINGS', 'HLDGS', 'HOLDING', 'GROUP', 'GRP', 'THE', 'MTN', 'FXD', 'FRN',
    '144A', 'NOTES', 'SR', 'JR', 'ISSUER', 'FUNDING', 'FINANCE', 'FIN', 'FINL', 'FINANCIAL',
}

VOLUME_CAPS = {'5MM+': 5_000_000.0, '1MM+': 1_000_000.0}


def normalize_issuer_name(name):
    """Reduce an issuer name to a set of identifying tokens.

    'GOLDMAN SACHS GROUP INC/THE' -> {'GOLDMAN', 'SACHS'}
    'US BANCORP (FXD-FRN) MTN'    -> {'US', 'BANCORP'}
    'ANHEUSER-BUSCH COS LLC'      -> {'ANHEUSER', 'BUSCH'}
    """
    if name is None or (isinstance(name, float) and np.isnan(name)):
        return frozenset()
    s = str(name).upper()
    s = re.sub(r'\(.*?\)', ' ', s)           # drop parentheticals like (FXD-FRN)
    s = s.replace('/THE', ' ').replace('&', ' AND ')
    s = re.sub(r'[^A-Z0-9]+', ' ', s)
    tokens = [t for t in s.split() if t and t not in _NOISE_TOKENS and t != 'AND']
    if not tokens:                            # everything was noise; keep the raw first word
        raw = re.sub(r'[^A-Z0-9]+', ' ', str(name).upper()).split()
        tokens = raw[:1]
    return frozenset(tokens)


def name_similarity(a, b):
    """Jaccard similarity of two normalised token sets (0..1)."""
    ta, tb = normalize_issuer_name(a), normalize_issuer_name(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def parse_volume(v):
    """Public TRACE prints cap displayed size: '5MM+' (IG) / '1MM+' (HY).
    Returns (volume_floor, is_capped)."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return (np.nan, False)
    s = str(v).strip()
    if s in VOLUME_CAPS:
        return (VOLUME_CAPS[s], True)
    try:
        return (float(s.replace(',', '')), False)
    except ValueError:
        return (np.nan, False)


def match_lqd_to_master(bonds, master, min_similarity=0.34):
    """Match each LQD bond to one CUSIP in a security master.

    bonds : DataFrame with bond_id, Name, Coupon (%), Maturity (datetime)
    master: DataFrame with cusip, issuerName, couponRate, maturityDate, and
            optionally is144A ('Y'/'N') and lastTradeDate

    Candidates share exact coupon (3 dp) and maturity; the winner is the candidate with
    the highest issuer-name similarity, preferring the registered line (is144A == 'N',
    non-Reg S CUSIP) unless the LQD name says 144A, then the most recently traded.
    Every bond_id gets exactly one row; unmatched bonds keep cusip = NaN and a
    match_method explaining why. Nothing is dropped silently.
    """
    m = master.copy()
    m['_coupon'] = pd.to_numeric(m['couponRate'], errors='coerce').round(3)
    m['_maturity'] = pd.to_datetime(m['maturityDate'], errors='coerce').dt.normalize()
    m['_is144a'] = m['is144A'].astype(str).str.upper().eq('Y') if 'is144A' in m else False
    m['_regs'] = m['cusip'].astype(str).str.upper().str.startswith('U')
    m['_last_trade'] = pd.to_datetime(m['lastTradeDate'], errors='coerce') if 'lastTradeDate' in m else pd.NaT
    groups = {k: g for k, g in m.groupby(['_coupon', '_maturity'])}

    rows = []
    for b in bonds[['bond_id', 'Name', 'Coupon (%)', 'Maturity']].to_dict('records'):
        key = (round(float(b['Coupon (%)']), 3), pd.Timestamp(b['Maturity']).normalize())
        wants_144a = '144A' in str(b['Name']).upper()
        cands = groups.get(key)
        rec = {'bond_id': b['bond_id'], 'cusip': np.nan, 'match_method': 'no_coupon_maturity_candidates',
               'match_score': np.nan, 'n_candidates': 0, 'n_name_matches': 0,
               'finra_issuer_name': np.nan, 'is_144a': np.nan, 'moodys_rating': np.nan, 'sp_rating': np.nan}
        if cands is not None and len(cands):
            c = cands.copy()
            c['_sim'] = [name_similarity(b['Name'], n) for n in c['issuerName']]
            c['_pref'] = (c['_is144a'] == wants_144a).astype(int) * 2 + (~c['_regs']).astype(int)
            c = c.sort_values(['_sim', '_pref', '_last_trade'], ascending=[False, False, False])
            best = c.iloc[0]
            rec.update(n_candidates=int(len(c)),
                       n_name_matches=int((c['_sim'] >= min_similarity).sum()))
            if best['_sim'] >= min_similarity:
                rec.update(cusip=best['cusip'], match_score=float(best['_sim']),
                           match_method='coupon_maturity_name',
                           finra_issuer_name=best['issuerName'],
                           is_144a='Y' if best['_is144a'] else 'N',
                           moodys_rating=best.get('moodysRating', np.nan),
                           sp_rating=best.get('standardAndPoorsRating', np.nan))
            else:
                rec.update(match_method='name_below_threshold', match_score=float(best['_sim']))
        rows.append(rec)
    return pd.DataFrame(rows)


def aggregate_trades(trades, window_start, window_end):
    """Per-CUSIP activity metrics from trade-level prints.

    trades: DataFrame with cusip, tradeExecutionDate, reportedTradeVolume and optionally
            tradeStatus ('M' trade / 'N' cancel / 'O' correction) and contraPartyTypeCode
            ('C' customer, 'D' dealer, 'A' affiliate, 'T' ATS).
    Cancels are dropped; corrections are kept as the surviving version of the print.
    Volume is a *floor* because of the public feed's caps; n_capped_trades says how often
    the cap bound. Only CUSIPs present in `trades` appear -- the caller adds zeros for
    matched bonds with no prints (zero trades is a real, maximally-illiquid observation).
    """
    t = trades.copy()
    t['tradeExecutionDate'] = pd.to_datetime(t['tradeExecutionDate'], errors='coerce').dt.normalize()
    ws, we = pd.Timestamp(window_start).normalize(), pd.Timestamp(window_end).normalize()
    t = t[(t['tradeExecutionDate'] >= ws) & (t['tradeExecutionDate'] <= we)]
    if 'tradeStatus' in t:
        t = t[t['tradeStatus'].astype(str).str.upper() != 'N']
    parsed = t['reportedTradeVolume'].map(parse_volume)
    t['_vol'] = [p[0] for p in parsed]
    t['_capped'] = [p[1] for p in parsed]
    cp = t['contraPartyTypeCode'].astype(str).str.upper() if 'contraPartyTypeCode' in t else pd.Series('', index=t.index)
    t['_cust'] = cp.eq('C')
    t['_dealer'] = cp.isin(['D', 'T', 'A'])

    g = t.groupby('cusip')
    out = pd.DataFrame({
        'n_trades': g.size(),
        'n_capped_trades': g['_capped'].sum().astype(int),
        'total_volume_floor': g['_vol'].sum(),
        'days_traded': g['tradeExecutionDate'].nunique(),
        'n_customer_trades': g['_cust'].sum().astype(int),
        'n_dealer_trades': g['_dealer'].sum().astype(int),
        'median_trade_size': g['_vol'].median(),
    }).reset_index()
    out.insert(1, 'window_start', ws.strftime('%Y-%m-%d'))
    out.insert(2, 'window_end', we.strftime('%Y-%m-%d'))
    return out


ACTIVITY_COLUMNS = ['cusip', 'window_start', 'window_end', 'n_trades', 'n_capped_trades',
                    'total_volume_floor', 'days_traded', 'n_customer_trades', 'n_dealer_trades',
                    'median_trade_size']
