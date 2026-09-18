# TRACE Liquidity Score (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a TRACE-activity liquidity score, re-run the Step 3–5 pipeline under it in parallel outputs, and cross-evaluate both sweeps to show whether the par-based tilt bought tradability.

**Architecture:** `portfolio_utils` gains `build_trace_liquidity_score`, a `score` argument on `load_bonds`, and `output_paths(score)`. Steps 3, 4, 4b, 5 take `--score {par,trace}` and route through `output_paths`. A new `07_score_comparison.py` reads both sweeps and both scores and writes `output/step7/`.

**Tech Stack:** Python 3.11, pandas, numpy, cvxpy (existing), matplotlib, pytest. Spec: `docs/superpowers/specs/2026-09-17-trace-liquidity-score-phase2-design.md`.

## Global Constraints

- `--score par` must reproduce today's outputs bit for bit (verify with `git diff --stat output/` after each re-run).
- Score normalisation: `(log1p(raw) - min) / (max - min)`, 0–1, higher = more liquid.
- Scaled bonds need ≥ 10 trading days of exposure; imputation groups need ≥ 5 full-window bonds.
- Trading days = weekdays minus US federal holidays (`pandas.tseries.holiday.USFederalHolidayCalendar`), same as Step 6.
- Steps 4/4b/5 run from `scripts/`; Step 3 runs from the repo root (pre-existing quirk, keep).
- Keep `cusip` as a string everywhere.
- Commit after each task; commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

### Task 1: `output_paths(score)` in portfolio_utils

**Files:**
- Modify: `scripts/portfolio_utils.py` (add after `MATURITY_LABELS`)
- Test: `tests/test_portfolio_utils_paths.py` (create)

**Interfaces:**
- Produces: `output_paths(score, root='output') -> dict` with keys `weights_dir, sweep_csv, step4_dir, step5_dir` (strings, joined under `root`).

- [ ] **Step 1: Write the failing test**

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import pytest
from portfolio_utils import output_paths

def test_output_paths_par_matches_existing_layout():
    p = output_paths('par', root='output')
    assert p == {'weights_dir': 'output/liquidity', 'sweep_csv': 'output/step3_liquidity_sweep.csv',
                 'step4_dir': 'output/step4', 'step5_dir': 'output/step5'}

def test_output_paths_trace_is_parallel():
    p = output_paths('trace', root='../output')
    assert p == {'weights_dir': '../output/liquidity_trace', 'sweep_csv': '../output/step3_liquidity_sweep_trace.csv',
                 'step4_dir': '../output/step4_trace', 'step5_dir': '../output/step5_trace'}

def test_output_paths_rejects_unknown_score():
    with pytest.raises(ValueError):
        output_paths('volume')
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_portfolio_utils_paths.py -q` → FAIL (ImportError).
- [ ] **Step 3: Implement**

```python
SCORES = ('par', 'trace')

def output_paths(score, root='output'):
    """Where a Step 3-5 run lands. 'par' is the historical layout; 'trace' is the parallel one."""
    if score not in SCORES:
        raise ValueError(f"score must be one of {SCORES}, got {score!r}")
    sfx = '' if score == 'par' else '_trace'
    return {'weights_dir': f'{root}/liquidity{sfx}', 'sweep_csv': f'{root}/step3_liquidity_sweep{sfx}.csv',
            'step4_dir': f'{root}/step4{sfx}', 'step5_dir': f'{root}/step5{sfx}'}
```

- [ ] **Step 4: Run** the test → PASS. **Step 5: Commit** `feat: output_paths helper for par/trace runs`.

---

### Task 2: the TRACE score and `load_bonds(score=)`

**Files:**
- Modify: `scripts/portfolio_utils.py:71-98` (`load_bonds`) and add `build_trace_liquidity_score`, `_trading_days`
- Test: `tests/test_trace_liquidity_score.py` (create)

**Interfaces:**
- Produces: `build_trace_liquidity_score(bonds, data_dir, min_exposure_days=10, min_group=5) -> DataFrame` indexed like `bonds` with columns `trace_raw_count` (float), `Liquidity_Score_TRACE` (0–1), `Liquidity_Score_Source` (one of `trace_full_window, trace_scaled, imputed_sector_maturity, imputed_sector, imputed_universe`). `bonds` must carry `bond_id, Sector, Maturity_Bucket, Effective Date`. Raises `FileNotFoundError` naming the missing input.
- `load_bonds(path, include_rating=True, score='par')`: adds `Liquidity_Score_Par` always; adds the three TRACE columns when the inputs exist next to `path`; sets `Liquidity_Score` from `score`; `score='trace'` without inputs raises.

- [ ] **Step 1: Write the failing tests** (fixture writes a crosswalk + activity file into `tmp_path`)

```python
import os, sys
import numpy as np, pandas as pd, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from portfolio_utils import build_trace_liquidity_score

ACT_HEADER = ('cusip,window_start,window_end,n_trades,n_capped_trades,total_volume_floor,days_traded,'
              'n_customer_trades,n_dealer_trades,median_trade_size\n')

def _inputs(tmp_path, act_rows, xw_rows):
    (tmp_path / 'lqd_cusip_crosswalk.csv').write_text('bond_id,cusip,match_method\n' + ''.join(xw_rows))
    (tmp_path / 'trace_activity_raw.csv').write_text(ACT_HEADER + ''.join(act_rows))

def _bonds(effective, sectors=None, buckets=None):
    n = len(effective)
    return pd.DataFrame({'bond_id': range(n), 'Sector': sectors or ['Banking'] * n,
                         'Maturity_Bucket': buckets or ['5-7yr'] * n,
                         'Effective Date': pd.to_datetime(effective)})

def test_full_window_bond_uses_count_and_silent_bond_is_zero(tmp_path):
    _inputs(tmp_path, ['AAA,2025-09-04,2025-12-04,630,0,1,63,,,1\n'],
            ['0,AAA,m\n', '1,BBB,m\n'])
    out = build_trace_liquidity_score(_bonds(['2020-01-01', '2020-01-01']), str(tmp_path))
    assert out['trace_raw_count'].tolist() == [630.0, 0.0]
    assert out['Liquidity_Score_Source'].tolist() == ['trace_full_window'] * 2
    assert out['Liquidity_Score_TRACE'].tolist() == [1.0, 0.0]

def test_in_window_issue_is_scaled_by_exposure_with_guard(tmp_path):
    # window 2025-09-04..2025-12-04 = 63 trading days. Issued 2025-11-06 -> 19 trading days of exposure (Nov 11 off).
    _inputs(tmp_path, ['AAA,2025-09-04,2025-12-04,19,0,1,19,,,1\n', 'BBB,2025-09-04,2025-12-04,5,0,1,5,,,1\n',
                       'CCC,2025-09-04,2025-12-04,100,0,1,50,,,1\n'],
            ['0,AAA,m\n', '1,BBB,m\n', '2,CCC,m\n'])
    out = build_trace_liquidity_score(_bonds(['2025-11-06', '2025-12-01', '2020-01-01']), str(tmp_path))
    assert out.loc[0, 'trace_raw_count'] == pytest.approx(19 / 19 * 63)
    assert out.loc[0, 'Liquidity_Score_Source'] == 'trace_scaled'
    assert out.loc[1, 'Liquidity_Score_Source'] == 'imputed_universe'   # 4 days of exposure < 10, one peer only

def test_imputation_falls_back_in_order(tmp_path):
    peers = [f'{i},P{i},m\n' for i in range(6)]
    acts = [f'P{i},2025-09-04,2025-12-04,{100 + i},0,1,60,,,1\n' for i in range(6)]
    _inputs(tmp_path, acts + ['Q0,2025-09-04,2025-12-04,10,0,1,10,,,1\n'] * 0, peers + ['6,,no\n', '7,,no\n', '8,,no\n'])
    b = _bonds(['2020-01-01'] * 6 + ['2026-03-01'] * 3,
               sectors=['Banking'] * 5 + ['Energy'] + ['Banking', 'Banking', 'Reits'],
               buckets=['5-7yr'] * 5 + ['5-7yr'] + ['5-7yr', '20yr+', '5-7yr'])
    out = build_trace_liquidity_score(b, str(tmp_path))
    assert out.loc[6, 'Liquidity_Score_Source'] == 'imputed_sector_maturity' and out.loc[6, 'trace_raw_count'] == 102.0
    assert out.loc[7, 'Liquidity_Score_Source'] == 'imputed_sector' and out.loc[7, 'trace_raw_count'] == 102.0
    assert out.loc[8, 'Liquidity_Score_Source'] == 'imputed_universe' and out.loc[8, 'trace_raw_count'] == 102.5

def test_missing_inputs_raise(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_trace_liquidity_score(_bonds(['2020-01-01']), str(tmp_path))

def test_load_bonds_score_switch():
    from portfolio_utils import load_bonds
    root = os.path.join(os.path.dirname(__file__), '..', 'data', 'lqd_holdings_raw.csv')
    par = load_bonds(root, include_rating=False)
    tr = load_bonds(root, include_rating=False, score='trace')
    assert (par['Liquidity_Score'] == par['Liquidity_Score_Par']).all()
    assert (tr['Liquidity_Score'] == tr['Liquidity_Score_TRACE']).all()
    assert tr['Liquidity_Score_Source'].str.startswith('trace_').mean() > 0.8
    assert tr['Liquidity_Score'].between(0, 1).all()
```

- [ ] **Step 2: Run** → FAIL (ImportError on `build_trace_liquidity_score`).
- [ ] **Step 3: Implement** in `portfolio_utils.py`

```python
from pandas.tseries.holiday import USFederalHolidayCalendar
from pandas.tseries.offsets import CustomBusinessDay

TRACE_SOURCES = ('trace_full_window', 'trace_scaled', 'imputed_sector_maturity', 'imputed_sector', 'imputed_universe')

def _trading_days(start, end):
    return int(len(pd.date_range(start, end, freq=CustomBusinessDay(calendar=USFederalHolidayCalendar()))))

def build_trace_liquidity_score(bonds, data_dir, min_exposure_days=10, min_group=5):
    xw_path = os.path.join(data_dir, 'lqd_cusip_crosswalk.csv')
    act_path = os.path.join(data_dir, 'trace_activity_raw.csv')
    for p in (xw_path, act_path):
        if not os.path.exists(p):
            raise FileNotFoundError(f'TRACE score needs {p}; run 00b/00d first')
    xw = pd.read_csv(xw_path, dtype={'cusip': str})[['bond_id', 'cusip']]
    act = pd.read_csv(act_path, dtype={'cusip': str})
    ws, we = pd.Timestamp(act['window_start'].iloc[0]), pd.Timestamp(act['window_end'].iloc[0])
    window_days = _trading_days(ws, we)
    counts = act.set_index('cusip')['n_trades']

    df = bonds[['bond_id', 'Sector', 'Maturity_Bucket', 'Effective Date']].merge(xw, on='bond_id', how='left')
    df.index = bonds.index
    eff = pd.to_datetime(df['Effective Date'], errors='coerce')
    n = df['cusip'].map(counts).fillna(0.0).where(df['cusip'].notna())
    raw = pd.Series(np.nan, index=df.index)
    src = pd.Series(None, index=df.index, dtype=object)

    full = df['cusip'].notna() & (eff < ws)
    raw[full] = n[full]; src[full] = 'trace_full_window'
    inwin = df['cusip'].notna() & (eff >= ws) & (eff <= we)
    for i in df.index[inwin]:
        expo = _trading_days(eff[i], we)
        if expo >= min_exposure_days:
            raw[i] = n[i] / expo * window_days; src[i] = 'trace_scaled'

    peers = raw[src == 'trace_full_window']
    grp_sm = peers.groupby([df.loc[peers.index, 'Sector'], df.loc[peers.index, 'Maturity_Bucket'].astype(str)]).agg(['median', 'size'])
    grp_s = peers.groupby(df.loc[peers.index, 'Sector']).agg(['median', 'size'])
    universe = float(peers.median())
    for i in df.index[raw.isna()]:
        key = (df.at[i, 'Sector'], str(df.at[i, 'Maturity_Bucket']))
        if key in grp_sm.index and grp_sm.loc[key, 'size'] >= min_group:
            raw[i], src[i] = grp_sm.loc[key, 'median'], 'imputed_sector_maturity'
        elif key[0] in grp_s.index and grp_s.loc[key[0], 'size'] >= min_group:
            raw[i], src[i] = grp_s.loc[key[0], 'median'], 'imputed_sector'
        else:
            raw[i], src[i] = universe, 'imputed_universe'

    lg = np.log1p(raw.astype(float))
    lo, hi = lg.min(), lg.max()
    score = (lg - lo) / (hi - lo) if hi > lo else pd.Series(0.0, index=df.index)
    return pd.DataFrame({'trace_raw_count': raw.astype(float), 'Liquidity_Score_TRACE': score,
                         'Liquidity_Score_Source': src})
```

and in `load_bonds`: rename the existing score to `Liquidity_Score_Par`, then

```python
    data_dir = os.path.dirname(os.path.abspath(path))
    try:
        bonds = bonds.join(build_trace_liquidity_score(bonds, data_dir))
    except FileNotFoundError:
        if score == 'trace':
            raise
    bonds['Liquidity_Score'] = bonds['Liquidity_Score_Par'] if score == 'par' else bonds['Liquidity_Score_TRACE']
```

Keep `Liquidity_Score_Raw` as it was (Step 2/3 may reference it: grep first).

- [ ] **Step 4: Run** the new tests and the whole suite → PASS; run `python3 -m pytest tests -q` → all green.
- [ ] **Step 5: Commit** `feat: TRACE-activity liquidity score with peer imputation`.

---

### Task 3: `--score` in Step 3, run the TRACE sweep

**Files:**
- Modify: `scripts/03_liquidity_optimization.py:35` (module-level makedirs), `:168-193` (`run_sweep`), `__main__`

- [ ] **Step 1:** Add argparse in `__main__`:

```python
if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--score', choices=SCORES, default='par', help='liquidity score to optimise on')
    args = ap.parse_args()
    run_sweep(score=args.score)
```

`run_sweep(score='par')`: `paths = output_paths(score)`; `os.makedirs(paths['weights_dir'], exist_ok=True)`; `bonds = load_bonds(score=score)`; write files to `paths['weights_dir']` and `paths['sweep_csv']`; add `Liquidity_Score_Source` to the saved columns when present. Remove the module-level `os.makedirs`.
- [ ] **Step 2: Verify par reproduces:** from repo root `python3 scripts/03_liquidity_optimization.py --score par` then `git status --short output/` must show nothing changed (weights files may differ at float noise only; if so, report the max abs diff and stop to decide).
- [ ] **Step 3: Run trace:** `python3 scripts/03_liquidity_optimization.py --score trace` → `output/liquidity_trace/` (81 files) and `output/step3_liquidity_sweep_trace.csv`.
- [ ] **Step 4: Commit** `feat: Step 3 --score switch; TRACE-score lambda sweep outputs`.

---

### Task 4: `--score` in Steps 4, 4b, 5, run them on the TRACE sweep

**Files:**
- Modify: `scripts/04_performance_comparison.py:31-33, 38-46, 48-57, 200-202`
- Modify: `scripts/04b_comparison_charts.py:14-16, 33-37, 189-195` (move the module-level reads into `main(step4_dir)`)
- Modify: `scripts/05_tradeoff_analysis.py:62-65, 105-110, 355-370`

- [ ] **Step 1:** Each script: `ap.add_argument('--score', choices=SCORES, default='par')`; `paths = output_paths(args.score, root=os.path.join(os.path.dirname(__file__), '..', 'output'))`; replace `OUT`/`LIQ`/`STEP4`/`CH` uses with values derived from `paths` inside `main`. `load_bonds(..., score=args.score)` in 04 and 05 so `weighted_avg_liquidity_score` is measured on the active score. In 4b, wrap the five chart functions so they take the dataframes as arguments (or set module globals inside `main` before calling them — the smaller diff; do that).
- [ ] **Step 2: Verify par reproduces:** from `scripts/`: `python3 04_performance_comparison.py && python3 04b_comparison_charts.py && python3 05_tradeoff_analysis.py` → `git status --short output/step4 output/step5` shows only PNG byte churn at most (compare CSV/JSON with `git diff --stat`; CSV/JSON must be unchanged).
- [ ] **Step 3: Run trace:** same three commands with `--score trace` → `output/step4_trace/`, `output/step5_trace/`.
- [ ] **Step 4: Commit** `feat: Steps 4/4b/5 --score switch; TRACE-score results`.

---

### Task 5: Step 7 cross-evaluation

**Files:**
- Create: `scripts/07_score_comparison.py`
- Test: `tests/test_score_comparison.py`

**Interfaces:**
- Produces: `cross_evaluate(bonds, weights_dir, sizes, lambdas, optimised_on) -> DataFrame` with columns `optimised_on, target_n, lambda_liq, wavg_par_score, wavg_trace_score, imputed_weight_share`; `main()` writes the four outputs in spec §4.

- [ ] **Step 1: Write the failing test**

```python
import importlib, os, sys
import numpy as np, pandas as pd, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
s7 = importlib.import_module('07_score_comparison')

def test_cross_evaluate_weighted_averages(tmp_path):
    bonds = pd.DataFrame({'bond_id': [0, 1, 2], 'Liquidity_Score_Par': [0.0, 0.5, 1.0],
                          'Liquidity_Score_TRACE': [1.0, 0.0, 0.5],
                          'Liquidity_Score_Source': ['trace_full_window', 'imputed_sector', 'trace_scaled']})
    pd.DataFrame({'bond_id': [0, 2], 'Sampled_Weight': [0.25, 0.75]}).to_csv(tmp_path / 'optimization_liquidity_N2_lambda0.0.csv', index=False)
    out = s7.cross_evaluate(bonds, str(tmp_path), sizes=[2], lambdas=[0.0], optimised_on='par')
    row = out.iloc[0]
    assert row['wavg_par_score'] == pytest.approx(0.75) and row['wavg_trace_score'] == pytest.approx(0.625)
    assert row['imputed_weight_share'] == pytest.approx(0.0) and row['optimised_on'] == 'par'

def test_verdict_rule():
    assert s7.verdict_for({50: (0.10, 0.20), 100: (0.10, 0.16)}).startswith('bought size')
    assert s7.verdict_for({50: (0.10, 0.11), 100: (0.10, 0.09)}).startswith('captured most')
    assert s7.verdict_for({50: (0.10, 0.20), 100: (0.10, 0.10)}).startswith('mixed')
```

- [ ] **Step 2: Run** → FAIL (module not found).
- [ ] **Step 3: Implement** `07_score_comparison.py` (run from `scripts/`): load `bonds = load_bonds('../data/lqd_holdings_raw.csv', include_rating=False, score='trace')`; `cross_evaluate` for `('par', output_paths('par', root)['weights_dir'])` and `('trace', ...)`; merge with each run's `step4_full_lambda_path.csv` on `(N, lambda)` for the mismatch dims; write `step7_cross_evaluation.csv`; build `step7_headline.csv` at λ ∈ {0.0, 1.0}: for each `optimised_on`, N: `trace_gain = wavg_trace(λ=1) − wavg_trace(λ=0)`, `par_gain`, `sector_L1_cost_pp`, `top10_cost_pp`; `verdict_for({N: (par_book_trace_gain, trace_book_trace_gain)})` implementing spec §4 (relative excess > 0.5 at every N → `bought size, not tradability: ...`; within ±0.25 at every N → `captured most of the available tradability: ...`; else `mixed: ...` listing per N); `step7_summary.json`; charts with the Step 4–6 palette: chart1 frontier panels per N (x = `sector_L1_pp`, y = `wavg_trace_score`, slate = par-optimised, ochre = TRACE-optimised, points sorted by λ and joined), chart2 grouped bars of `trace_gain` at λ=1 per N.
- [ ] **Step 4: Run** tests → PASS; `python3 07_score_comparison.py` from `scripts/`; open both PNGs and check labels/overlap.
- [ ] **Step 5: Commit** `feat: Step 7 cross-evaluation of par vs TRACE liquidity tilts`.

---

### Task 6: Docs

**Files:**
- Modify: `README.md` (Step 7 paragraph after Step 6; tree entries for `07_score_comparison.py`, `output/liquidity_trace`, `step3_liquidity_sweep_trace.csv`, `step4_trace/`, `step5_trace/`, `step7/`; Running-it commands with `--score trace`; Known-limitations liquidity line)
- Modify: `docs/superpowers/specs/2026-09-17-trace-liquidity-score-phase2-design.md` (add §7 "Result" with the headline numbers and the imputed share)

- [ ] **Step 1:** Write the README paragraph from `output/step7/step7_summary.json` (numbers, not adjectives). **Step 2:** Add spec §7. **Step 3:** `python3 -m pytest tests -q` all green. **Step 4: Commit** `docs: Phase 2 results`.
