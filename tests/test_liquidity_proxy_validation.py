import importlib
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

step6 = importlib.import_module('06_liquidity_proxy_validation')


def test_trading_days_in_window_counts_us_business_days():
    # 2025-09-04 .. 2025-12-04: 66 weekdays minus Columbus Day, Veterans Day and Thanksgiving
    # (the bond market closes on all three) = 63, which is also the modal days_traded in the pull.
    assert step6.trading_days_in_window('2025-09-04', '2025-12-04') == 63


def _trace_inputs(tmp_path):
    (tmp_path / 'lqd_cusip_crosswalk.csv').write_text(
        'bond_id,cusip,match_method\n0,AAA,coupon_maturity_name\n1,BBB,coupon_maturity_name\n'
        '2,,no_coupon_maturity_candidates\n3,CCC,coupon_maturity_name\n4,DDD,coupon_maturity_name\n')
    (tmp_path / 'trace_activity_raw.csv').write_text(
        'cusip,window_start,window_end,n_trades,n_capped_trades,total_volume_floor,days_traded,'
        'n_customer_trades,n_dealer_trades,median_trade_size\n'
        'AAA,2025-09-04,2025-12-04,10,0,5000,4,,,500\n'
        'DDD,2025-09-04,2025-12-04,3,0,900,2,,,300\n')
    return pd.DataFrame({'bond_id': [0, 1, 2, 3, 4],
                         'Effective Date': pd.to_datetime(['2020-01-01', '2021-06-30', '2026-02-01',
                                                           '2026-03-15', '2025-10-20'])})


def test_load_trace_reads_window_and_keeps_missing_contra_party_as_nan(tmp_path):
    bonds = _trace_inputs(tmp_path)
    merged, report, window, note = step6.load_trace(bonds, str(tmp_path))
    assert window == ('2025-09-04', '2025-12-04')
    m = merged.set_index('bond_id')
    assert m.loc[1, 'n_trades'] == 0 and m.loc[1, 'days_traded'] == 0 and bool(m.loc[1, 'zero_trade_bond'])
    assert np.isnan(m.loc[0, 'n_customer_trades']) and np.isnan(m.loc[1, 'n_customer_trades'])
    assert np.isnan(m.loc[2, 'n_trades']) and not bool(m.loc[2, 'zero_trade_bond'])
    assert '1 matched bonds outstanding for the full window had zero prints' in note


def test_load_trace_classifies_window_exposure_from_effective_date(tmp_path):
    # A bond issued after the window cannot have printed: that is not illiquidity. A bond
    # issued mid-window had partial exposure. Only bonds outstanding before the window
    # start belong in the headline correlation.
    bonds = _trace_inputs(tmp_path)
    merged, _, _, note = step6.load_trace(bonds, str(tmp_path))
    m = merged.set_index('bond_id')
    assert m.loc[0, 'window_exposure'] == 'full_window'
    assert m.loc[1, 'window_exposure'] == 'full_window'
    assert m.loc[3, 'window_exposure'] == 'issued_after_window'
    assert m.loc[4, 'window_exposure'] == 'issued_in_window'
    assert pd.isna(m.loc[2, 'window_exposure'])            # no CUSIP: not in the activity universe
    assert bool(m.loc[3, 'zero_trade_bond'])                # still flagged, but not a full-window zero
    assert '1 issued after the window' in note and '1 issued inside it' in note


def test_load_issue_size_prefers_amount_outstanding_and_is_none_when_absent(tmp_path):
    bonds = pd.DataFrame({'bond_id': [0, 1, 2]})
    assert step6.load_issue_size(bonds, str(tmp_path)) is None
    (tmp_path / 'fisd_issue_size.csv').write_text(
        'bond_id,cusip,offering_amt,amount_outstanding,offering_date\n'
        '0,AAA,750000000,600000000,2023-05-01\n1,BBB,1000000000,,2020-02-01\n')
    out = step6.load_issue_size(bonds, str(tmp_path)).set_index('bond_id')
    assert out.loc[0, 'issue_size'] == 600_000_000.0      # amount outstanding when known
    assert out.loc[1, 'issue_size'] == 1_000_000_000.0    # else the offering amount
    assert np.isnan(out.loc[2, 'issue_size'])             # no FISD row: stays missing, not zero
    assert out.loc[0, 'issue_size_basis'] == 'amount_outstanding' and out.loc[1, 'issue_size_basis'] == 'offering_amt'
