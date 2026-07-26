import pandas as pd
import pytest

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal


def _engine(closes, *, buy_cost=0.0, sell_cost=0.0):
    dates = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-05"])
    close = pd.DataFrame({"000001": closes}, index=dates)
    dummy = pd.DataFrame(1.0, index=dates, columns=close.columns)
    return BacktestEngine(
        PricePanel(close=close, volume=dummy, amount=dummy),
        BacktestConfig(
            start="2026-01-01",
            leverage=1.0,
            cost=CostModel(
                buy_cost=buy_cost,
                sell_cost=sell_cost,
                financing_rate=0.0,
            ),
        ),
    )


def test_t_plus_one_close_does_not_capture_pre_fill_return():
    engine = _engine([100.0, 110.0, 121.0])
    decisions = pd.DataFrame(
        {"000001": [1.0]},
        index=pd.to_datetime(["2026-01-01"]),
    )

    result = engine.run(
        Signal(
            decision_weights=decisions,
            execution_timing="T_PLUS_1_CLOSE",
        )
    )

    assert result.returns.loc["2026-01-02"] == 0.0
    assert result.returns.loc["2026-01-05"] == pytest.approx(0.10)
    assert result.turnover.loc["2026-01-02"] == pytest.approx(1.0)


def test_fill_day_contains_cost_but_not_prefill_price_return():
    engine = _engine([100.0, 110.0, 121.0], buy_cost=0.01)
    decisions = pd.DataFrame(
        {"000001": [1.0]},
        index=pd.to_datetime(["2026-01-01"]),
    )

    result = engine.run(
        Signal(
            decision_weights=decisions,
            execution_timing="T_PLUS_1_CLOSE",
        )
    )

    assert result.returns.loc["2026-01-02"] == pytest.approx(-0.01)
    assert result.returns.loc["2026-01-05"] == pytest.approx(0.10)


def test_sell_at_t_plus_one_close_keeps_return_until_sell_close():
    engine = _engine([100.0, 110.0, 121.0])
    decisions = pd.DataFrame(
        {"000001": [1.0, 0.0]},
        index=pd.to_datetime(["2025-12-31", "2026-01-02"]),
    )

    result = engine.run(
        Signal(
            decision_weights=decisions,
            execution_timing="T_PLUS_1_CLOSE",
        )
    )

    assert result.returns.loc["2026-01-02"] == pytest.approx(0.10)
    assert result.returns.loc["2026-01-05"] == pytest.approx(0.10)
    assert result.turnover.loc["2026-01-05"] == pytest.approx(1.0)
