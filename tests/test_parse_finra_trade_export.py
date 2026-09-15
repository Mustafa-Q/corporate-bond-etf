import importlib
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
mod = importlib.import_module('00c_parse_finra_trade_export')


def _export_frame():
    # what the FINRA page's Export produces: display headers, descriptive code values
    return pd.DataFrame({
        'Symbol': ['FB5509319'] * 4 + ['XYZ1'],
        'Issuer Name': ['META PLATFORMS INC'] * 4 + ['OTHER'],
        'Date': ['2026-04-22', '2026-04-22', '2026-04-23', '2026-04-23', '2026-04-23'],
        'Time': ['04:26:00', '04:26:00', '10:00:00', '10:00:01', '11:00:00'],
        'Quantity': ['77000.00', '77000.00', '5MM+', '1000.00', '500'],
        'Price': ['99.522', '99.522', '99.4', '99.5', '90'],
        'Yield': ['3.87', '3.87', '3.9', '3.88', '5'],
        'Side': ['S-Reporting party sold to contra party'] * 4 + ['B-Reporting party bought'],
        'Contra Party Type': ['D-Contra party is a Broker/Dealer', 'D-Contra party is a Broker/Dealer',
                              'C-Contra party is a Customer (non-FINRA member)', 'N', 'C'],
        'Trade Status': ['M-Trade', 'M-Trade', 'M-Trade', 'N-Cancels', 'M-Trade'],
        'CUSIP': ['30303M8G0', '30303M8G0', '30303M8G0', '30303M8G0', 'NOTINLQD1'],
    })


def test_read_export_maps_headers_and_strips_code_descriptions(tmp_path):
    p = tmp_path / 'export.csv'
    _export_frame().to_csv(p, index=False)
    df = mod.read_export(str(p))
    assert {'cusip', 'tradeExecutionDate', 'reportedTradeVolume', 'tradeStatus', 'contraPartyTypeCode'} <= set(df.columns)
    assert df['tradeStatus'].tolist() == ['M', 'M', 'M', 'N', 'M']
    assert df['contraPartyTypeCode'].tolist() == ['D', 'D', 'C', 'N', 'C']


def test_build_activity_dedups_filters_and_aggregates(tmp_path):
    p1, p2 = tmp_path / 'a.csv', tmp_path / 'b.csv'
    f = _export_frame()
    f.iloc[:3].to_csv(p1, index=False)
    f.iloc[1:].to_csv(p2, index=False)          # overlapping export -> duplicate rows
    act, stats = mod.build_activity([str(p1), str(p2)], {'30303M8G0', '00000QUIET'}, '2026-04-22', '2026-07-22')
    # rows 0 and 1 of the fixture are attribute-identical, so without a sequence number they collapse too
    assert stats['rows_read'] == 7 and stats['rows_after_dedup'] == 4 and stats['rows_in_lqd_universe'] == 3
    row = act.set_index('cusip').loc['30303M8G0']
    assert row['n_trades'] == 2                  # duplicates dropped, cancel dropped
    assert row['n_capped_trades'] == 1
    assert row['days_traded'] == 2
    assert row['n_customer_trades'] == 1 and row['n_dealer_trades'] == 1
    assert list(act.columns) == mod.ACTIVITY_COLUMNS
    assert '00000QUIET' not in act['cusip'].values   # zero-trade CUSIPs are added by Step 6, not here


def test_sequence_number_keeps_identical_prints_apart(tmp_path):
    f = _export_frame()
    f['Message Sequence Number'] = ['1', '2', '3', '4', '5']
    p1, p2 = tmp_path / 'a.csv', tmp_path / 'b.csv'
    f.iloc[:3].to_csv(p1, index=False)
    f.iloc[1:].to_csv(p2, index=False)
    act, stats = mod.build_activity([str(p1), str(p2)], {'30303M8G0'}, '2026-04-22', '2026-07-22')
    assert stats['rows_after_dedup'] == 5 and stats['rows_in_lqd_universe'] == 4
    assert act.set_index('cusip').loc['30303M8G0', 'n_trades'] == 3


def test_missing_required_column_is_a_clear_error(tmp_path):
    p = tmp_path / 'bad.csv'
    _export_frame().drop(columns=['CUSIP']).to_csv(p, index=False)
    try:
        mod.read_export(str(p))
    except ValueError as e:
        assert 'cusip' in str(e) and 'Columns' in str(e)
    else:
        raise AssertionError('expected ValueError for missing CUSIP column')
