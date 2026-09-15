import importlib
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
mod = importlib.import_module('00b_build_cusip_crosswalk')

NPORT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport" xmlns:com="http://www.sec.gov/edgar/common">
  <headerData><submissionType>NPORT-P</submissionType></headerData>
  <formData>
    <genInfo><seriesName>iShares iBoxx $ Investment Grade Corporate Bond ETF</seriesName>
      <seriesId>S000004361</seriesId><repPdDate>2026-05-31</repPdDate></genInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>META PLATFORMS INC</name><title>Meta Platforms Inc</title><cusip>30303M8G0</cusip>
        <identifiers><isin value="US30303M8G07"/></identifiers>
        <balance>52873000.00000000</balance><units>PA</units><curCd>USD</curCd>
        <valUSD>50640914.31000000</valUSD><pctVal>0.147</pctVal>
        <assetCat>DBT</assetCat><issuerCat>CORP</issuerCat>
        <debtSec><maturityDt>2035-11-15</maturityDt><couponKind>Fixed</couponKind><annualizedRt>4.88000000</annualizedRt></debtSec>
      </invstOrSec>
      <invstOrSec>
        <name>BLK CSH FND TREASURY SL AGENCY</name><title>cash</title><cusip>000000000</cusip>
        <balance>1</balance><assetCat>STIV</assetCat><issuerCat>RF</issuerCat>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
"""


def test_parse_nport_reads_holdings_and_metadata(tmp_path):
    p = tmp_path / 'primary_doc.xml'
    p.write_text(NPORT_XML)
    df, meta = mod.parse_nport(str(p))
    assert meta == {'seriesName': 'iShares iBoxx $ Investment Grade Corporate Bond ETF',
                    'seriesId': 'S000004361', 'repPdDate': '2026-05-31'}
    assert list(df.columns) == mod.NPORT_COLUMNS and len(df) == 2
    meta_row = df.iloc[0]
    assert meta_row['cusip'] == '30303M8G0' and meta_row['isin'] == 'US30303M8G07'
    assert meta_row['maturityDt'] == '2035-11-15' and meta_row['annualizedRt'] == 4.88
    assert meta_row['balance'] == 52873000.0


def test_build_crosswalk_matches_and_reports_par_ratio(tmp_path):
    p = tmp_path / 'primary_doc.xml'
    p.write_text(NPORT_XML)
    nport, _ = mod.parse_nport(str(p))
    bonds = pd.DataFrame({'bond_id': [0, 1], 'Name': ['META PLATFORMS INC', 'NOBODY CORP'],
                          'Coupon (%)': [4.88, 4.88], 'Maturity': pd.to_datetime(['2035-11-15', '2035-11-15']),
                          'Par Value': [52873000.0, 100.0]})
    xw = mod.build_crosswalk(bonds, nport)
    assert list(xw['bond_id']) == [0, 1]
    assert xw.loc[0, 'cusip'] == '30303M8G0' and xw.loc[0, 'isin'] == 'US30303M8G07'
    assert abs(xw.loc[0, 'par_ratio_nport_over_ishares'] - 1.0) < 1e-9
    assert pd.isna(xw.loc[1, 'cusip']) and xw.loc[1, 'match_method'] == 'name_below_threshold'
    assert 'is_144a' not in xw.columns
