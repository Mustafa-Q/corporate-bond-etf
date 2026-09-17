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


def _tokens_equivalent(x, y):
    """Exact, or one is a >=3-letter prefix of the other: filings abbreviate
    ('CAP' ~ 'CAPITAL', 'COMM' ~ 'COMMUNICATIONS', 'TELECOMMUNICATIO' ~ 'TELECOMMUNICATIONS')."""
    if x == y:
        return True
    short, long_ = (x, y) if len(x) <= len(y) else (y, x)
    return len(short) >= 3 and long_.startswith(short)


def name_similarity(a, b):
    """Jaccard-style similarity of two normalised token sets (0..1), where tokens
    count as shared when equal or prefix-equivalent (see _tokens_equivalent)."""
    ta, tb = normalize_issuer_name(a), normalize_issuer_name(b)
    if not ta or not tb:
        return 0.0
    unused = set(tb)
    shared = 0
    for x in ta:
        hit = next((y for y in unused if _tokens_equivalent(x, y)), None)
        if hit is not None:
            unused.discard(hit)
            shared += 1
    return shared / (len(ta) + len(tb) - shared)


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


def match_lqd_to_master(bonds, master, min_similarity=0.34, coupon_tol=0.0151, size_cols=None):
    """Match each LQD bond to one CUSIP in a security master, one-to-one.

    bonds : DataFrame with bond_id, Name, Coupon (%), Maturity (datetime)
    master: DataFrame with cusip, issuerName, couponRate, maturityDate, and
            optionally is144A ('Y'/'N') and lastTradeDate
    coupon_tol: coupons within this of each other count as equal. Sources round
            differently (5.805% shows as 5.8 in one file and 5.81 in another).
    size_cols: optional (bond_col, master_col) holding a position size in both files.
            When the master describes the *same* portfolio (e.g. the fund's own N-PORT),
            a lone coupon+maturity candidate whose size agrees with the typical ratio
            is accepted even if the issuer name is abbreviated past recognition.

    Candidates share maturity and (tolerant) coupon. Pairs are scored by issuer-name
    similarity, then preference for the registered line (is144A == 'N', non-Reg S
    CUSIP) unless the LQD name says 144A, then most recent trade. Accepted pairs are
    assigned greedily best-first so no CUSIP serves two bonds. Every bond_id gets
    exactly one row; unmatched bonds keep cusip = NaN and a match_method saying why.
    """
    m = master.reset_index(drop=True).copy()
    m['_coupon'] = pd.to_numeric(m['couponRate'], errors='coerce')
    m['_maturity'] = pd.to_datetime(m['maturityDate'], errors='coerce').dt.normalize()
    m['_is144a'] = m['is144A'].astype(str).str.upper().eq('Y') if 'is144A' in m else False
    m['_regs'] = m['cusip'].astype(str).str.upper().str.startswith('U')
    m['_last_trade'] = pd.to_datetime(m['lastTradeDate'], errors='coerce') if 'lastTradeDate' in m else pd.NaT
    m['_size'] = pd.to_numeric(m[size_cols[1]], errors='coerce') if size_cols else np.nan
    by_maturity = {k: g for k, g in m.groupby('_maturity')}

    cols = ['bond_id', 'Name', 'Coupon (%)', 'Maturity'] + ([size_cols[0]] if size_cols else [])
    blist = bonds[cols].to_dict('records')
    pairs = []          # (bond_pos, master_idx, sim, pref, coupon_diff, size_ratio)
    n_cands, best_sim = {}, {}
    for i, b in enumerate(blist):
        mat = pd.Timestamp(b['Maturity']).normalize()
        cpn = float(b['Coupon (%)'])
        wants_144a = '144A' in str(b['Name']).upper()
        g = by_maturity.get(mat)
        if g is None:
            n_cands[i] = 0
            continue
        c = g[(g['_coupon'] - cpn).abs() <= coupon_tol]
        n_cands[i] = int(len(c))
        for idx, row in c.iterrows():
            sim = name_similarity(b['Name'], row['issuerName'])
            pref = int(row['_is144a'] == wants_144a) * 2 + int(not row['_regs'])
            ratio = (row['_size'] / float(b[size_cols[0]])) if size_cols and float(b[size_cols[0]] or 0) > 0 else np.nan
            pairs.append((i, idx, sim, pref, abs(row['_coupon'] - cpn), ratio))
            best_sim[i] = max(best_sim.get(i, 0.0), sim)

    # size band from confident name matches, used only for the size fallback
    lo, hi = None, None
    if size_cols:
        conf = [p[5] for p in pairs if p[2] >= min_similarity and np.isfinite(p[5])]
        if conf:
            med = float(np.median(conf))
            lo, hi = 0.5 * med, 2.0 * med

    def accepted(p):
        if p[2] >= min_similarity:
            return 'coupon_maturity_name'
        if lo is not None and n_cands[p[0]] == 1 and np.isfinite(p[5]) and lo <= p[5] <= hi:
            return 'coupon_maturity_size'
        return None

    ranked = sorted(pairs, key=lambda p: (-p[2], -p[3], p[4], abs(np.log(p[5])) if np.isfinite(p[5]) else 9.0))
    assigned_bond, used_cusip = {}, set()
    for p in ranked:
        method = accepted(p)
        if method is None or p[0] in assigned_bond or m.at[p[1], 'cusip'] in used_cusip:
            continue
        assigned_bond[p[0]] = (p, method)
        used_cusip.add(m.at[p[1], 'cusip'])

    rows = []
    for i, b in enumerate(blist):
        rec = {'bond_id': b['bond_id'], 'cusip': np.nan, 'match_method': 'no_coupon_maturity_candidates',
               'match_score': np.nan, 'n_candidates': n_cands.get(i, 0), 'n_name_matches': 0,
               'finra_issuer_name': np.nan, 'is_144a': np.nan, 'moodys_rating': np.nan, 'sp_rating': np.nan}
        rec['n_name_matches'] = sum(1 for p in pairs if p[0] == i and p[2] >= min_similarity)
        if i in assigned_bond:
            p, method = assigned_bond[i]
            row = m.loc[p[1]]
            rec.update(cusip=row['cusip'], match_score=float(p[2]), match_method=method,
                       finra_issuer_name=row['issuerName'], is_144a='Y' if row['_is144a'] else 'N',
                       moodys_rating=row.get('moodysRating', np.nan),
                       sp_rating=row.get('standardAndPoorsRating', np.nan))
        elif n_cands.get(i, 0) > 0:
            rec['match_score'] = float(best_sim.get(i, 0.0))
            rec['match_method'] = ('cusip_taken_by_better_match' if rec['n_name_matches'] > 0
                                   else 'name_below_threshold')
        rows.append(rec)
    return pd.DataFrame(rows)


def aggregate_trades(trades, window_start, window_end, volume_cap=None):
    """Per-CUSIP activity metrics from trade-level prints.

    trades: DataFrame with cusip, tradeExecutionDate, reportedTradeVolume and optionally
            tradeStatus ('M' trade / 'N' cancel / 'O' correction) and contraPartyTypeCode
            ('C' customer, 'D' dealer, 'A' affiliate, 'T' ATS).
    volume_cap: optional per-print ceiling for sources whose volume is *uncapped* (WRDS
            TRACE Enhanced). Prints at or above it are winsorised to the cap and counted
            in n_capped_trades, so a handful of fat-finger $10B entries cannot dominate a
            bond's summed volume.
    Cancels are dropped; corrections are kept as the surviving version of the print.
    Volume is a *floor* because of the public feed's caps; n_capped_trades says how often
    the cap bound. Only CUSIPs present in `trades` appear -- the caller adds zeros for
    matched bonds with no prints (zero trades is a real, maximally-illiquid observation).
    Contra-party counts are NaN (not zero) when the source carries no contra-party field.
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
    if volume_cap is not None:
        over = t['_vol'] >= volume_cap
        t.loc[over, '_vol'] = float(volume_cap)
        t['_capped'] = t['_capped'] | over
    has_cp = 'contraPartyTypeCode' in t
    cp = t['contraPartyTypeCode'].astype(str).str.upper() if has_cp else pd.Series('', index=t.index)
    t['_cust'] = cp.eq('C')
    t['_dealer'] = cp.isin(['D', 'T', 'A'])

    g = t.groupby('cusip')
    out = pd.DataFrame({
        'n_trades': g.size(),
        'n_capped_trades': g['_capped'].sum().astype(int),
        'total_volume_floor': g['_vol'].sum(),
        'days_traded': g['tradeExecutionDate'].nunique(),
        'n_customer_trades': g['_cust'].sum().astype(int) if has_cp else np.nan,
        'n_dealer_trades': g['_dealer'].sum().astype(int) if has_cp else np.nan,
        'median_trade_size': g['_vol'].median(),
    }).reset_index()
    out.insert(1, 'window_start', ws.strftime('%Y-%m-%d'))
    out.insert(2, 'window_end', we.strftime('%Y-%m-%d'))
    return out


WRDS_COLUMNS = ['cusip_id', 'trd_exctn_dt', 'trc_st', 'entrd_vol_qt', 'rptd_pr']
_WRDS_MATCH_KEY = ['cusip_id', 'trd_exctn_dt', 'entrd_vol_qt', 'rptd_pr']


def clean_wrds_enhanced(df):
    """Turn a WRDS TRACE Enhanced (trace_enhanced.trace_enhanced) pull into the print
    schema aggregate_trades expects, applying the trade-status cleaning.

    df: DataFrame with cusip_id (str), trd_exctn_dt, trc_st, entrd_vol_qt, rptd_pr.
        Extra columns (bond_sym_id, company_symbol) are ignored.

    Cleaning (a reduced Dick-Nielsen 2014: the pull carries no msg_seq_nb, so cancels
    are matched on attributes rather than sequence number):
      T  normal trade report          -> the base set of prints
      X  same-day cancel              -> removes ONE T print with identical
      R  reversal of a prior report      cusip / execution date / volume / price;
                                         unmatched (original before the window) -> dropped
      C  correction                   -> dropped; the original T print stays as the single
                                         count-bearing record (counts and days are unaffected,
                                         only the corrected size/price is lost)
      Y  and anything else            -> dropped
    Returns (prints, stats). prints has cusip, tradeExecutionDate, reportedTradeVolume
    (float, uncapped), lastSalePrice, tradeStatus (all 'M'). stats records every count.
    """
    missing = [c for c in WRDS_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f'WRDS pull lacks columns {missing}; expected {WRDS_COLUMNS}')
    t = df[WRDS_COLUMNS].copy()
    t['cusip_id'] = t['cusip_id'].astype(str).str.strip().str.upper()
    t['trc_st'] = t['trc_st'].astype(str).str.strip().str.upper()
    t['entrd_vol_qt'] = pd.to_numeric(t['entrd_vol_qt'], errors='coerce')
    t['rptd_pr'] = pd.to_numeric(t['rptd_pr'], errors='coerce')
    by_status = t['trc_st'].value_counts().to_dict()

    base = t[t['trc_st'] == 'T'].copy()
    base['_n'] = base.groupby(_WRDS_MATCH_KEY, dropna=False).cumcount()   # k-th identical print

    def _match(status):
        s = t[t['trc_st'] == status].copy()
        s['_n'] = s.groupby(_WRDS_MATCH_KEY, dropna=False).cumcount()
        hit = s.merge(base[_WRDS_MATCH_KEY + ['_n']], on=_WRDS_MATCH_KEY + ['_n'], how='left', indicator=True)
        return s, int((hit['_merge'] == 'both').sum())

    cancels, cancels_matched = _match('X')
    reversals, reversals_matched = _match('R')
    void = pd.concat([cancels, reversals], ignore_index=True)
    # each X/R row knocks out the k-th identical T print, so a genuine duplicate print survives
    void['_n'] = void.groupby(_WRDS_MATCH_KEY, dropna=False).cumcount()
    keep = base.merge(void[_WRDS_MATCH_KEY + ['_n']], on=_WRDS_MATCH_KEY + ['_n'], how='left', indicator=True)
    keep = keep[keep['_merge'] == 'left_only']

    prints = pd.DataFrame({
        'cusip': keep['cusip_id'].values,
        'tradeExecutionDate': keep['trd_exctn_dt'].values,
        'reportedTradeVolume': keep['entrd_vol_qt'].astype(float).values,
        'lastSalePrice': keep['rptd_pr'].values,
        'tradeStatus': 'M',
    })
    stats = {
        'rows_in': int(len(t)),
        'by_status': {k: int(v) for k, v in by_status.items()},
        'cancels_matched': cancels_matched,
        'cancels_unmatched': int(len(cancels)) - cancels_matched,
        'reversals_matched': reversals_matched,
        'reversals_unmatched': int(len(reversals)) - reversals_matched,
        'corrections_dropped': int(by_status.get('C', 0)),
        'other_status_dropped': int(sum(v for k, v in by_status.items() if k not in ('T', 'X', 'R', 'C'))),
        'originals_removed': int(len(base) - len(prints)),
        'rows_out': int(len(prints)),
    }
    return prints, stats


ACTIVITY_COLUMNS = ['cusip', 'window_start', 'window_end', 'n_trades', 'n_capped_trades',
                    'total_volume_floor', 'days_traded', 'n_customer_trades', 'n_dealer_trades',
                    'median_trade_size']
