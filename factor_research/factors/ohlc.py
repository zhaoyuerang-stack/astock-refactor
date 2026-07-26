"""OHLC 派生因子 — 用 daily_raw 的 high/low/open (之前完全没用).

PoC 提示: load_price_panels 输出多个字段被丢弃。daily_raw_all.parquet 有
完整 OHLC, 之前只用 raw_close。

3 个新维度:
  - 振幅 (amplitude): (high - low) / low 滚动均值 → 日内波动 ≠ 收盘波动
  - 跳空 (overnight gap): (open - prev_close) / prev_close → 隔夜 return
  - 收盘位置 (close position): (close - low) / (high - low) → 日内强弱

这些都是微观结构信号, 与 small_cap 流动性独立维度。
"""
from functools import lru_cache

import numpy as np
import pandas as pd

from factors.utils import mad_clip, safe_zscore


@lru_cache(maxsize=1)
def _load_ohlc_cache():
    """加载 daily_raw_all.parquet, 转 4 个 panel (open/high/low/close, date×code)."""
    df = pd.read_parquet(
        "data_lake/price/daily_raw_all.parquet",
        columns=["date", "code", "raw_open", "raw_high", "raw_low", "raw_close"],
    )
    df["date"] = pd.to_datetime(df["date"])
    df["code"] = df["code"].astype(str).str.zfill(6)
    panels = {}
    for col in ["raw_open", "raw_high", "raw_low", "raw_close"]:
        panels[col] = df.pivot(index="date", columns="code", values=col)
    return panels


def _align(panel: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    return panel.reindex(index=close.index, columns=close.columns)


def amplitude_mean(close, n=20, **_):
    """日内振幅 rolling N 日均值. 高振幅 = 高不确定性, A 股 lottery 偏好."""
    p = _load_ohlc_cache()
    high = _align(p["raw_high"], close)
    low = _align(p["raw_low"], close)
    amp = (high - low) / low.replace(0, np.nan)
    return safe_zscore(mad_clip(amp.rolling(n).mean()))


def overnight_gap(close, n=20, **_):
    """隔夜跳空 (open - prev_close) / prev_close rolling N 日均值. 隔夜 alpha."""
    p = _load_ohlc_cache()
    op = _align(p["raw_open"], close)
    pc = _align(p["raw_close"], close).shift(1)
    gap = (op - pc) / pc.replace(0, np.nan)
    return safe_zscore(mad_clip(gap.rolling(n).mean()))


def close_position(close, n=20, **_):
    """收盘相对日内位置 (close - low) / (high - low). 日内强弱.
    高 = 收在日内高位 (买盘强势), 低 = 收在日内低位 (抛压强势).
    """
    p = _load_ohlc_cache()
    high = _align(p["raw_high"], close)
    low = _align(p["raw_low"], close)
    close_r = _align(p["raw_close"], close)
    pos = (close_r - low) / (high - low + 1e-9)
    return safe_zscore(mad_clip(pos.rolling(n).mean()))


def high_low_breakout(close, n=20, **_):
    """突破因子: close vs N 日 high/low 位置.
    1 = 突破 N 日新高, 0 = 跌破 N 日新低, 0.5 = 中位.
    """
    p = _load_ohlc_cache()
    close_r = _align(p["raw_close"], close)
    rolling_high = close_r.rolling(n).max()
    rolling_low = close_r.rolling(n).min()
    pos = (close_r - rolling_low) / (rolling_high - rolling_low + 1e-9)
    return safe_zscore(mad_clip(pos))
