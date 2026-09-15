# Liquidity-Aware Sampling of a Corporate Bond ETF Portfolio

## Motivation

Corporate bond ETFs often track indices containing hundreds or thousands of individual bonds. Full replication is expensive and often impractical because many bonds trade infrequently and incur higher transaction costs. Instead, portfolio managers construct a representative *sample* that closely matches the benchmark's risk characteristics while maintaining sufficient liquidity.

This project studies that trade-off using **only publicly available data**.

## Research Question

How does incorporating liquidity constraints affect the construction of a sampled corporate bond ETF portfolio? Specifically, what is the trade-off between portfolio liquidity and the accuracy with which the sampled portfolio replicates the benchmark's characteristics?

## Benchmark

**LQD** — iShares iBoxx $ Investment Grade Corporate Bond ETF. As of the data used here (July 22, 2026): 3,143 bond holdings across 591 issuers.

## Data sources (all free, no login required)

| File | Source | Used for |
|---|---|---|
| LQD holdings | [iShares LQD product page](https://www.ishares.com/us/products/239566/ishares-iboxx-investment-grade-corporate-bond-etf) → "Data Download" | The benchmark portfolio |
| QLTA holdings | [iShares QLTA product page](https://www.ishares.com/us/products/239431/ishares-aaa-a-rated-corporate-bond-etf) → "Data Download" | Rating proxy (Aaa–A bonds) |
| LQDB holdings | [iShares LQDB product page](https://www.ishares.com/us/products/318688/ishares-bbb-rated-corporate-bond-etf) → "Data Download" | Rating proxy (BBB bonds) |

These download as `.xls` but are actually SpreadsheetML XML, not binary Excel — `scripts/00_parse_ishares_holdings.py` parses them directly (see below).

**Note:** the download files are served from `blackrock.com`, which may not be reachable from a sandboxed/automated environment. Download manually via the links above (click "Data Download" near the top of each product page) and place them in `data/raw/`.

### Key data gaps and how each was resolved

| Field | Gap | Resolution |
|---|---|---|
| CUSIP / ISIN | Not in the public holdings file | Bonds identified via composite key: `Issuer Name + Coupon + Maturity Date` |
| Credit rating | No rating field at all | Cross-matched against QLTA (Aaa-A) and LQDB (BBB) holdings — see below |
| Issue size / amount outstanding | Only the ETF's own position size (Par Value) is available | Par Value used as a liquidity **proxy**, explicitly caveated — not a true issue-size or trading-activity measure |
| Callable status | "Yield to Call" column exists but is blank for every LQD holding | Not used |

**Rating proxy methodology:** LQD's fact sheet publishes a fund-level rating distribution (AAA 1.03% / AA 9.05% / A 47.93% / BBB 40.47% / cash 1.52%), but no bond-level rating. To recover a bond-level proxy, each LQD bond is matched (by issuer + coupon + maturity) against the holdings of QLTA (only holds Aaa-A rated corporates) and LQDB (only holds BBB rated corporates) — both run by BlackRock on closely related index methodologies. A direct match labels the bond `A-or-better` or `BBB`; unmatched bonds are inferred from their issuer's other bonds. This recovers **59.6% A-or-better / 37.6% BBB / 2.8% Unknown**, versus the official 58.0% / 40.5% / 1.5% cash — within ~1-3 points, from entirely public data.

## Methodology

1. **Benchmark construction** — aggregate the full LQD holdings into target characteristics: sector allocation, maturity profile, rating distribution, duration, yield, and issuer concentration.
2. **Portfolio sampling** — build reduced portfolios of N = 25, 50, 100, 200 bonds using three methods:
   - **Largest Holdings** — top N bonds by weight (baseline)
   - **Stratified Sampling** — proportional allocation across Sector × Rating × Maturity strata
   - **Optimization-Based Sampling** — a quadratic program minimizing weighted deviation from benchmark characteristics, subject to full investment, long-only, an issuer concentration cap, and a fixed holdings count (solved via a relax-and-round heuristic — see script docstring for details, since exact cardinality constraints are intractable at this universe size)
3. **Incorporating liquidity** — extend the optimizer with a soft penalty (`λ × weighted average liquidity score`) and sweep λ to trace an accuracy/liquidity frontier.
4. **Performance comparison** *(done)* — head-to-head comparison of the pure-tracking portfolio (λ=0) against a liquidity-tilted portfolio (λ=1) at N = 50, 100, 200, across every dimension in the proposal: rating exposure, sector exposure, duration, yield, issuer concentration, liquidity profile, and characteristic mismatch. Both books come from the same optimizer, so the contrast isolates the effect of adding liquidity considerations. Tables and charts are in `output/step4/`. N=25 is excluded (see limitations). Headline finding (N=100, λ=0 → λ=1): the weighted-average liquidity score rises from 0.85 to 0.91 and yield holds up (5.61% → 5.65%), but sector fidelity and issuer concentration pay for it — sector L1 mismatch widens from 1.2 to 8.4 pp, top-10 issuer weight from 26.4% to 30.0%. Rating exposure barely moves.
5. **Trade-off analysis** *(done)* — directly answers the four questions in the original proposal using the full λ grid: (1) accuracy lost to liquidity, via a Pareto-efficient frontier per dimension — now smooth for the liquidity-score axis at every N, since the densified grid gave the underlying trend enough points to separate from sweep noise; (2) yield impact — stays within 16bp (N=50) to 8bp (N=100/200) of benchmark YTM across the entire grid; (3) hardest characteristics to preserve, ranked with a correlation-based confidence check (so an off-frontier noise point can't set the ranking) — issuer concentration and sector fidelity are the most *reliably* the biggest casualties, rating/maturity/duration trend the same direction but noisily; (4) how the trade-off scales with N — inconclusive even after densifying the grid: an OLS slope of sector mismatch vs. liquidity score has R²<0.5 at every N, so no confident claim about smaller N being a "worse" trade-off is supported by this data. Tables and charts are in `output/step5/`.

6. **Liquidity-proxy validation** *(in progress — data-access decision pending)* — tests the reviewer's critique that par holding size measures **issue size**, not **tradability**. Two reads: (A) a *size* read that is always computable from the data already in the repo — the same bond's position in QLTA or LQDB, a different fund sizing the same issue; (B) an *activity* read from FINRA TRACE — trade count, distinct days traded and (capped) volume per CUSIP over 2026-04-22 → 2026-07-22. `scripts/06_liquidity_proxy_validation.py` runs read A today and read B as soon as `data/lqd_cusip_crosswalk.csv` and `data/trace_activity_raw.csv` exist (schema in `scripts/trace_utils.py`). **Why B is not done yet:** the only free, programmatic per-bond TRACE source found is the JSON API behind FINRA's public Fixed Income Data site, and FINRA's user agreement for that site forbids automated copying and any redistribution — so a 3,143-bond pull committed to a public repo is not permissible without a licensed source or a manual-export path. The investigation, the options and the input contract are written up in [`docs/superpowers/specs/2026-09-15-trace-liquidity-measure-design.md`](docs/superpowers/specs/2026-09-15-trace-liquidity-measure-design.md). Current read-A result (`output/step6/step6_summary.json`): LQD par holding vs the same bond's par share in QLTA/LQDB has Spearman ρ = 0.53 over 2,566 bonds, and it splits by fund — 0.74 within the 1,714 QLTA (A-or-better) bonds but only 0.33 within the 852 LQDB (BBB) bonds. Even two BlackRock funds' positions in the *same* bond agree only moderately, so par holding size is a noisy read of issue size itself, before tradability even enters.

Full narrative write-up of Steps 1–3, including known limitations and edge cases, is in [`methodology_notes.docx`](methodology_notes.docx) (or see `docs/` if converted to Markdown).

## Repository structure

```
data/
  raw/                        # original iShares "Data Download" files (.xls, actually XML)
  *.csv                        # parsed holdings (output of 00_parse_ishares_holdings.py)
scripts/
  00_parse_ishares_holdings.py # parses iShares' XML-as-.xls files into clean CSVs
  portfolio_utils.py           # shared: load data, compute benchmark targets, evaluate portfolios
  01_benchmark_construction.py # Step 1
  02_portfolio_sampling.py     # Step 2: three sampling methods x four portfolio sizes
  03_liquidity_optimization.py # Step 3: liquidity-penalty sweep (paths are relative to REPO ROOT, not scripts/ -- see Running it)
  04_performance_comparison.py # Step 4: with vs. without liquidity comparison tables
  04b_comparison_charts.py     # Step 4: renders the comparison charts
  05_tradeoff_analysis.py      # Step 5: trade-off frontier analysis, answers the four proposal questions
  06_liquidity_proxy_validation.py # Step 6: does par size track issue size or trading activity? (see above)
  trace_utils.py               # shared: LQD -> CUSIP matching, TRACE trade aggregation (source-agnostic)
tests/
  test_trace_utils.py          # pytest unit tests for trace_utils (python -m pytest tests)
docs/superpowers/specs/        # design / decision records (Step 6 data-access investigation lives here)
output/                        # generated by the scripts above
  benchmark_summary.json
  benchmark_targets.json
  step2_sampling_summary.csv
  step3_liquidity_sweep.csv
  portfolios/                  # per-method, per-N sampled portfolio weights
  liquidity/                   # per-N, per-lambda liquidity-aware portfolio weights
  step4/                       # Step 4 comparison tables, summary JSON, and charts/
    step4_headline_comparison.csv
    step4_sector_exposure.csv
    step4_rating_exposure.csv
    step4_maturity_exposure.csv
    step4_full_lambda_path.csv
    step4_summary.json
    charts/                    # 5 comparison PNGs (mismatch, sector, rating/maturity, frontier, concentration/yield)
  step5/                       # Step 5 trade-off tables, summary JSON, and charts/
    step5_pareto_frontier.csv
    step5_dimension_ranking.csv
    step5_N_scaling.csv
    step5_summary.json
    charts/                    # 4 charts (frontier by dimension, dimension ranking, N scaling, yield path)
  step6/                       # Step 6 proxy-validation tables, summary JSON, and charts/
    step6_proxy_correlations.csv
    step6_bond_level.csv
    step6_summary.json         # headline correlations + plain-language verdict + trace_status
    charts/                    # size read (always); activity read charts appear once TRACE data is present
requirements.txt
```

## Running it

```bash
pip install -r requirements.txt

# 1. Download LQD, QLTA, LQDB holdings from ishares.com into data/raw/ (see links above)

# 2. Parse raw files into clean CSVs
python scripts/00_parse_ishares_holdings.py data/raw/LQD_holdings.xls  data/lqd_holdings_raw.csv
python scripts/00_parse_ishares_holdings.py data/raw/QLTA_holdings.xls data/qlta_holdings_raw.csv
python scripts/00_parse_ishares_holdings.py data/raw/LQDB_holdings.xls data/lqdb_holdings_raw.csv

# 3. Run the pipeline
cd scripts
python 01_benchmark_construction.py
python 02_portfolio_sampling.py
cd .. && python scripts/03_liquidity_optimization.py && cd scripts   # NOTE: run from repo root, see below
python 04_performance_comparison.py   # Step 4 tables (reads Step 2/3 outputs)
python 04b_comparison_charts.py       # Step 4 charts (reads the Step 4 tables)
python 05_tradeoff_analysis.py        # Step 5 trade-off frontier analysis (reads the Step 4 tables)
python 06_liquidity_proxy_validation.py  # Step 6 proxy validation (size read always; activity read if data/trace_* present)
```

Unit tests for the Step 6 helpers: `python -m pytest tests` from the repo root.

Every script writes to `../output/` using paths relative to `scripts/`, so run them from inside `scripts/` as shown — **except `03_liquidity_optimization.py`**, which uses paths relative to the *repo root* (`data/...`, `output/...` with no `../`) and must be run as `python scripts/03_liquidity_optimization.py` from the repo root instead, or it fails with `FileNotFoundError` looking for `scripts/data/...`. This inconsistency is pre-existing in that script and hasn't been unified with the others' `__file__`-relative convention.

## Known limitations

- **Rating proxy** is a two-tier bucket (A-or-better vs. BBB), not a true agency rating; ~2.8% of the portfolio is unclassified.
- **Liquidity proxy** (ETF par holding size) is a stand-in for true amount outstanding / trading activity — the single biggest open data gap in the project.
- **N=25 optimization** (with both rating-matching and the liquidity penalty active) is infeasible: the per-position weight cap collapses to a flat 3% for N<133, and 25 positions × 3% = 75% < 100% can't sum to 1. It's fixable by loosening the issuer/position concentration constraints specifically for small N, but that changes what the constraint set *means* for the study rather than just its numerics — even patched to be feasible, the issuer cap self-adjusts to ~1/(unique issuers) ≈ 4% at N=25, which leaves almost no slack for uneven weighting among the chosen bonds (liquidity tilt would show up almost entirely through *which* bonds are picked, not how they're weighted). Given that, N=25 is excluded from Steps 4–5 and left as a documented limitation rather than patched.
- The liquidity-penalty sweep (λ) is not perfectly monotonic. This isn't sampling noise around a smooth curve — it's the reweighted-L1 relax-and-round heuristic's final `top_n = argsort(w_prior)[:n]` step switching between *discrete* candidate bond sets as λ crosses certain thresholds, so no grid density makes it perfectly continuous. The grid was densified from the original 8 points to 27 (concentrated at low λ, where the trade-off moves fastest) so the plateau/threshold structure resolves clearly instead of a coarse grid landing on an unlucky single point that reads as an extreme outlier — e.g. the original grid's N=50 Rating L1 spike to 4.06pp at λ=1.0 is gone once finer points surround it. Residual jumps in the charts are real (discrete selection switches), not something a finer grid alone erases; Step 5's Pareto-frontier extraction and R² checks are built around that rather than assuming it away.
- `portfolio_utils.compute_rating_buckets()` uses default QLTA/LQDB paths that are relative to the working directory, and silently falls back to all-`Unknown/Ambiguous` ratings if they aren't found. Run the scripts from inside `scripts/` (as documented above), or pass explicit paths, or all rating exposures collapse to Unknown.

## Data as of

July 22, 2026 (LQD), per each fund's most recent "Data Download" at the time of analysis. Holdings are subject to daily change — re-download for a current snapshot.
