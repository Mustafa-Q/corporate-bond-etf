import importlib
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

s7 = importlib.import_module('07_score_comparison')


def test_cross_evaluate_weighted_averages(tmp_path):
    bonds = pd.DataFrame({'bond_id': [0, 1, 2], 'Liquidity_Score_Par': [0.0, 0.5, 1.0],
                          'Liquidity_Score_TRACE': [1.0, 0.0, 0.5],
                          'Liquidity_Score_Source': ['trace_full_window', 'imputed_sector', 'trace_scaled']})
    pd.DataFrame({'bond_id': [0, 2], 'Sampled_Weight': [0.25, 0.75]}).to_csv(
        tmp_path / 'optimization_liquidity_N2_lambda0.0.csv', index=False)
    pd.DataFrame({'bond_id': [1], 'Sampled_Weight': [1.0]}).to_csv(
        tmp_path / 'optimization_liquidity_N2_lambda1.0.csv', index=False)
    out = s7.cross_evaluate(bonds, str(tmp_path), sizes=[2], lambdas=[0.0, 1.0], optimised_on='par')
    assert list(out.columns) == ['optimised_on', 'target_n', 'lambda_liq', 'wavg_par_score', 'wavg_trace_score',
                                 'imputed_weight_share']
    r0 = out[out.lambda_liq == 0.0].iloc[0]
    assert r0['wavg_par_score'] == pytest.approx(0.75) and r0['wavg_trace_score'] == pytest.approx(0.625)
    assert r0['imputed_weight_share'] == pytest.approx(0.0) and r0['optimised_on'] == 'par'
    r1 = out[out.lambda_liq == 1.0].iloc[0]
    assert r1['imputed_weight_share'] == pytest.approx(1.0) and r1['wavg_trace_score'] == pytest.approx(0.0)


def test_verdict_rule_is_the_spec_rule():
    # {N: (TRACE-score gain of the par-optimised book, TRACE-score gain of the TRACE-optimised book)} at lambda=1
    assert s7.verdict_for({50: (0.10, 0.20), 100: (0.10, 0.16)}).startswith('bought size, not tradability')
    assert s7.verdict_for({50: (0.10, 0.11), 100: (0.10, 0.09)}).startswith('captured most of the available tradability')
    assert s7.verdict_for({50: (0.10, 0.20), 100: (0.10, 0.10)}).startswith('mixed')
    assert s7.verdict_for({50: (0.0, 0.05)}).startswith('bought size, not tradability')   # zero par gain, positive TRACE gain
