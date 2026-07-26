"""ADR-040: BacktestEngine blocks limit-up buys / limit-down sells / zero volume on fill day."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import BacktestConfig, BacktestEngine, PricePanel, Signal


def _panel_with_limit_day():
    idx = pd.bdate_range("2023-01-02", periods=40)
    codes = ["600000", "600001"]
    close = pd.DataFrame(10.0, index=idx, columns=codes)
    # Day 20: 600000 hits up limit 11.0
    close.iloc[20, 0] = 11.0
    volume = pd.DataFrame(1e6, index=idx, columns=codes)
    amount = volume * close
    raw = close.copy()
    up = pd.DataFrame(10.5, index=idx, columns=codes)
    up.iloc[20, 0] = 11.0  # at limit on fill day only
    dn = pd.DataFrame(9.0, index=idx, columns=codes)
    return PricePanel(
        close=close,
        volume=volume,
        amount=amount,
        raw_close=raw,
        up_limit=up,
        down_limit=dn,
    ), idx[20], idx[19]


def test_enforce_fill_blocks_limit_up_buy_on_fill_day():
    prices, fill_day, decision = _panel_with_limit_day()
    w = pd.DataFrame(0.0, index=[decision], columns=prices.close.columns)
    w.loc[decision, "600000"] = 1.0

    eng = BacktestEngine(
        prices=prices,
        config=BacktestConfig(start="2023-01-02", leverage=1.0, enforce_fill_tradable=True),
    )
    result = eng.run(Signal(weights=w, family="test", version="t"))
    eng_off = BacktestEngine(
        prices=prices,
        config=BacktestConfig(start="2023-01-02", leverage=1.0, enforce_fill_tradable=False),
    )
    result_off = eng_off.run(Signal(weights=w, family="test", version="t"))

    # T+1 maps decision → fill_day: enforce must not buy into limit-up that day.
    assert float(result_off.turnover.loc[fill_day]) > 0.5
    assert float(result.turnover.loc[fill_day]) < 1e-9


def test_enforce_off_allows_limit_buy_on_fill_day():
    prices, fill_day, decision = _panel_with_limit_day()
    w = pd.DataFrame(0.0, index=[decision], columns=prices.close.columns)
    w.loc[decision, "600000"] = 1.0
    eng = BacktestEngine(
        prices=prices,
        config=BacktestConfig(start="2023-01-02", leverage=1.0, enforce_fill_tradable=False),
    )
    result = eng.run(Signal(weights=w))
    assert float(result.turnover.loc[fill_day]) > 0.5


def test_zero_volume_blocks_trade_on_fill_day():
    idx = pd.bdate_range("2023-01-02", periods=30)
    codes = ["600000"]
    close = pd.DataFrame(10.0, index=idx, columns=codes)
    volume = pd.DataFrame(1e6, index=idx, columns=codes)
    fill_day = idx[15]
    decision = idx[14]
    volume.loc[fill_day, "600000"] = 0.0
    amount = volume * close
    prices = PricePanel(close=close, volume=volume, amount=amount, raw_close=close)
    w = pd.DataFrame(1.0, index=[decision], columns=codes)
    eng = BacktestEngine(
        prices=prices,
        config=BacktestConfig(start="2023-01-02", leverage=1.0, enforce_fill_tradable=True),
    )
    result = eng.run(Signal(weights=w))
    eng_off = BacktestEngine(
        prices=prices,
        config=BacktestConfig(start="2023-01-02", leverage=1.0, enforce_fill_tradable=False),
    )
    result_off = eng_off.run(Signal(weights=w))
    assert float(result_off.turnover.loc[fill_day]) > 0.5
    assert float(result.turnover.loc[fill_day]) < 1e-9
