import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from trace_utils import (aggregate_trades, match_lqd_to_master, name_similarity,  # noqa: E402
                         normalize_issuer_name, parse_volume)


# --- name normalisation -----------------------------------------------------------

@pytest.mark.parametrize('name, expected', [
    ('GOLDMAN SACHS GROUP INC/THE', {'GOLDMAN', 'SACHS'}),
    ('US BANCORP (FXD-FRN) MTN', {'US', 'BANCORP'}),
    ('TORONTO-DOMINION BANK/THE MTN', {'TORONTO', 'DOMINION', 'BANK'}),
    ('ANHEUSER-BUSCH COMPANIES LLC', {'ANHEUSER', 'BUSCH'}),
    ('ANHEUSER-BUSCH COS LLC', {'ANHEUSER', 'BUSCH'}),
    ('SPACE EXPLORATION TECHNOLOGIES COR 144A', {'SPACE', 'EXPLORATION', 'TECHNOLOGIES', 'COR'}),
    ('AT&T INC', {'AT', 'T'}),
])
def test_normalize_issuer_name(name, expected):
    assert normalize_issuer_name(name) == frozenset(expected)


def test_normalize_all_noise_falls_back_to_first_word():
    assert normalize_issuer_name('THE CO') == frozenset({'THE'})
    assert normalize_issuer_name(None) == frozenset()


def test_name_similarity_ignores_suffix_style():
    assert name_similarity('ANHEUSER-BUSCH COMPANIES LLC', 'ANHEUSER-BUSCH COS LLC') == 1.0
    assert name_similarity('META PLATFORMS INC', 'AT&T INC') == 0.0
    assert 0 < name_similarity('JPMORGAN CHASE & CO', 'JPMORGAN CHASE BANK NA') < 1


def test_name_similarity_tolerates_filing_abbreviations():
    assert name_similarity('CHARTER COMMUNICATIONS OPERATING LLC', 'CHARTER COMM OPT LLC/CAP') >= 0.34
    assert name_similarity('BRITISH TELECOMMUNICATIONS PLC', 'BRITISH TELECOMMUNICATIO') == 1.0
    assert name_similarity('GLAXOSMITHKLINE CAPITAL INC', 'GLAXOSMITHKLINE CAP INC') == 1.0
    assert name_similarity('LOWES COMPANIES INC', "LOWE'S COS INC") >= 0.34
    assert name_similarity('AT&T INC', 'ATMOS ENERGY') < 0.34        # 2-letter tokens never prefix-match


# --- volume caps ------------------------------------------------------------------

@pytest.mark.parametrize('raw, vol, capped', [
    ('77000.00', 77000.0, False),
    ('5MM+', 5_000_000.0, True),
    ('1MM+', 1_000_000.0, True),
    ('1,000', 1000.0, False),
])
def test_parse_volume(raw, vol, capped):
    assert parse_volume(raw) == (vol, capped)


def test_parse_volume_garbage_is_nan():
    v, c = parse_volume('n/a')
    assert np.isnan(v) and c is False


# --- matching ---------------------------------------------------------------------

def _master():
    return pd.DataFrame({
        'cusip': ['30303M8B1', '30303M8G0', 'U59197AB6', '999999AA1', '38141GXX1'],
        'issuerName': ['META PLATFORMS INC', 'META PLATFORMS INC', 'META PLATFORMS INC',
                       'SOME OTHER ISSUER CORP', 'GOLDMAN SACHS GROUP INC'],
        'couponRate': ['3.5000000', '3.5000000', '3.5000000', '3.5000000', '6.7500000'],
        'maturityDate': ['2027-08-15', '2027-08-15', '2027-08-15', '2027-08-15', '2037-10-01'],
        'is144A': ['Y', 'N', 'N', 'N', 'N'],
        'lastTradeDate': ['2026-08-12', '2026-09-15', '2026-08-26', '2026-01-01', '2026-09-01'],
        'moodysRating': ['Aa3', 'Aa3', None, 'Baa2', 'A2'],
        'standardAndPoorsRating': ['AA-', 'AA-', 'AA-', 'BBB', 'BBB+'],
    })


def _bonds(rows):
    df = pd.DataFrame(rows, columns=['bond_id', 'Name', 'Coupon (%)', 'Maturity'])
    df['Maturity'] = pd.to_datetime(df['Maturity'])
    return df


def test_match_prefers_registered_line_over_144a_and_regs():
    xw = match_lqd_to_master(_bonds([(0, 'META PLATFORMS INC', 3.5, '2027-08-15')]), _master())
    assert xw.loc[0, 'cusip'] == '30303M8G0'
    assert xw.loc[0, 'match_method'] == 'coupon_maturity_name'
    assert xw.loc[0, 'n_candidates'] == 4 and xw.loc[0, 'n_name_matches'] == 3
    assert xw.loc[0, 'moodys_rating'] == 'Aa3'


def test_match_picks_144a_line_when_lqd_name_says_144a():
    xw = match_lqd_to_master(_bonds([(0, 'META PLATFORMS INC 144A', 3.5, '2027-08-15')]), _master())
    assert xw.loc[0, 'cusip'] == '30303M8B1'


def test_match_rejects_same_coupon_maturity_different_issuer():
    xw = match_lqd_to_master(_bonds([(7, 'UNRELATED ENERGY LP', 3.5, '2027-08-15')]), _master())
    assert pd.isna(xw.loc[0, 'cusip'])
    assert xw.loc[0, 'match_method'] == 'name_below_threshold'
    assert xw.loc[0, 'bond_id'] == 7


def test_match_reports_no_candidates_and_keeps_every_bond():
    bonds = _bonds([(0, 'META PLATFORMS INC', 3.5, '2027-08-15'),
                    (1, 'GOLDMAN SACHS GROUP INC/THE', 6.75, '2037-10-01'),
                    (2, 'NOBODY INC', 9.99, '2099-01-01')])
    xw = match_lqd_to_master(bonds, _master())
    assert list(xw['bond_id']) == [0, 1, 2]
    assert xw.loc[1, 'cusip'] == '38141GXX1'
    assert xw.loc[2, 'match_method'] == 'no_coupon_maturity_candidates'


def test_match_coupon_tolerance_bridges_rounding_differences():
    master = _master()
    master.loc[4, 'couponRate'] = '6.7600000'          # other file rounded 6.755 the other way
    xw = match_lqd_to_master(_bonds([(1, 'GOLDMAN SACHS GROUP INC/THE', 6.75, '2037-10-01')]), master)
    assert xw.loc[0, 'cusip'] == '38141GXX1'
    xw = match_lqd_to_master(_bonds([(1, 'GOLDMAN SACHS GROUP INC/THE', 6.70, '2037-10-01')]), master)
    assert xw.loc[0, 'match_method'] == 'no_coupon_maturity_candidates'


def test_match_is_one_to_one_and_loser_takes_next_candidate():
    master = pd.DataFrame({
        'cusip': ['AAA', 'BBB'],
        'issuerName': ['ANHEUSER-BUSCH INBEV FINANCE INC', 'ANHEUSER-BUSCH COS LLC'],
        'couponRate': [4.9, 4.9], 'maturityDate': ['2046-02-01', '2046-02-01'],
    })
    bonds = _bonds([(0, 'ANHEUSER-BUSCH COMPANIES LLC', 4.9, '2046-02-01'),
                    (1, 'ANHEUSER-BUSCH INBEV FINANCE INC', 4.9, '2046-02-01')])
    xw = match_lqd_to_master(bonds, master).set_index('bond_id')
    assert xw.loc[1, 'cusip'] == 'AAA' and xw.loc[0, 'cusip'] == 'BBB'
    assert xw['cusip'].is_unique


def test_match_reports_when_only_cusip_is_taken():
    master = pd.DataFrame({'cusip': ['AAA'], 'issuerName': ['META PLATFORMS INC'],
                           'couponRate': [3.5], 'maturityDate': ['2027-08-15']})
    bonds = _bonds([(0, 'META PLATFORMS INC', 3.5, '2027-08-15'), (1, 'META PLATFORMS INC', 3.5, '2027-08-15')])
    xw = match_lqd_to_master(bonds, master).set_index('bond_id')
    assert (xw['cusip'].notna()).sum() == 1
    assert 'cusip_taken_by_better_match' in xw['match_method'].values


def test_match_size_fallback_accepts_lone_candidate_with_agreeing_size():
    master = pd.DataFrame({
        'cusip': ['C1', 'C2', 'C3', 'C4'],
        'issuerName': ['META PLATFORMS INC', 'GOLDMAN SACHS GROUP INC', 'IBM CORP', 'RTX CORP'],
        'couponRate': [3.5, 6.75, 4.0, 4.0],
        'maturityDate': ['2027-08-15', '2037-10-01', '2030-01-01', '2031-01-01'],
        'size': [1000.0, 2000.0, 850.0, 50.0],
    })
    bonds = pd.DataFrame({
        'bond_id': [0, 1, 2, 3],
        'Name': ['META PLATFORMS INC', 'GOLDMAN SACHS GROUP INC/THE',
                 'INTERNATIONAL BUSINESS MACHINES CORP', 'RAYTHEON TECHNOLOGIES CORPORATION'],
        'Coupon (%)': [3.5, 6.75, 4.0, 4.0],
        'Maturity': pd.to_datetime(['2027-08-15', '2037-10-01', '2030-01-01', '2031-01-01']),
        'par': [1100.0, 2100.0, 900.0, 1000.0],
    })
    xw = match_lqd_to_master(bonds, master, size_cols=('par', 'size')).set_index('bond_id')
    assert xw.loc[2, 'cusip'] == 'C3' and xw.loc[2, 'match_method'] == 'coupon_maturity_size'
    assert pd.isna(xw.loc[3, 'cusip'])          # size ratio 0.05 is far outside the band
    assert xw.loc[3, 'match_method'] == 'name_below_threshold'
    # without size columns the fallback never fires
    assert pd.isna(match_lqd_to_master(bonds, master).set_index('bond_id').loc[2, 'cusip'])


# --- trade aggregation ------------------------------------------------------------

def test_aggregate_trades_metrics_caps_cancels_and_window():
    trades = pd.DataFrame({
        'cusip':               ['A', 'A', 'A', 'A', 'A', 'B', 'A'],
        'tradeExecutionDate':  ['2026-05-01', '2026-05-01', '2026-05-04', '2026-05-04', '2026-05-05', '2026-05-05', '2026-01-01'],
        'reportedTradeVolume': ['1000.00', '5MM+', '2000.00', '3000.00', '4000.00', '10.00', '999999'],
        'tradeStatus':         ['M', 'M', 'N', 'M', 'O', 'M', 'M'],
        'contraPartyTypeCode': ['C', 'D', 'C', 'C', 'T', 'A', 'C'],
    })
    out = aggregate_trades(trades, '2026-04-22', '2026-07-22').set_index('cusip')
    a = out.loc['A']
    assert a['n_trades'] == 4                      # cancel and out-of-window print excluded
    assert a['n_capped_trades'] == 1
    assert a['total_volume_floor'] == 1000 + 5_000_000 + 3000 + 4000
    assert a['days_traded'] == 3
    assert a['n_customer_trades'] == 2 and a['n_dealer_trades'] == 2
    assert a['median_trade_size'] == 3500.0
    assert out.loc['B', 'n_trades'] == 1 and out.loc['B', 'n_dealer_trades'] == 1
    assert (out['window_start'] == '2026-04-22').all()
