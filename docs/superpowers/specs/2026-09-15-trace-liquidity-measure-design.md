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
