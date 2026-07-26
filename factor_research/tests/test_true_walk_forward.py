"""Task 14: rolling-origin 因果截断 —— builder 永远看不到 test_end 之后的数据。"""
import numpy as np
import pandas as pd
import pytest

from core.analysis.rolling_origin import rolling_origin_stability, rolling_origin_windows
from core.engine import PricePanel


def _panel(years=6, n=20, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=years * 252)
    cols = [f"{600000+i:06d}" for i in range(n)]
    rets = rng.normal(0.0004, 0.02, size=(len(idx), n))
    close = pd.DataFrame(10 * np.cumprod(1 + rets, axis=0), index=idx, columns=cols)
    vol = pd.DataFrame(rng.uniform(1e5, 1e6, size=(len(idx), n)), index=idx, columns=cols)
    return PricePanel(close=close, volume=vol, amount=close * vol)


def test_windows_respect_min_train_years():
    p = _panel()
    wins = rolling_origin_windows(p.close.index, test_years=1, min_train_years=3)
    assert wins, "应至少有一个 test 窗口"
    # 第一个 test 窗口必须在第 4 个年份之后(前 3 年留作历史)
    years = sorted({d.year for d in p.close.index})
    assert wins[0].test_start.year == years[3]


def test_builder_never_sees_future_than_window_end():
    p = _panel()
    seen_max_dates = []

    def spy_builder(sliced_prices):
        seen_max_dates.append(sliced_prices.close.index.max())
        # 固定公式:简单动量,自身已因果(用截断面板)
        return sliced_prices.close.pct_change(fill_method=None).mean(axis=1).fillna(0.0)

    wins = rolling_origin_windows(p.close.index, test_years=1, min_train_years=3)
    rolling_origin_stability(p, spy_builder, test_years=1, min_train_years=3)

    # 每次喂给 builder 的最大日期 = 对应窗口的 test_end,绝不超过
    assert len(seen_max_dates) == len(wins)
    for seen, w in zip(seen_max_dates, wins, strict=True):
        assert seen <= w.test_end, "builder 看到了 test_end 之后的未来数据 —— 因果截断失效"


def test_stability_report_shape():
    p = _panel()
    rep = rolling_origin_stability(
        p, lambda sp: sp.close.pct_change(fill_method=None).mean(axis=1).fillna(0.0),
        test_years=1, min_train_years=3,
    )
    assert rep["method"] == "rolling_origin_stability"
    assert rep["n_windows"] >= 1
    assert rep["positive_ratio"] is not None


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
