"""基本面 factor 适配器 — 用 fundamental_batch.parquet 长表(PIT 已对齐)。

数据已防未来函数:lake.load_fundamental_panel 用 avail_date 公告日 ffill,
T 日只用 T 日前已披露的财务。

设计:每个 factor 签名 (close, **params),内部 lru_cache 加载 fundamental panel。
mutate_existing.py 注册时 data_dependencies 仍写 ('price/close',),engine dispatch
喂 close 即可,fundamental panel 由本模块内部 cache 加载。

第一版只暴露 6 个核心字段:
  - net_profit_yoy   (NPY,净利润同比增长 — size_earnings v1.0 成长腿;台账状态=参考)
  - revenue_yoy      (营收同比)
  - roe              (净资产收益率)
  - gross_margin     (毛利率)
  - bp_proxy         (1/PB ≈ bps/close,价值因子)
  - ep_proxy         (1/PE ≈ eps/close,价值因子)
"""
from functools import lru_cache

import numpy as np
import pandas as pd

from factors.registry import register_factor
from factors.utils import mad_clip, safe_zscore


@lru_cache(maxsize=1)
def _load_fundamental_cache():
    """加载完整 fundamental panel,缓存避免重复 I/O."""
    from lake.load_lake import load_fundamental_panel
    # 用宽日期范围拿全历史
    trade_dates = pd.date_range("2010-01-01", "2030-01-01", freq="B")
    return load_fundamental_panel(
        trade_dates, codes=None,
        fields=["roe", "eps", "eps_ttm", "bps", "revenue", "net_profit",
                "gross_margin", "revenue_yoy", "net_profit_yoy"],
    )


def _align_to_close(panel_field: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    """Panel(date×code,业务日历) → align 到 close 交易日索引 + 列。"""
    if panel_field.empty:
        raise ValueError("empty fundamental panel field")
    # Index: close trading days
    out = panel_field.reindex(close.index).ffill()
    # Columns: close universe (drop fundamental-only stocks)
    common_cols = close.columns.intersection(out.columns)
    return out[common_cols].reindex(columns=close.columns)


_FUND_EVIDENCE = (
    "knowledge/direction_registry:frontier-fundamental-family;"
    "reports/research/metasearch_findings_20260623.md"
)


@register_factor(
    "net_profit_yoy",
    definition=(
        "净利润同比(net_profit_yoy,avail_date/ann_date PIT ffill 对齐交易日)"
        "MAD截尾+截面z;正=盈利增速高"
    ),
    data=("fundamental/net_profit_yoy",),
    input="close",
    searchable=True,
    evidence=_FUND_EVIDENCE,
)
def net_profit_yoy(close, **_):
    """净利润同比增长 — size_earnings v1.0(台账状态=参考,passed_all=False)成长腿.

    standalone 全市场线性 IC 稳定为负(round7 probe raw IS=-0.0115/OOS=-0.0182);
    台账 v1.0 的正 IC(0.051)属 0.5*size+0.5*npy 混合因子、由 size 腿主导,
    不构成本因子单独正向证据。
    见 reports/research/probe_round7_fundamental_net_profit_yoy.json。
    """
    panel = _load_fundamental_cache()
    aligned = _align_to_close(panel["net_profit_yoy"], close)
    return safe_zscore(mad_clip(aligned))


@register_factor(
    "revenue_yoy",
    definition=(
        "营收同比(revenue_yoy,PIT ffill 对齐交易日)MAD截尾+截面z;正=营收增速高"
    ),
    data=("fundamental/revenue_yoy",),
    input="close",
    searchable=True,
    evidence=_FUND_EVIDENCE,
)
def revenue_yoy(close, **_):
    """营收同比增长 — 顶层成长信号."""
    panel = _load_fundamental_cache()
    aligned = _align_to_close(panel["revenue_yoy"], close)
    return safe_zscore(mad_clip(aligned))


@register_factor(
    "roe",
    definition=(
        "净资产收益率 ROE(PIT ffill 对齐交易日)MAD截尾+截面z;正=ROE 高"
    ),
    data=("fundamental/roe",),
    input="close",
    searchable=True,
    evidence=_FUND_EVIDENCE,
)
def roe(close, **_):
    """ROE — 经典质量因子."""
    panel = _load_fundamental_cache()
    aligned = _align_to_close(panel["roe"], close)
    return safe_zscore(mad_clip(aligned))


@register_factor("gross_margin",
                 definition="毛利率(gross_margin,已按 avail_date/ann_date PIT ffill 对齐交易日)MAD截尾+截面z;正=毛利率高(盈利质量/护城河代理)",
                 data=("fundamental/gross_margin",), input="close",
                 searchable=False)
def gross_margin(close, **_):
    """毛利率 — 质量信号."""
    panel = _load_fundamental_cache()
    aligned = _align_to_close(panel["gross_margin"], close)
    return safe_zscore(mad_clip(aligned))


@lru_cache(maxsize=1)
def _load_raw_close_cache():
    from lake.load_lake import load_raw_close
    return load_raw_close(start="2010-01-01")


@register_factor(
    "bp_proxy",
    definition=(
        "BP 代理 = bps / raw_close(不复权价),PIT ffill 对齐后 MAD截尾+截面z;"
        "正=账面市值比高(价值)"
    ),
    data=("price/close", "fundamental/bps"),
    input="close",
    searchable=True,
    evidence=_FUND_EVIDENCE,
)
def bp_proxy(close, **_):
    """BP = bps / raw_close. 价值因子 (contrarian, 选股范围多大盘金融/周期)."""
    panel = _load_fundamental_cache()
    raw = _load_raw_close_cache().reindex(index=close.index, columns=close.columns)
    bps = _align_to_close(panel["bps"], close)
    bp = bps / raw.replace(0, np.nan)
    return safe_zscore(mad_clip(bp))


@register_factor(
    "ep_proxy",
    definition=(
        "EP 代理 = eps_ttm / raw_close(不复权价),PIT ffill 对齐后 MAD截尾+截面z;"
        "正=盈利收益率高(价值)"
    ),
    data=("price/close", "fundamental/eps_ttm"),
    input="close",
    searchable=True,
    evidence=_FUND_EVIDENCE,
)
def ep_proxy(close, **_):
    """EP = eps_ttm / raw_close. 价值因子."""
    panel = _load_fundamental_cache()
    raw = _load_raw_close_cache().reindex(index=close.index, columns=close.columns)
    eps = _align_to_close(panel["eps_ttm"], close)
    ep = eps / raw.replace(0, np.nan)
    return safe_zscore(mad_clip(ep))


def cfo_quality(close, **_):
    """cfo_ps z-score. 现金流质量 (与 net_profit 互补,绕过应收账款操纵)."""
    panel = _load_fundamental_cache()
    aligned = _align_to_close(panel["cfo_ps"], close)
    return safe_zscore(mad_clip(aligned))
