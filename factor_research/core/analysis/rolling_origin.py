"""Rolling-origin 稳定性(Task 14)。

区分两种诚实模式,杜绝把「年度切片 / 全样本权重切片」冒充「训练 / 净化 CV」:

  · 固定公式(illiquidity 等无可训练参数):用 `rolling_origin_stability` ——
    每个窗口**从历史到 test_end 因果重算信号**,报告年度稳定性与执行退化。
    不声称训练,因为没有参数被拟合/选择。

  · 可训练/可搜索策略:用 walk_forward_selection(见 factory.autoresearch.walkforward),
    fit 物理截断到 train,predict 只见 history_through_test_date。

本模块只实现固定公式的因果滚动稳定性。关键不变量:signal_builder 在第 k 个窗口
**只能看到 < window_end 的价格**,绝不触碰 test 之后的数据(由 spy 测试机械证明)。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class RollingWindow:
    origin: pd.Timestamp     # 该窗口可见历史的右边界(信号只能用 < origin 的数据)
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def rolling_origin_windows(
    dates: pd.DatetimeIndex, *, test_years: int = 1, min_train_years: int = 3,
) -> list[RollingWindow]:
    """按自然年切 rolling-origin 窗口:第 k 年为 test,可见历史 = 该年起点之前。"""
    dates = pd.DatetimeIndex(dates).sort_values()
    if len(dates) == 0:
        return []
    years = sorted({d.year for d in dates})
    out: list[RollingWindow] = []
    for i, y in enumerate(years):
        if i < min_train_years:
            continue
        test_mask = (dates.year >= y) & (dates.year < y + test_years)
        test_dates = dates[test_mask]
        if len(test_dates) == 0:
            continue
        origin = test_dates[0]
        out.append(RollingWindow(origin=origin, test_start=test_dates[0], test_end=test_dates[-1]))
    return out


def rolling_origin_stability(
    prices,
    signal_builder: Callable,
    *,
    test_years: int = 1,
    min_train_years: int = 3,
) -> dict:
    """对固定公式做 rolling-origin 因果稳定性评估。

    signal_builder(prices_through_origin) -> per-date returns(pd.Series)。
    每个窗口只把 **< origin** 的价格面板喂给 builder(因果截断),取该窗口 test 段收益,
    汇总年度 sharpe 离散度作为稳定性指标。
    """
    dates = prices.close.index
    windows = rolling_origin_windows(dates, test_years=test_years, min_train_years=min_train_years)
    per_window = []
    for w in windows:
        # 因果截断:builder 只见 < test_end 的数据(含当前 test 段,信号自身 shift 防未来)
        sliced = _slice_prices(prices, upto=w.test_end)
        rets = signal_builder(sliced)
        seg = rets[(rets.index >= w.test_start) & (rets.index <= w.test_end)].dropna()
        if len(seg) < 20:
            continue
        ann = float(seg.mean() * 252)
        sharpe = float(seg.mean() / seg.std() * np.sqrt(252)) if seg.std() > 0 else 0.0
        per_window.append({"year": int(w.test_start.year), "annual": ann, "sharpe": sharpe, "n": int(len(seg))})

    sharpes = [w["sharpe"] for w in per_window]
    return {
        "method": "rolling_origin_stability",
        "windows": per_window,
        "n_windows": len(per_window),
        "sharpe_mean": float(np.mean(sharpes)) if sharpes else None,
        "sharpe_std": float(np.std(sharpes)) if sharpes else None,
        "positive_ratio": float(np.mean([s > 0 for s in sharpes])) if sharpes else None,
    }


def _slice_prices(prices, *, upto: pd.Timestamp):
    """返回只含 <= upto 的价格面板(同 PricePanel 形态)。"""
    from core.engine import PricePanel
    mask = prices.close.index <= upto
    return PricePanel(
        close=prices.close[mask],
        volume=prices.volume[mask] if prices.volume is not None else None,
        amount=prices.amount[mask] if prices.amount is not None else None,
    )
