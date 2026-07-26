"""Regime Engine — 多维市场环境分类.

从单一 PureTrend 扩展为多维 regime 分类器:
  trend     : 小盘趋势方向 (small_nav vs MA)
  volatility: 高波 vs 低波环境 (市场 vol 分位)
  liquidity : 流动性充裕 vs 枯竭 (全市场 turnover 分位)
  breadth   : 普涨 vs 分化 (MA 扩散度)

输出: 每天一个 regime 标签向量, 用于 leg 激活条件匹配.

用法:
  from factory.regime import RegimeEngine
  re = RegimeEngine(close, amount)
  labels = re.classify()  # DataFrame with trend/vol/liquidity/breadth columns
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from factors.small_cap import small_cap_timing


@dataclass(frozen=True)
class RegimeConfig:
    """Regime 分类参数."""
    trend_ma: int = 16          # PureTrend MA 窗口
    vol_window: int = 20        # 波动率计算窗口
    vol_percentile: float = 0.5 # 高波/低波分界线
    liq_window: int = 20        # 流动性计算窗口
    liq_percentile: float = 0.5
    breadth_ma: int = 20        # MA 扩散度 MA 窗口


# B008 修复:frozen dataclass 不可变,模块级单例做缺省,与旧内联缺省语义等价。
_DEFAULT_CFG = RegimeConfig()


class RegimeEngine:
    """多维市场环境分类器."""

    def __init__(
        self,
        close: pd.DataFrame,
        amount: pd.DataFrame,
        cfg: RegimeConfig = _DEFAULT_CFG,
    ):
        self.close = close
        self.amount = amount
        self.cfg = cfg
        self._labels: pd.DataFrame | None = None

    def classify(self) -> pd.DataFrame:
        """返回 date × dimension 的 regime 标签 DataFrame.

        Columns: trend, volatility, liquidity, breadth
        Values:
          trend:      'up' | 'down'
          volatility: 'high' | 'low'
          liquidity:  'plenty' | 'dry'
          breadth:    'wide' | 'narrow'
        """
        if self._labels is not None:
            return self._labels

        close = self.close; amount = self.amount
        idx = close.index

        labels = pd.DataFrame(index=idx)

        # ── 趋势 ──
        # ⚠️ dist[T] 包含 T 日 close, 必须 shift(1) 防未来函数
        # 否则 T 日的 regime 标签和 T 日的收益来自同一信息 → 虚假相关
        _, small_nav, dist = small_cap_timing(close, amount, ma_window=self.cfg.trend_ma)
        dist_lagged = dist.shift(1)
        labels["trend"] = np.where(dist_lagged > 0, "up", "down")
        labels["trend_dist"] = dist_lagged.round(6)

        # ── 波动率 ──
        ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
        active = amount.gt(0) & close.notna()
        mkt_ret = ret.where(active).mean(axis=1)
        mkt_vol = mkt_ret.rolling(self.cfg.vol_window).std()
        vol_median = mkt_vol.expanding().median()
        labels["volatility"] = np.where(mkt_vol > vol_median, "high", "low")
        labels["vol_value"] = mkt_vol.round(6)

        # ── 流动性 ──
        mkt_amount = amount.sum(axis=1, min_count=1)
        liq_ratio = mkt_amount / mkt_amount.rolling(self.cfg.liq_window).mean()
        liq_median = liq_ratio.expanding().median()
        labels["liquidity"] = np.where(liq_ratio > liq_median, "plenty", "dry")
        labels["liq_value"] = liq_ratio.round(4)

        # ── 广度 ──
        ma = close.rolling(self.cfg.breadth_ma).mean()
        valid_ma = ma.notna()
        diffusion = (close.gt(ma) & valid_ma).sum(axis=1) / valid_ma.sum(axis=1).replace(0, np.nan)
        diffusion_median = diffusion.expanding().median()
        labels["breadth"] = np.where(diffusion > diffusion_median, "wide", "narrow")
        labels["breadth_value"] = diffusion.round(4)

        # 清理
        labels = labels.replace([np.inf, -np.inf], np.nan)
        # forward fill NaN from rolling windows at the beginning
        labels = labels.ffill()

        self._labels = labels
        return labels

    def get_regime_mask(self, start: str = "2018-01-01", **conditions) -> pd.Series:
        """返回满足条件的日期 mask.

        Usage:
          re.get_regime_mask(trend='up')                    # PureTrend bull
          re.get_regime_mask(trend='down', volatility='high')  # 下跌+高波
          re.get_regime_mask(trend='down')                  # 下跌(任意波/流/广)
        """
        labels = self.classify()
        mask = pd.Series(True, index=labels.index)
        for dim, val in conditions.items():
            if dim in labels.columns:
                mask = mask & (labels[dim] == val)
        return mask.loc[start:]

    @property
    def trend_up(self) -> pd.Series:
        """PureTrend bull regime mask."""
        return self.get_regime_mask(trend="up")

    @property
    def trend_down(self) -> pd.Series:
        """PureTrend bear regime mask."""
        return self.get_regime_mask(trend="down")

    def summary(self, start: str = "2018-01-01") -> dict:
        """各 regime 的统计摘要."""
        labels = self.classify().loc[start:]
        out = {}
        for dim in ["trend", "volatility", "liquidity", "breadth"]:
            counts = labels[dim].value_counts().to_dict()
            out[dim] = counts
        return out
