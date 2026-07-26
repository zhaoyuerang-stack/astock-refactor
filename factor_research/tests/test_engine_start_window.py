"""BacktestConfig.start 统计窗口语义测试(原死字段,LESSONS 2026-06-12)。

Run:
    cd factor_research && python3 tests/test_engine_start_window.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal  # noqa: E402


def _panel(n_days=300, n_stocks=10, seed=7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n_days)
    codes = [f"{600000+i:06d}" for i in range(n_stocks)]
    rets = rng.normal(0.0005, 0.01, size=(n_days, n_stocks))
    close = pd.DataFrame(100.0 * np.exp(np.cumsum(rets, axis=0)), index=dates, columns=codes)
    one = pd.DataFrame(1.0, index=dates, columns=codes)
    return PricePanel(close=close, volume=one, amount=one)


def _weights(prices, every=20, top=3):
    w = {}
    for rd in prices.close.index[::every]:
        pos = prices.close.index.get_loc(rd)
        if pos + 1 >= len(prices.close.index):
            continue
        w[prices.close.index[pos + 1]] = pd.Series(1.0 / top, index=prices.close.columns[:top])
    return w


def test_start_truncates_stats_window_not_simulation():
    prices = _panel()
    weights = _weights(prices)
    cut = prices.close.index[150]

    full = BacktestEngine(prices, BacktestConfig(start=str(prices.close.index[0].date()), cost=CostModel(), leverage=1.0)).run(Signal(weights=weights))
    late = BacktestEngine(prices, BacktestConfig(start=str(cut.date()), cost=CostModel(), leverage=1.0)).run(Signal(weights=weights))

    # 统计序列被切片到 start 之后
    assert late.returns.index[0] >= cut
    assert late.n < full.n
    # 切片内逐日收益与全样本一致(模拟连续,只切统计)——持仓不重启
    overlap = full.returns.loc[cut:]
    pd.testing.assert_series_equal(late.returns, overlap)
    # start 当日没有"重新建仓"的额外换手
    assert late.turnover.loc[cut:].iloc[0] == full.turnover.loc[cut:].iloc[0]
    print("✅ start truncates stats window; simulation stays continuous")


def test_start_at_panel_start_is_noop():
    prices = _panel()
    weights = _weights(prices)
    cfg = BacktestConfig(start=str(prices.close.index[0].date()), cost=CostModel(), leverage=1.0)
    result = BacktestEngine(prices, cfg).run(Signal(weights=weights))
    assert result.returns.index[0] == prices.close.index[1]  # 首日无前收益,dropna 后从第2日起
    assert result.n == len(prices.close.index) - 1
    print("✅ start == panel start is a no-op (canonical lines unaffected)")


if __name__ == "__main__":
    test_start_truncates_stats_window_not_simulation()
    test_start_at_panel_start_is_noop()
    print("\n🎉 BacktestConfig.start window tests passed!")
