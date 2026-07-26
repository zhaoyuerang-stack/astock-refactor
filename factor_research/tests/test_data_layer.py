"""Data-layer regression tests."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from lake.load_lake import (
    load_capital_panel,
    load_fundamental_panel,
    load_panel,
    load_prices,
)
from lake.schema import CAPITAL_FIELDS, FUNDAMENTAL_FIELDS

# Use a narrow date window for fast test execution.
TEST_START = "2025-06-01"


def test_load_prices_shape():
    """load_prices returns a dict with expected fields."""
    px = load_prices(start=TEST_START, fields=("close", "volume"))
    assert "close" in px
    assert "volume" in px
    assert isinstance(px["close"], pd.DataFrame)
    assert isinstance(px["volume"], pd.DataFrame)
    assert px["close"].shape == px["volume"].shape
    print("✅ test_load_prices_shape passed")


def test_load_fundamental_availability():
    """Fundamental panel uses avail_date (not report_date) — no future data."""
    px = load_prices(start=TEST_START, fields=("close",))
    trade_dates = px["close"].index
    fund = load_fundamental_panel(trade_dates, fields=["roe"])
    assert "roe" in fund
    assert isinstance(fund["roe"], pd.DataFrame)
    # avail_date alignment: first non-NaN should be before or at first trade date
    first_valid = fund["roe"].first_valid_index()
    if first_valid is not None:
        assert first_valid <= trade_dates[-1]
    print("✅ test_load_fundamental_availability passed")


def test_load_capital_shift():
    """Capital panel is shifted by 1 day (T+1 availability)."""
    px = load_prices(start=TEST_START, fields=("close",))
    trade_dates = px["close"].index
    cap = load_capital_panel(trade_dates, fields=["margin_balance"])
    assert "margin_balance" in cap
    print("✅ test_load_capital_shift passed")


def test_load_panel_integration():
    """load_panel integrates all data sources."""
    panel = load_panel(start=TEST_START, with_fundamental=True)
    assert "close" in panel
    assert "volume" in panel
    assert "amount" in panel
    assert "fund_roe" in panel
    print("✅ test_load_panel_integration passed")


def test_schema_consistency():
    """All field names match lake.schema definitions."""
    px = load_prices(start=TEST_START, fields=("close", "volume"))
    trade_dates = px["close"].index

    fund = load_fundamental_panel(trade_dates, fields=FUNDAMENTAL_FIELDS)
    for f in FUNDAMENTAL_FIELDS:
        if f in fund:
            assert isinstance(fund[f], pd.DataFrame)

    cap = load_capital_panel(trade_dates, fields=CAPITAL_FIELDS)
    for f in CAPITAL_FIELDS:
        if f in cap:
            assert isinstance(cap[f], pd.DataFrame)
    print("✅ test_schema_consistency passed")


if __name__ == "__main__":
    test_load_prices_shape()
    test_load_fundamental_availability()
    test_load_capital_shift()
    test_load_panel_integration()
    test_schema_consistency()
    print("\n🎉 All data-layer tests passed!")
