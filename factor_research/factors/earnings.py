"""业绩 SUE(标准化未预期盈余)因子族 — 事件/基本面正交数据源。

Disposition: dormant — 零消费者且无在案 probe 立项;复活须先立 probe-signal-source 体检项(R-ARCH-005 精神)。

立项动机(打破小盘坍缩,同 northbound):现有因子取材偏价量/市值/流动性,缺事件/盈余维度。
业绩预告(forecast)+ 快报(express)是早于正式财报的盈余信号,与价量簇正交。

防未来(R-DATA-003):forecast/express 经 ``lake.load_lake.load_tushare_panel`` 的 anndate 口径
加载——按**公告日 ann_date** ffill 对齐(T 日只看 ann_date<=T 的已公告值),loader 已防未来,
因子内不再额外 shift。所有因子返回 date×code 截面 z-score(与 capital_flow/northbound 同口径)。

口径:盈余惊喜以 express 实际(yoy_net_profit,快报净利同比)优先,缺则用 forecast 预告
(p_change 净利变动幅度中点)。anndate ffill 使惊喜值在下次公告前持续有效(慢变盈余信号)。

L0 性质:这是原始因子,**非已验证 alpha**(无成本/DSR/PBO/9-Gate);入册走 workflow(R-WF-001)。
"""
from functools import lru_cache

import numpy as np
import pandas as pd

from factors.utils import mad_clip, safe_zscore


@lru_cache(maxsize=1)
def _load_earnings_cache():
    """加载 forecast/express 面板(loader 已 anndate ffill 防未来)。缓存一次。"""
    from lake.load_lake import load_tushare_panel

    dates = pd.date_range("2010-01-01", "2030-01-01", freq="B")
    fc = load_tushare_panel("forecast", dates, fields=["p_change_min", "p_change_max"])
    ex = load_tushare_panel("express", dates, fields=["yoy_net_profit"])
    return fc, ex


def _align(panel: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    out = panel.reindex(close.index)
    common = close.columns.intersection(out.columns)
    return out[common].reindex(columns=close.columns)


def _forecast_midpoint(close: pd.DataFrame) -> pd.DataFrame:
    fc, _ = _load_earnings_cache()
    lo = _align(fc["p_change_min"], close)
    hi = _align(fc["p_change_max"], close)
    return (lo + hi) / 2


def sue(close, **_):
    """标准化未预期盈余:实际(快报 yoy_net_profit)优先、缺则预告(p_change 中点),截面 z-score。

    高值 = 已公告盈余同比大幅正向。PIT(公告日 ffill)。
    """
    _, ex = _load_earnings_cache()
    yoy = _align(ex["yoy_net_profit"], close)
    pmid = _forecast_midpoint(close)
    surprise = yoy.where(yoy.notna(), pmid)  # express 实际优先,缺则 forecast 预告
    return safe_zscore(mad_clip(surprise.replace([np.inf, -np.inf], np.nan)))


def earnings_forecast_surprise(close, **_):
    """仅预告口径的盈余惊喜(p_change 净利变动幅度中点),截面 z-score。"""
    return safe_zscore(mad_clip(_forecast_midpoint(close).replace([np.inf, -np.inf], np.nan)))


# 工厂/autoresearch 可发现的族成员(name → callable)
EARNINGS_FACTORS = {
    "sue": sue,
    "earnings_forecast_surprise": earnings_forecast_surprise,
}
