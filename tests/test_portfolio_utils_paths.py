import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from portfolio_utils import output_paths  # noqa: E402


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
