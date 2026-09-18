# Phase 2 — a TRACE-based liquidity score for the optimiser, and what it changes

*Written 2026-09-17 on branch `trace-liquidity-measure`. Follows Phase 1 in
`2026-09-15-trace-liquidity-measure-design.md` §5, which found LQD par holding size ranks
TRACE trading activity only moderately (Spearman ρ ≈ 0.3 with days traded / trade count).*

## 1. Goal

Replace the optimiser's liquidity score (normalised log par holding) with one built from real
TRACE activity, re-run the λ sweep and the Step 4/5 analyses under it in parallel outputs, and
answer one question directly: **did the par-based liquidity tilt actually buy tradability, or
just size?**

Decisions taken with the user on 2026-09-17: score = log trade count; bonds without full-window
activity are imputed from peers; outputs land in parallel, the par-score results stay.

## 2. The TRACE score (`portfolio_utils.build_trace_liquidity_score`)

Inputs: the bond universe (with `bond_id`, `Sector`, `Maturity_Bucket`, `Effective Date`),
`data/lqd_cusip_crosswalk.csv`, `data/trace_activity_raw.csv`.

Raw activity `trace_raw_count` per bond, and a `Liquidity_Score_Source` flag:

| window exposure | raw count | source flag |
|---|---|---|
| outstanding for the full window | `n_trades` (0 for the 11 silent bonds) | `trace_full_window` |
| issued inside the window, ≥ 10 trading days of exposure | `n_trades / exposure_days × window_days` | `trace_scaled` |
| issued inside the window, < 10 days exposure; issued after the window; no CUSIP | median raw count of `trace_full_window` bonds in the same Sector × Maturity_Bucket (≥ 5 bonds), else the sector median (≥ 5), else the universe median | `imputed_sector_maturity` / `imputed_sector` / `imputed_universe` |

Exposure days = US business days (federal-holiday calendar, as in Step 6) from the Effective
Date to the window end, inclusive; window days = the same count over the whole window (63).

Score: `Liquidity_Score_TRACE = (log1p(raw) − min) / (max − min)` over the universe, 0–1,
higher = more liquid — the same transform the par score uses, so λ values are comparable in
spirit (not in magnitude: the two scores have different spreads, which is why the comparison
in §4 is done at matched λ *and* along the whole frontier).

`load_bonds(path, include_rating=True, score='par')` gains the `score` argument. Both
`Liquidity_Score_Par` and `Liquidity_Score_TRACE` are always present when the TRACE inputs
exist; `Liquidity_Score` is whichever `score` selects. `score='trace'` without the inputs raises
a clear error rather than silently falling back. TRACE inputs are found next to the holdings
file (same `data/` directory), so the repo-root vs `scripts/` working-directory difference
between Step 3 and the others keeps working.

## 3. The switch (Steps 3, 4, 4b, 5)

Each script takes `--score {par,trace}` (default `par`, reproducing today's outputs bit for
bit). A helper `portfolio_utils.output_paths(score)` returns the directory/file names:

| artefact | par | trace |
|---|---|---|
| per-portfolio weights | `output/liquidity/` | `output/liquidity_trace/` |
| sweep summary | `output/step3_liquidity_sweep.csv` | `output/step3_liquidity_sweep_trace.csv` |
| Step 4 tables and charts | `output/step4/` | `output/step4_trace/` |
| Step 5 tables and charts | `output/step5/` | `output/step5_trace/` |

Nothing else changes: λ grid, N ∈ {50, 100, 200}, constraints, the N=25 exclusion, the
rating proxy. `evaluate_portfolio` keeps reporting `weighted_avg_liquidity_score` on the
active score. Step 7 does not need extra columns in the saved portfolios: it reconstructs each
weight vector from `bond_id` + `Sampled_Weight` and takes both scores from `load_bonds`.

## 4. The comparison (`07_score_comparison.py` → `output/step7/`)

Reads both sweeps' portfolio files and both bond scores. For every (score used to optimise,
N, λ) it computes the weighted-average **par** score and the weighted-average **TRACE** score
of the book, plus the Step 4 mismatch dimensions.

Outputs:

- `step7_cross_evaluation.csv` — the full grid: `optimised_on, target_n, lambda_liq,
  wavg_par_score, wavg_trace_score, sector_L1_pp, rating_L1_pp, maturity_L1_pp,
  duration_diff, ytm, ytm_diff_pp, top10_issuer_pct, issuer_hhi, imputed_weight_share`.
- `step7_headline.csv` — at λ = 0 and λ = 1 for each N: the two books side by side, with the
  gain in TRACE score that the par tilt delivered versus the gain the TRACE tilt delivered, and
  what each cost in sector L1 and top-10 issuer weight.
- `step7_summary.json` — headline numbers and a plain `verdict`, e.g. "at N=100, λ=1, the
  par-optimised book raised its TRACE activity score by X vs λ=0 while the TRACE-optimised
  book raised it by Y at a sector cost of A vs B pp".
- `charts/chart1_frontier_par_vs_trace.png` — sector L1 mismatch (x) against weighted TRACE
  score (y), one line per optimisation score, one panel per N. Same palette as Steps 4–6.
- `charts/chart2_score_gain_at_lambda1.png` — bars: TRACE-score gain at λ=1 per N for the two
  books.

Verdict rule (fixed here, before the numbers): if the TRACE-optimised book's TRACE-score gain
at λ=1 exceeds the par-optimised book's by more than 50% (relative) at every N, the par tilt
"bought size, not tradability"; if within ±25% at every N, "the par tilt captured most of the
available tradability"; otherwise "mixed", reported per N.

## 5. Tests

- `build_trace_liquidity_score`: full-window count used as is; a silent full-window bond
  scores 0 raw; scaled bonds use exposure days and the 10-day guard; each imputation fallback
  level fires in order; scores are 0–1 with min and max hit; the source flag is set for every
  bond; missing TRACE inputs raise with `score='trace'`.
- `output_paths`: both scores map to the table in §3.
- `07`: cross-evaluation on a tiny synthetic sweep gives the expected weighted averages.

## 6. Out of scope

Changing the optimiser, the λ grid, N=25, or the rating proxy. Pulling amount outstanding.
Any re-run of Step 2 (no liquidity term there).

## 7. Result (2026-09-17)

Score construction on the real universe: 2,604 `trace_full_window`, 139 `trace_scaled`,
392 `imputed_sector_maturity`, 4 `imputed_sector`, 4 `imputed_universe`; imputed bonds carry
15.9% of benchmark weight. Spearman rank correlation between the par and TRACE scores across
the 3,143 bonds: 0.28. Scaled bonds have a median raw count ~3× the full-window median (new
issues trade heavily in their first weeks), a known upward bias for those 139 bonds.

Cross-evaluation at λ = 0 → λ = 1 (`output/step7/step7_headline.csv`):

| N | book | TRACE-score gain | par-score gain | sector L1 cost (pp) | top-10 issuer cost (pp) | imputed weight at λ=1 |
|---|---|---|---|---|---|---|
| 50 | par-optimised | +0.028 | +0.037 | +2.6 | 0.0 | 22% |
| 50 | TRACE-optimised | +0.076 | +0.022 | +0.8 | 0.0 | 16% |
| 100 | par-optimised | +0.003 | +0.067 | +7.2 | +3.6 | 24% |
| 100 | TRACE-optimised | +0.084 | +0.033 | +9.7 | +3.6 | 8% |
| 200 | par-optimised | +0.034 | +0.078 | +5.6 | +13.2 | 28% |
| 200 | TRACE-optimised | +0.126 | +0.030 | +5.2 | +13.2 | 5% |

Verdict under the §4 rule: **bought size, not tradability** — the TRACE tilt's activity gain
exceeds the par tilt's by far more than 50% at every N, at a comparable cost. Caveat: the par
tilt concentrates in large new issues whose TRACE score is an imputed peer median, so part of
its flat activity reading is the imputation being neutral; that is itself a statement about
what the par proxy rewards.

Runtime: the TRACE sweep takes ~45 s; Steps 4–5 and 7 a few seconds each.
