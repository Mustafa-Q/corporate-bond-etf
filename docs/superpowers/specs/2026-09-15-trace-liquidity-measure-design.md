# TRACE-based liquidity measure — design and data-access decision record

*Written 2026-09-15 on branch `trace-liquidity-measure`. Companion to `NEXT_STEPS_liquidity_proxy.md`
(the workstream brief). This file records what was found about data access, the decisions that
follow from it, and the design of the Phase 1 validation study.*

## 1. Goal (from the brief)

Test the reviewer's critique of the project's liquidity proxy: the ETF's **par holding size** may
track **issue size**, not **tradability**. Get a real per-bond trading-activity measure from FINRA
TRACE, measure how weakly par size tracks it, and only then decide whether to rebuild the
liquidity score (Phases 2–3).

## 2. What the data-access investigation found

All probing was done on 2026-09-15 with a few dozen exploratory requests; nothing was bulk-pulled
and nothing from FINRA is committed to the repo.

### 2.1 The public FINRA Fixed Income Data site exposes exactly what the study needs

FINRA's public site (`finra.org/finra-data/fixed-income/...`) is driven by a JSON reporting API
at `services-dynarep.ddwa.finra.org/public/reporting/v2/...`. It needs no account: a GET of a
template sets a session cookie and an `XSRF-TOKEN` cookie, and data POSTs succeed when that token
is echoed back as an `X-XSRF-TOKEN` header. Two datasets matter:

| Dataset (`group/FixedIncomeMarket/name/...`) | Granularity | Fields that matter | Size / limits |
|---|---|---|---|
| `CorporateAndAgencySecurities` (security master) | one row per CUSIP | `cusip`, `issuerName`, `couponRate`, `maturityDate`, `is144A`, `moodysRating`, `standardAndPoorsRating`, `lastTradeDate`, `productSubTypeCode` | 202,604 `CORP` rows; pages of 20,000 work; only exact-match filters (no `CONTAINS`) |
| `CorporateAndAgencyTradeHistory` (TRACE trade prints) | one row per disseminated trade | `cusip`, `tradeExecutionDate`, `tradeExecutionTime`, `reportedTradeVolume`, `lastSalePrice`, `lastSaleYield`, `reportingPartySideCode`, `contraPartyTypeCode` (D dealer / C customer / A affiliate / T ATS), `tradeStatus` (M trade / N cancel / O correction), `isAsof`, `atsFlag`, `tradeModifier2Code` (P portfolio trade) | filter by `cusip` + `dateRangeFilters` on `tradeExecutionDate`; 5,000-row pages confirmed; ~3 years of history |

Two facts about the trade data that shape the metrics:

- **Volume is capped at dissemination**, as in every public TRACE feed: investment-grade prints
  above $5MM appear as the string `5MM+` (high-yield: `1MM+`). Trade **count** and **days traded**
  are unaffected; volume must be treated as a floor and the number of capped prints reported.
- One active bond (META 3.5% 2027, CUSIP 30303M8G0) had 2,053 prints on 63 distinct days in the
  proposed window 2026-04-22 → 2026-07-22, i.e. every trading day. Cancels/corrections were 3 of
  2,053.

There is **no amount-outstanding field anywhere in the public FINRA data**, and no free bulk
per-CUSIP amount-outstanding source was found (Cbonds/WRDS/Bloomberg are licensed; Terrapin is a
one-at-a-time lookup). Phase 1a as written in the brief is therefore not achievable from free data.

### 2.2 But the site's terms prohibit exactly the pull the brief asks for

FINRA's *Fixed Income Data User Agreement* (finra.org/finra-data/fixed-income/user-agreement):

> You are not permitted to: 1) duplicate or download the Data other than for your own personal
> non-commercial use; 2) use any robot, spider, other automatic or manual process to monitor or
> copy the Data; or 3) distribute the Data.

Pulling 3,143 bonds' trade histories programmatically is (2); committing the pull to a public
GitHub repository, as the brief's "store the raw pull under `data/` and commit it" instructs, is
(3). The other routes to per-bond TRACE activity are all licensed:

| Route | What it gives | Access |
|---|---|---|
| TRACE Security Activity Report (TSAR) | monthly per-security volume, trade count, days traded — the ideal input | paid FINRA subscription |
| TRACE Enhanced Historical (incl. via WRDS) | trade-level, uncapped volume | institutional licence (WRDS subscription) |
| Academic Corporate Bond TRACE Data | trade-level, 36-month lag | institution applies directly to FINRA, fees apply; the lag rules out a 2026 window |
| FINRA Developer API (`api.finra.org`) | market-aggregate / report-card datasets only | free key, but nothing per-CUSIP |
| **Manual export from the public site** | the site's *Corporate and Agency Trade Activity* view (`FixedIncomeMarket/CorporateAndAgencyTradeActivity`) lists every disseminated print market-wide with a date filter: ~16k prints/day, 1,003,124 prints over 2026-04-22 → 2026-07-22 | **No export control is shown to anonymous visitors** (toolbar is Glossary / Columns / Filter only; the widget's export lands in an "Exports Ready for Download" panel that exists only in the logged-in report centre, and the grid's right-click menu is restricted to copy). Whether FINRA's free Data/Watchlist login enables it is unverified. Redistribution barred either way |

**Decision surfaced to the user (blocking for data acquisition, not for the code):** which route to
take. The pipeline below is written against a source-agnostic input schema so the choice only
changes the acquisition step.

## 3. Design

### 3.1 Input contract (source-agnostic)

Two files under `data/`, produced by whichever acquisition route is chosen:

- `data/lqd_cusip_crosswalk.csv` — `bond_id, cusip, match_method, match_score, n_candidates,
  finra_issuer_name, is_144a, moodys_rating, sp_rating` (rating columns optional; kept because
  they are free once a CUSIP is known and are a future check on the rating proxy).
- `data/trace_activity_raw.csv` — per-CUSIP activity over the window: `cusip, window_start,
  window_end, n_trades, n_capped_trades, total_volume_floor, days_traded, n_customer_trades,
  n_dealer_trades, median_trade_size`. A bond with a CUSIP but no prints appears with zeros
  (zero trades = maximally illiquid, not missing). A bond with no CUSIP does not appear.

**Decision (2026-09-15, user):** the trade prints come from a **manual export** of FINRA's public
*Corporate and Agency Trade Activity* page (personal, non-commercial use, no robot), dropped under
`data/trace_export/` (gitignored) and converted by `00c_parse_finra_trade_export.py`, which keeps
only the crosswalk's CUSIPs, de-duplicates overlapping exports, and writes
`data/trace_activity_raw.csv`. No FINRA-derived file is committed; only `output/step6/` results are.

### 3.2 CUSIP matching (`trace_utils.match_lqd_to_master`, driven by `00b_build_cusip_crosswalk.py`)

**Decision (2026-09-15): the CUSIP source is LQD's own Form N-PORT on EDGAR, not FINRA's
security master.** SEC filings are public-domain, so nothing about the crosswalk is licence-bound.
The filing used is iShares Trust accession 0001410368-26-075235 (filed 2026-07-24, period
2026-05-31, series S000004361): 3,135 debt holdings, every one with CUSIP, ISIN, coupon, maturity
and par balance. It predates the 2026-07-22 holdings snapshot by seven weeks, so bonds bought after
May 31 (new issues such as SpaceX, Nvidia, Amazon lines) get no CUSIP from this route.

Matching: candidates share maturity and coupon within ±0.015 (the two files round 5.805% to 5.8
and 5.81 respectively), ranked by issuer-name similarity after normalisation (strip `/THE`, `MTN`,
`(FXD-FRN)`, `144A`, legal suffixes; tokens count as shared when equal or prefix-equivalent, since
the filing abbreviates `CAPITAL` → `CAP`, `COMMUNICATIONS` → `COMM`). Because the N-PORT describes the
same portfolio, a lone candidate whose par balance sits within the typical N-PORT/iShares ratio band
is accepted even when the name is unrecognisable (`IBM CORP` vs `INTERNATIONAL BUSINESS MACHINES`).
CUSIPs are assigned one-to-one, best pair first. Result on the real data:

| match_method | bonds | share |
|---|---|---|
| coupon_maturity_name | 2,971 | 94.5% |
| coupon_maturity_size | 91 | 2.9% |
| no_coupon_maturity_candidates (mostly post-May-31 issues) | 73 | 2.3% |
| name_below_threshold | 8 | 0.3% |

Unmatched bonds stay in the crosswalk with `cusip` empty and are excluded from the activity
correlations, counted explicitly in `step6_match_report.csv`.

### 3.3 Activity metrics (`trace_utils.aggregate_trades`)

From trade-level rows: drop `tradeStatus == 'N'` (cancels); keep corrections; `5MM+`/`1MM+` →
numeric floor with a capped flag. Metrics: `n_trades`, `days_traded`, `total_volume_floor`,
`n_capped_trades`, customer vs dealer counts, median size. Days-traded and trade count are the
headline measures (a single block print ≠ liquid); volume is secondary because of the cap.

### 3.4 The correlation study (`scripts/06_liquidity_proxy_validation.py`, run from `scripts/`)

Spearman rank correlations on the matched LQD universe between:

- par holding size ↔ `n_trades`, `days_traded`, `total_volume_floor` (the reviewer's point);
- par holding size ↔ the best available **size** read. With no amount-outstanding source, the
  substitute is the **same bond's par holding in QLTA or LQDB** (independent funds sizing the same
  issue). If two funds' positions in a bond are far more rank-correlated with each other than
  either is with trading activity, par size is measuring issue size, not tradability.
- the size read ↔ activity.

Plus a "parked issue" check: among the top par-size decile, the share of bonds that traded on
fewer than half the window's trading days.

Outputs to `output/step6/`: `step6_proxy_correlations.csv`, `step6_match_report.csv`,
`step6_summary.json` (headline correlations + plain `verdict`), and a rank-scatter chart with
the correlation annotated. Bonds without a CUSIP are excluded from correlations and counted in
the match report; zero-trade bonds are included at zero.

### 3.5 What is committed

Only derived study outputs (`output/step6/`) and the crosswalk are committed. Trade-level and
per-bond activity files from any FINRA source stay out of git (`data/trace_*` gitignored) until
the user confirms the chosen source's licence permits redistribution.

## 4. Out of scope (unchanged from the brief)

N=25 exclusion, the rating proxy, the 27-point λ grid, and the non-monotonic sweep residuals.

## 5. Phase 1 data landed: WRDS TRACE Enhanced (2026-09-17)

**Decision (2026-09-17, user):** the trade prints come from **WRDS TRACE Enhanced** (table
`trace_enhanced.trace_enhanced`, filtered on `cusip_id` with `data/lqd_cusips.txt`, the 3,062
CUSIPs from the N-PORT crosswalk), not from a manual export of FINRA's public page. This
supersedes the manual-export decision in §3.1; `00c_parse_finra_trade_export.py` stays as the
parser for that route but is not used.

### 5.1 What was pulled

7 columns (`cusip_id, trd_exctn_dt, bond_sym_id, company_symbol, trc_st, entrd_vol_qt, rptd_pr`),
3,760,163 trade rows, execution dates 2025-09-04 → 2025-12-04 (the latest window available under
the ~6-month embargo), 2,756 distinct CUSIPs. The raw file (~206 MB) lives at
`data/raw/wrds_trace_enhanced.csv`, gitignored: WRDS data is licensed and not redistributable.
The per-CUSIP aggregate `data/trace_activity_raw.csv` (2,756 rows, derived statistics) **is**
committed, as are the cleaning statistics in `output/step6/step6_trace_cleaning.json`.

### 5.2 Cleaning (`trace_utils.clean_wrds_enhanced`, driven by `00d_parse_wrds_trace_enhanced.py`)

A reduced Dick-Nielsen (2014): the pull carries no `msg_seq_nb`, so cancels are matched on
attributes rather than sequence number.

| `trc_st` | rows | treatment |
|---|---|---|
| T trade | 3,729,862 | base set of prints |
| X same-day cancel | 10,358 | each removes one T print with identical CUSIP / execution date / volume / price (10,342 matched) |
| R reversal | 9,966 | same matching (7,875 matched; the 2,091 unmatched reverse trades executed before the window and are dropped) |
| C correction | 9,938 | dropped; the original T print stays as the single count-bearing record |
| Y | 39 | dropped |

3,711,731 prints kept. Volume on WRDS is uncapped, but the pull contains fat-finger entries of
$1–10B par (most of them subsequently cancelled). After cancel matching, prints are winsorised at
**$100MM per print** (the 99.99th percentile; 477 prints affected, counted in `n_capped_trades`)
so a handful of entries cannot dominate a bond's summed volume. Days traded and trade count are
the primary measures precisely because they are immune to this.

### 5.3 The universe correction: silent ≠ illiquid

The brief assumed the 306 matched CUSIPs with no prints were "maximally illiquid". They are not:
the holdings snapshot (2026-07-22) post-dates the TRACE window by seven months, and the iShares
*Effective Date* shows **293 of the 306 were issued after 2025-12-04** (they could not have
printed) and 2 inside the window. Only **11** bonds outstanding for the whole window never
printed. Step 6 therefore classifies every matched bond by window exposure and computes the
headline on the **2,604 bonds outstanding for the full window** (159 issued inside the window
and 299 issued after it are excluded; 81 bonds have no CUSIP). The 11 true zero-print bonds enter
at zero, tied at the bottom of the rank. A "naive" sensitivity row keeps every matched bond with
new issues at zero and shows how far that distorts the read (ρ falls from 0.33 to 0.20 on days
traded).

### 5.4 Result

Spearman rank correlation of LQD par holding with TRACE activity, 2,604 full-window bonds:

| measure | ρ | traded-only (n=2,593) |
|---|---|---|
| days traded (primary) | 0.33 | 0.33 |
| trade count (primary) | 0.30 | 0.30 |
| volume, capped (secondary) | 0.47 | 0.47 |

Verdict rule, fixed before the numbers were seen: judged on the better of the two primary
measures, ρ < 0.3 weak, 0.3–0.6 moderate, ≥ 0.6 strong. Result: **moderate (0.33)**. Par holding
size is a partial, noisy read of tradability — the reviewer's critique holds in substance, though
not in the "parked issue" form: among the top par decile only 0.8% of bonds traded on fewer than
half of the 63 trading days (median 64 days traded, vs 62 in the bottom decile). In LQD nearly
every bond prints almost every day; what par size fails to rank is the *intensity* of trading
(median 1,551 prints in the top par decile vs 621 in the bottom). Days traded therefore saturates
and trade count / volume discriminate better at the liquid end.

For context only, the cross-fund size-consistency check (LQD par vs the same bond's par share in
QLTA/LQDB) is ρ = 0.53 — previously mislabelled as a proxy read, now reported as what it is.

### 5.5 Still open

- **Amount outstanding** (FISD/Mergent) — **pulled 2026-09-17**, see §6.
- **Contra-party type** is not in the pull, so customer vs dealer counts are unavailable.
- Whether to rebuild the optimiser's liquidity score on TRACE activity (Phases 2–3) is the
  user's call; nothing in Steps 3–5 was touched.

## 6. The direct size test: Mergent FISD (2026-09-17)

**Decision (user):** commit the FISD pull as `data/fisd_amount_outstanding.csv` (WRDS → Mergent
FISD → Bond Issues, `fisd_mergedissue`, matched on `COMPLETE_CUSIP` from `data/lqd_cusips.txt`,
offering-date range 1990-01 → 2026-09 with "include missing dates" — a first pull with a
narrower date range returned only 1,362 bonds; do not re-narrow it). 3,004 of 3,062 CUSIPs,
offering dates 1993-11 → 2026-05, amounts in $ thousands. `00e_parse_fisd_issue_size.py` writes
`data/fisd_issue_size.csv` in dollars; three issues (`694308JG3/JH1/JJ7`) carry
`AMOUNT_OUTSTANDING == 0` with a real offering amount and are treated as missing so the offering
amount is the fallback. 272 bonds have amount outstanding ≠ offering amount (buybacks / partial
calls); amount outstanding is primary.

Coverage of the 3,062 CUSIP universe: 3,004 with FISD, 2,756 with TRACE prints, **2,714 with
both**, 290 FISD-only (277 issued after the TRACE window, 2 inside it, 11 genuinely silent
full-window bonds), 42 TRACE-only.

Three-way Spearman result (`output/step6/step6_proxy_correlations.csv`):

| pair | ρ | n |
|---|---|---|
| par holding ↔ FISD issue size (amount outstanding, offering fallback) | **0.85** | 3,004 |
| par holding ↔ FISD offering amount | 0.80 | 3,004 |
| par holding ↔ days traded / trade count / capped volume (full-window bonds) | 0.33 / 0.30 / 0.47 | 2,604 |
| FISD issue size ↔ days traded / trade count / capped volume (full-window bonds) | 0.34 / 0.33 / 0.49 | 2,567 |

Par holding is very nearly a rank copy of issue size, and issue size is itself only a moderate
read of tradability. That is the reviewer's claim in its exact form: the par proxy measures
issue size, and issue size ≠ liquidity. Chart 2 now shows the five bars side by side.
