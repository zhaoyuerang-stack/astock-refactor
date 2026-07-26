"""Canonical price unit contract tests."""

import pandas as pd

import strategies.small_cap as small_cap_strategy


def test_canonical_price_units_are_shares_yuan_and_yuan_per_share():
    from lake.units import PriceUnitContract

    assert PriceUnitContract.volume == "share"
    assert PriceUnitContract.amount == "CNY"
    assert PriceUnitContract.raw_close == "CNY_per_share"


def test_implied_amount_has_no_board_specific_branch():
    from lake.units import implied_amount

    volume = pd.DataFrame(
        [[1000.0, 1000.0, 1000.0]],
        index=pd.to_datetime(["2026-06-18"]),
        columns=["000001", "300750", "688256"],
    )
    raw_close = pd.DataFrame(
        [[10.0, 20.0, 30.0]],
        index=volume.index,
        columns=volume.columns,
    )
    expected = pd.DataFrame(
        [[10000.0, 20000.0, 30000.0]],
        index=volume.index,
        columns=volume.columns,
    )
    pd.testing.assert_frame_equal(implied_amount(volume, raw_close), expected)


def test_small_cap_loader_prefers_lake_amount_and_only_fills_missing(monkeypatch):
    dates = pd.bdate_range("2026-06-17", periods=2)
    close = pd.DataFrame({"000001": [10.0, 11.0]}, index=dates)
    volume = pd.DataFrame({"000001": [1000.0, 2000.0]}, index=dates)
    lake_amount = pd.DataFrame({"000001": [10000.0, float("nan")]}, index=dates)
    raw_close = pd.DataFrame({"000001": [10.0, 11.0]}, index=dates)

    def fake_load_prices(*, start, fields):
        assert fields == ("close", "volume", "amount")
        return {"close": close, "volume": volume, "amount": lake_amount}

    monkeypatch.setattr(small_cap_strategy, "load_prices", fake_load_prices)
    monkeypatch.setattr(
        small_cap_strategy,
        "load_raw_close",
        lambda start: raw_close,
    )

    loaded_close, loaded_volume, loaded_amount = small_cap_strategy.load_price_panels(
        "2026-06-17"
    )

    pd.testing.assert_frame_equal(loaded_close, close)
    pd.testing.assert_frame_equal(loaded_volume, volume)
    expected_amount = pd.DataFrame({"000001": [10000.0, 22000.0]}, index=dates)
    pd.testing.assert_frame_equal(loaded_amount, expected_amount)
