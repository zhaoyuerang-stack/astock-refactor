"""市场 regime 分类器 — 4 档 BULL / BEAR / CHOP / CRISIS。

输入: close panel (date × code)
输出: regime label Series (date → str)

逻辑（简单稳健，不需要 VIX 等外部数据）：
  market_ret = 截面 mean daily return
  mom_6m    = 滚动 126 日累计 market return
  vol_20    = 滚动 20 日 market return std × sqrt(252)

  优先级:
    CRISIS: vol_20 > 0.40 (年化 40%+ 波动 = 极端市场)
    BULL:   mom_6m > +0.15
    BEAR:   mom_6m < -0.10
    CHOP:   其余
"""
import numpy as np
import pandas as pd

REGIME_LABELS = ["bull", "bear", "chop", "crisis"]


def classify_regime(
    close: pd.DataFrame,
    long_window: int = 126,
    short_window: int = 20,
    bull_threshold: float = 0.15,
    bear_threshold: float = -0.10,
    crisis_vol_threshold: float = 0.40,
) -> pd.Series:
    """返回 date → regime label 的 Series.

    long_window: 6 个月累计动量的窗口
    short_window: 实现波动率窗口
    """
    daily_ret = (
        close.pct_change(fill_method=None)
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
    )
    # 截面平均（简单 market proxy）
    market_ret = daily_ret.mean(axis=1)

    # Rolling features
    mom = market_ret.rolling(long_window).sum()
    vol = market_ret.rolling(short_window).std() * np.sqrt(252)

    regime = pd.Series("chop", index=close.index)
    regime[mom > bull_threshold] = "bull"
    regime[mom < bear_threshold] = "bear"
    # crisis 优先（覆盖 bull/bear）
    regime[vol > crisis_vol_threshold] = "crisis"
    # 前期数据不足时标 NaN-equivalent
    regime[mom.isna()] = "warmup"
    return regime
