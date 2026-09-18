import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from portfolio_utils import build_trace_liquidity_score, load_bonds  # noqa: E402

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
    _inputs(tmp_path, ['AAA,2025-09-04,2025-12-04,630,0,1,63,,,1\n'], ['0,AAA,m\n', '1,BBB,m\n'])
    out = build_trace_liquidity_score(_bonds(['2020-01-01', '2020-01-01']), str(tmp_path))
    assert out['trace_raw_count'].tolist() == [630.0, 0.0]
    assert out['Liquidity_Score_Source'].tolist() == ['trace_full_window'] * 2
    assert out['Liquidity_Score_TRACE'].tolist() == [1.0, 0.0]


def test_in_window_issue_is_scaled_by_exposure_with_guard(tmp_path):
    # window 2025-09-04..2025-12-04 = 63 trading days; issued 2025-11-06 -> 19 trading days of
    # exposure (Veterans Day off); issued 2025-12-01 -> 4 days, under the 10-day guard
    _inputs(tmp_path, ['AAA,2025-09-04,2025-12-04,38,0,1,19,,,1\n', 'BBB,2025-09-04,2025-12-04,5,0,1,4,,,1\n',
                       'CCC,2025-09-04,2025-12-04,100,0,1,50,,,1\n'],
            ['0,AAA,m\n', '1,BBB,m\n', '2,CCC,m\n'])
    out = build_trace_liquidity_score(_bonds(['2025-11-06', '2025-12-01', '2020-01-01']), str(tmp_path))
    assert out.loc[0, 'trace_raw_count'] == pytest.approx(38 / 19 * 63)
    assert out.loc[0, 'Liquidity_Score_Source'] == 'trace_scaled'
    assert out.loc[1, 'Liquidity_Score_Source'] == 'imputed_universe'   # one peer only: no group of 5
    assert out.loc[1, 'trace_raw_count'] == 100.0


def test_imputation_falls_back_in_order(tmp_path):
    peers = [f'{i},P{i},m\n' for i in range(6)]
    acts = [f'P{i},2025-09-04,2025-12-04,{100 + i},0,1,60,,,1\n' for i in range(6)]
    _inputs(tmp_path, acts, peers + ['6,,no\n', '7,,no\n', '8,,no\n'])
    b = _bonds(['2020-01-01'] * 6 + ['2026-03-01'] * 3,
               sectors=['Banking'] * 5 + ['Energy'] + ['Banking', 'Banking', 'Reits'],
               buckets=['5-7yr'] * 5 + ['5-7yr'] + ['5-7yr', '20yr+', '5-7yr'])
    out = build_trace_liquidity_score(b, str(tmp_path))
    assert out.loc[6, 'Liquidity_Score_Source'] == 'imputed_sector_maturity' and out.loc[6, 'trace_raw_count'] == 102.0
    assert out.loc[7, 'Liquidity_Score_Source'] == 'imputed_sector' and out.loc[7, 'trace_raw_count'] == 102.0
    assert out.loc[8, 'Liquidity_Score_Source'] == 'imputed_universe' and out.loc[8, 'trace_raw_count'] == 102.5


def test_post_window_issue_is_imputed_even_with_prints(tmp_path):
    # a when-issued print a day after the window must not count as full-window activity
    peers = [f'{i},P{i},m\n' for i in range(5)]
    acts = [f'P{i},2025-09-04,2025-12-04,{50 + i},0,1,60,,,1\n' for i in range(5)]
    _inputs(tmp_path, acts + ['NEW,2025-09-04,2025-12-04,400,0,1,2,,,1\n'], peers + ['5,NEW,m\n'])
    out = build_trace_liquidity_score(_bonds(['2020-01-01'] * 5 + ['2025-12-05']), str(tmp_path))
    assert out.loc[5, 'Liquidity_Score_Source'] == 'imputed_sector_maturity' and out.loc[5, 'trace_raw_count'] == 52.0


def test_missing_inputs_raise(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_trace_liquidity_score(_bonds(['2020-01-01']), str(tmp_path))


def test_load_bonds_score_switch():
    path = os.path.join(os.path.dirname(__file__), '..', 'data', 'lqd_holdings_raw.csv')
    par = load_bonds(path, include_rating=False)
    tr = load_bonds(path, include_rating=False, score='trace')
    assert (par['Liquidity_Score'] == par['Liquidity_Score_Par']).all()
    assert (tr['Liquidity_Score'] == tr['Liquidity_Score_TRACE']).all()
    assert tr['Liquidity_Score_Source'].str.startswith('trace_').mean() > 0.8
    assert tr['Liquidity_Score'].between(0, 1).all()
    assert par['Liquidity_Score_Source'].notna().all()          # TRACE columns ride along on a par run too
