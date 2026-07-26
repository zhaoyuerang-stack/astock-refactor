"""Timing taxonomy compatibility tests.

Run:
    cd factor_research && python3 tests/test_timing_taxonomy.py
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factors.small_cap import small_cap_exposure_signal, small_cap_timing


def _panels():
    dates = pd.date_range("2024-01-01", periods=80, freq="B")
    close = pd.DataFrame(
        {
            "A": [10.0 + i * 0.01 for i in range(80)],
            "B": [9.0 + i * 0.02 for i in range(80)],
            "C": [8.0 - i * 0.005 for i in range(80)],
        },
        index=dates,
    )
    amount = pd.DataFrame(
        {
            "A": [100.0] * 80,
            "B": [200.0] * 80,
            "C": [300.0] * 80,
        },
        index=dates,
    )
    return close, amount


def test_small_cap_exposure_signal_matches_legacy_small_cap_timing():
    close, amount = _panels()
    new = small_cap_exposure_signal(close, amount, ma_window=16)
    old = small_cap_timing(close, amount, ma_window=16)
    assert len(new) == 3
    for a, b in zip(new, old, strict=True):
        pd.testing.assert_series_equal(a, b)


def test_strategies_small_cap_reexports_legacy_timing_name():
    """Task 7 kept ``small_cap_timing`` as a re-export from strategies.small_cap
    because research scripts import it from there. Pin that back-compat path so a
    future cleanup does not silently drop it."""
    from factors.small_cap import small_cap_timing as canonical_wrapper
    from strategies.small_cap import small_cap_timing as reexported

    assert reexported is canonical_wrapper


if __name__ == "__main__":
    test_small_cap_exposure_signal_matches_legacy_small_cap_timing()
    test_strategies_small_cap_reexports_legacy_timing_name()
    print("timing taxonomy tests passed")
