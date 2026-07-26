"""Physical-unit invariants for canonical A-share price rows."""

import pandas as pd
import pytest

from lake.invariants import PriceAmountInvariantError, validate_price_amount_units
from lake.sources import tushare_price


def _frame(amount_multiplier: float, rows_per_board: int = 120) -> pd.DataFrame:
    rows = []
    for prefix, raw_close in (("600", 10.0), ("300", 20.0), ("688", 30.0)):
        for i in range(rows_per_board):
            volume = 1000.0 + i
            rows.append(
                {
                    "date": pd.Timestamp("2026-06-18"),
                    "code": f"{prefix}{i:03d}",
                    "raw_close": raw_close,
                    "volume": volume,
                    "amount": volume * raw_close * amount_multiplier,
                }
            )
    return pd.DataFrame(rows)


def test_price_amount_invariant_accepts_canonical_units():
    report = validate_price_amount_units(_frame(1.0))

    assert report["passed"] is True
    assert report["status"] == "passed"
    assert report["median_ratio"] == pytest.approx(1.0)
    assert set(report["boards"]) == {"main", "chinext", "star"}


def test_price_amount_invariant_rejects_hundredfold_error():
    with pytest.raises(PriceAmountInvariantError, match="main.*median_ratio=100"):
        validate_price_amount_units(_frame(100.0))


def test_price_amount_invariant_does_not_fake_pass_small_samples():
    report = validate_price_amount_units(_frame(1.0, rows_per_board=2))

    assert report["passed"] is False
    assert report["status"] == "insufficient_sample"
    assert all(board["status"] == "insufficient_sample" for board in report["boards"].values())


def test_price_update_rejects_an_insufficient_unit_sample():
    from scripts.data.update_lake import _require_price_unit_report

    report = validate_price_amount_units(_frame(1.0, rows_per_board=2))

    with pytest.raises(PriceAmountInvariantError, match="insufficient_sample"):
        _require_price_unit_report(report)


def test_price_amount_error_exposes_alert_category():
    assert PriceAmountInvariantError.category == "price_unit_contract"


def test_price_amount_invariant_ignores_zero_and_missing_rows():
    frame = _frame(1.0)
    extra = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-06-18"),
                "code": "600999",
                "raw_close": 0.0,
                "volume": 0.0,
                "amount": 0.0,
            },
            {
                "date": pd.Timestamp("2026-06-18"),
                "code": "300999",
                "raw_close": float("nan"),
                "volume": 10.0,
                "amount": float("nan"),
            },
        ]
    )

    report = validate_price_amount_units(pd.concat([frame, extra], ignore_index=True))

    assert report["passed"] is True
    assert report["n"] == len(frame)


def test_tushare_increment_exposes_raw_close_for_prewrite_validation(monkeypatch):
    daily = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": "20260618",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "pre_close": 10.0,
                "vol": 123.0,
                "amount": 129.15,
            }
        ]
    )
    adj = pd.DataFrame(
        [
            {"ts_code": "000001.SZ", "trade_date": "20260617", "adj_factor": 2.0},
            {"ts_code": "000001.SZ", "trade_date": "20260618", "adj_factor": 2.0},
        ]
    )
    responses = iter([daily, adj])
    monkeypatch.setattr(tushare_price, "call", lambda *args, **kwargs: next(responses))

    out = tushare_price.fetch_new_day(
        pd.Timestamp("2026-06-18"),
        pd.Timestamp("2026-06-17"),
        pd.Series({"000001": 20.0}),
    )

    assert out.loc[0, "raw_close"] == 10.5
    assert out.loc[0, "volume"] == 12300.0
    assert out.loc[0, "amount"] == 129150.0
