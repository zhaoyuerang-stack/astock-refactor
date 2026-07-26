"""Signal factory compatibility tests.

Run:
    cd factor_research && python3 tests/test_signal_factory.py
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _factor():
    return pd.DataFrame(
        [[1.0, 2.0, 3.0], [3.0, 2.0, 1.0]],
        index=pd.date_range("2024-01-01", periods=2),
        columns=["a", "b", "c"],
    )


def test_factor_to_signal_sets_core_fields():
    from engine.signal_factory import factor_to_signal

    sig = factor_to_signal(_factor(), top_n=2, direction=-1, rebalance_freq="20D", family="fam", version="v1")
    assert sig.factor.equals(_factor())
    assert sig.top_n == 2
    assert sig.direction == -1
    assert sig.rebalance_freq == "20D"
    assert sig.family == "fam"
    assert sig.version == "v1"


def test_legacy_engine_portfolio_to_signal_delegates_to_same_fields():
    from engine.portfolio import to_signal

    sig = to_signal(_factor(), n=2, direction=-1, rebalance_freq="20D", family="fam", version="v1")
    assert sig.factor.equals(_factor())
    assert sig.top_n == 2
    assert sig.direction == -1
    assert sig.rebalance_freq == "20D"
    assert sig.family == "fam"
    assert sig.version == "v1"


if __name__ == "__main__":
    test_factor_to_signal_sets_core_fields()
    test_legacy_engine_portfolio_to_signal_delegates_to_same_fields()
    print("signal factory tests passed")
