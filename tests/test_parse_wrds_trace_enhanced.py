import importlib
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

parse = importlib.import_module('00d_parse_wrds_trace_enhanced')

HEADER = 'cusip_id,trd_exctn_dt,bond_sym_id,company_symbol,trc_st,entrd_vol_qt,rptd_pr\n'


def _write(tmp_path, rows):
    p = tmp_path / 'wrds.csv'
    p.write_text(HEADER + ''.join(','.join(str(v) for v in r) + '\n' for r in rows))
    return str(p)


def test_build_activity_keeps_crosswalk_cusips_cleans_and_infers_window(tmp_path):
    path = _write(tmp_path, [
        ('001055BJ0', '2025-09-04', 'AFL1', 'AFL', 'T', '200000.00', '98.12'),
        ('001055BJ0', '2025-09-04', 'AFL1', 'AFL', 'T', '50000.00', '98.30'),
        ('001055BJ0', '2025-09-04', 'AFL1', 'AFL', 'X', '50000.00', '98.30'),
        ('001055BJ0', '2025-12-04', 'AFL1', 'AFL', 'T', '10000000000.00', '98.30'),
        ('999999ZZ9', '2025-10-01', 'ZZZ1', 'ZZZ', 'T', '1000.00', '100.0'),   # not in the crosswalk
    ])
    act, stats = parse.build_activity(path, {'001055BJ0', '00108WAU4'}, volume_cap=100_000_000)
    assert act['cusip'].tolist() == ['001055BJ0']
    row = act.set_index('cusip').loc['001055BJ0']
    assert row['n_trades'] == 2 and row['days_traded'] == 2
    assert row['n_capped_trades'] == 1
    assert row['total_volume_floor'] == 200_000 + 100_000_000
    assert pd.isna(row['n_customer_trades']) and pd.isna(row['n_dealer_trades'])   # WRDS pull carries no contra-party field
    assert row['window_start'] == '2025-09-04' and row['window_end'] == '2025-12-04'
    assert stats['rows_read'] == 5 and stats['rows_in_lqd_universe'] == 4
    assert stats['cleaning']['cancels_matched'] == 1
    assert stats['cusips_with_activity'] == 1 and stats['cusips_in_crosswalk'] == 2
    assert stats['window'] == {'start': '2025-09-04', 'end': '2025-12-04'}
    assert stats['volume_cap'] == 100_000_000


def test_main_writes_activity_file_and_cleaning_report(tmp_path):
    path = _write(tmp_path, [('001055BJ0', '2025-09-04', 'AFL1', 'AFL', 'T', '200000.00', '98.12')])
    xw = tmp_path / 'xw.csv'
    xw.write_text('bond_id,cusip,match_method\n0,001055BJ0,coupon_maturity_name\n1,,no_coupon_maturity_candidates\n')
    out = tmp_path / 'trace_activity_raw.csv'
    report = tmp_path / 'cleaning.json'
    assert parse.main([path, '--crosswalk', str(xw), '--out', str(out), '--report', str(report)]) == 0
    act = pd.read_csv(out, dtype={'cusip': str})
    assert list(act.columns) == parse.ACTIVITY_COLUMNS
    assert act['cusip'].tolist() == ['001055BJ0']
    rep = json.loads(report.read_text())
    assert rep['source'].startswith('WRDS')
    assert rep['cusips_in_crosswalk'] == 1
    assert 'cleaning' in rep and rep['cleaning']['rows_out'] == 1
