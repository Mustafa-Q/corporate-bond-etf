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
4. **Performance comparison** *(done)* — head-to-head comparison of the pure-tracking portfolio (λ=0) against a liquidity-tilted portfolio (λ=1) at N = 50, 100, 200, across every dimension in the proposal: rating exposure, sector exposure, duration, yield, issuer concentration, liquidity profile, and characteristic mismatch. Both books come from the same optimizer, so the contrast isolates the effect of adding liquidity considerations. Tables and charts are in `output/step4/`. Headline finding (N=100, λ=0 → λ=1): the weighted-average liquidity score rises from 0.86 to 0.91 and yield holds up (5.61% → 5.65%), but sector fidelity and issuer concentration pay for it — sector L1 mismatch widens from 2.6 to 8.4 pp, top-10 issuer weight from 25.3% to 30.0%. Rating exposure barely moves. N=25 is excluded from the comparison because the liquidity optimizer is infeasible there (see limitations).
5. **Trade-off analysis** *(in progress)* — directly answer the four questions in the original proposal (accuracy lost to liquidity, yield impact, hardest characteristics to preserve, how holdings count scales with tighter constraints).

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
  03_liquidity_optimization.py # Step 3: liquidity-penalty sweep
  04_performance_comparison.py # Step 4: with vs. without liquidity comparison tables
  04b_comparison_charts.py     # Step 4: renders the comparison charts
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
python 03_liquidity_optimization.py
python 04_performance_comparison.py   # Step 4 tables (reads Step 2/3 outputs)
python 04b_comparison_charts.py       # Step 4 charts (reads the Step 4 tables)
```

Each script writes its outputs to `../output/` (paths are relative to `scripts/`), so run them from inside `scripts/` as shown.

## Known limitations

- **Rating proxy** is a two-tier bucket (A-or-better vs. BBB), not a true agency rating; ~2.8% of the portfolio is unclassified.
- **Liquidity proxy** (ETF par holding size) is a stand-in for true amount outstanding / trading activity — the single biggest open data gap in the project.
- **N=25 optimization** (with both rating-matching and the liquidity penalty active) can hit solver infeasibility from the combination of issuer cap + position floor/cap constraints, and falls back to equal weighting. Treat N=25 optimization results with caution until this is tightened.
- The liquidity-penalty sweep (λ) is not perfectly monotonic — an artifact of the reweighted-L1 relax-and-round heuristic responding differently at each λ. A finer grid would smooth this.
- `portfolio_utils.compute_rating_buckets()` uses default QLTA/LQDB paths that are relative to the working directory, and silently falls back to all-`Unknown/Ambiguous` ratings if they aren't found. Run the scripts from inside `scripts/` (as documented above), or pass explicit paths, or all rating exposures collapse to Unknown.

## Data as of

July 22, 2026 (LQD), per each fund's most recent "Data Download" at the time of analysis. Holdings are subject to daily change — re-download for a current snapshot.
