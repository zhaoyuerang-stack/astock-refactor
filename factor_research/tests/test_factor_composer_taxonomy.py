"""Factor composer taxonomy tests.

Run:
    cd factor_research && python3 tests/test_factor_composer_taxonomy.py
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _factors():
    idx = pd.date_range("2024-01-01", periods=3)
    return {
        "a": pd.DataFrame([[1, 2], [3, 4], [5, 6]], index=idx, columns=["x", "y"], dtype=float),
        "b": pd.DataFrame([[3, 4], [5, 6], [7, 8]], index=idx, columns=["x", "y"], dtype=float),
    }


def test_factor_composer_canonical_equal_weight():
    from engine.factor_composer import equal_weight_factor

    expected = pd.DataFrame([[2, 3], [4, 5], [6, 7]], index=list(_factors().values())[0].index, columns=["x", "y"], dtype=float)
    pd.testing.assert_frame_equal(equal_weight_factor(_factors()), expected)


def test_portfolio_composer_canonical_and_legacy_compose_match():
    from portfolio.composer import compose
    from portfolio.portfolio_composer import compose_portfolio_returns

    idx = pd.date_range("2024-01-01", periods=3)
    returns = {
        "a": pd.Series([0.01, 0.02, 0.03], index=idx),
        "b": pd.Series([0.03, 0.02, 0.01], index=idx),
    }
    new_ret, new_w = compose_portfolio_returns(returns, method="equal_weight")
    old_ret, old_w = compose(returns, method="equal_weight")
    pd.testing.assert_series_equal(new_ret, old_ret)
    pd.testing.assert_frame_equal(new_w, old_w)


if __name__ == "__main__":
    test_factor_composer_canonical_equal_weight()
    test_portfolio_composer_canonical_and_legacy_compose_match()
    print("factor/portfolio composer taxonomy tests passed")
