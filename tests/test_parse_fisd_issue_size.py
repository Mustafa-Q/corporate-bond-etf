import importlib
import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

parse = importlib.import_module('00e_parse_fisd_issue_size')


def _xw(tmp_path):
    p = tmp_path / 'xw.csv'
    p.write_text('bond_id,cusip,match_method\n0,001055BJ0,m\n1,00108WAU4,m\n2,,no\n3,ZZZZZZZZ9,m\n')
    return str(p)


def test_build_issue_size_maps_fisd_columns_and_detects_thousands(tmp_path):
    # WRDS FISD export style: complete_cusip, offering_amt in $ thousands
    f = tmp_path / 'fisd.csv'
    f.write_text('issue_id,complete_cusip,offering_amt,offering_date,maturity\n'
                 '1,001055BJ0,750000,2023-05-01,2033-05-15\n'
                 '2,00108WAU4,1000000,2020-02-01,2030-02-01\n'
                 '3,999999AA1,500000,2020-01-01,2030-01-01\n')          # not in the crosswalk
    out, stats = parse.build_issue_size(str(f), _xw(tmp_path))
    assert list(out.columns) == ['bond_id', 'cusip', 'offering_amt', 'amount_outstanding', 'offering_date']
    assert out['bond_id'].tolist() == [0, 1]
    assert out['offering_amt'].tolist() == [750_000_000.0, 1_000_000_000.0]   # converted to dollars
    assert out['amount_outstanding'].isna().all()                              # column absent in this export
    assert stats['units_detected'] == 'thousands' and stats['rows_read'] == 3
    assert stats['bonds_with_issue_size'] == 2 and stats['cusips_in_crosswalk'] == 3


def test_build_issue_size_keeps_dollars_and_amount_outstanding(tmp_path):
    f = tmp_path / 'fisd.csv'
    f.write_text('CUSIP,OFFERING_AMT,AMOUNT_OUTSTANDING\n001055BJ0,750000000,700000000\n')
    out, stats = parse.build_issue_size(str(f), _xw(tmp_path), units='dollars')
    assert out['offering_amt'].iloc[0] == 750_000_000.0 and out['amount_outstanding'].iloc[0] == 700_000_000.0
    assert stats['units_detected'] == 'dollars'


def test_duplicate_cusips_keep_the_largest_outstanding(tmp_path):
    f = tmp_path / 'fisd.csv'
    f.write_text('complete_cusip,offering_amt,amount_outstanding,effective_date\n'
                 '001055BJ0,750000,750000,2023-05-01\n001055BJ0,750000,600000,2025-01-01\n')
    out, _ = parse.build_issue_size(str(f), _xw(tmp_path), units='thousands')
    assert len(out) == 1 and out['amount_outstanding'].iloc[0] == 600_000_000.0   # latest row wins


def test_missing_cusip_column_is_a_clear_error(tmp_path):
    f = tmp_path / 'fisd.csv'
    f.write_text('issue_id,offering_amt\n1,5\n')
    with pytest.raises(ValueError, match='CUSIP'):
        parse.build_issue_size(str(f), _xw(tmp_path))


def test_main_writes_file_and_report(tmp_path):
    f = tmp_path / 'fisd.csv'
    f.write_text('complete_cusip,offering_amt\n001055BJ0,750000\n')
    out, rep = tmp_path / 'fisd_issue_size.csv', tmp_path / 'rep.json'
    assert parse.main([str(f), '--crosswalk', _xw(tmp_path), '--out', str(out), '--report', str(rep)]) == 0
    got = pd.read_csv(out, dtype={'cusip': str})
    assert got['cusip'].iloc[0] == '001055BJ0'
    assert json.loads(rep.read_text())['source'].startswith('WRDS')


def test_zero_amount_outstanding_is_treated_as_missing(tmp_path):
    # FISD quirk: a few bonds carry AMOUNT_OUTSTANDING == 0 with a real OFFERING_AMT; a literal
    # zero must not sort them as the smallest issues
    f = tmp_path / 'fisd.csv'
    f.write_text('MATURITY,OFFERING_AMT,OFFERING_DATE,COMPLETE_CUSIP,AMOUNT_OUTSTANDING,COUPON\n'
                 '2033-05-15,750000,2023-05-01,001055BJ0,0,5.1\n'
                 '2030-02-01,1000000,2020-02-01,00108WAU4,900000,4.0\n')
    out, stats = parse.build_issue_size(str(f), _xw(tmp_path))
    o = out.set_index('cusip')
    assert pd.isna(o.loc['001055BJ0', 'amount_outstanding']) and o.loc['001055BJ0', 'offering_amt'] == 750_000_000.0
    assert o.loc['00108WAU4', 'amount_outstanding'] == 900_000_000.0
    assert stats['amount_outstanding_zero_treated_missing'] == 1
    assert stats['bonds_with_amount_outstanding'] == 1
