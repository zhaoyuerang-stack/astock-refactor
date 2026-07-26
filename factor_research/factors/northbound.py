"""北向资金因子族 — 与价量/市值/流动性簇正交的独立信息源(沪深股通持仓 & 流向)。

立项动机(打破「研究引擎一直坍缩到小盘」的机制问题):现有因子绝大多数取材 pe/amount/
turnover/illiq,即市值/流动性簇,搜索空间天生是小盘形状。本族从**北向持仓**这一正交数据
取材,给搜索空间加上与 size/liquidity 正交的维度。

L0 探索证据(2018-2024 月度,北向覆盖 774 可投票):``northbound_accumulation`` 20d
rank-IC ≈ 0.022,去 size/流动性中性化后**残差 IC 不塌(≈0.023)**、与小市值 corr ≈ -0.05、
与流动性 corr ≈ 0.03 —— 携带正交于小盘/流动性的增量信息,且就在**可投(大中盘)universe**
上有效(同期纯小市值在该 universe IC ≈ 0)。**这是 L0 原始因子,非已验证 alpha**:未扣成本、
无 DSR/PBO/holdout、未过 9-Gate。入册仍须走 workflow(R-WF-001)。

数据/防未来:``capital/northbound_all.parquet``,经 ``lake.load_lake.load_capital_panel``
统一加载——持仓 T 日盘后披露(T+1 起可见),loader **已对面板 shift(1)**,故本模块因子内
**不再额外 shift**。所有因子返回 date×code 截面 z-score(与 ``capital_flow`` 同口径)。
"""
from functools import lru_cache

import numpy as np
import pandas as pd

from factors.registry import register_factor
from factors.utils import mad_clip, safe_zscore

# 取北向子集字段(load_capital_panel 默认含 margin+northbound,这里只要北向持仓口径)
_NB_FIELDS = (
    "northbound_hold_pct",            # 北向持股占流通比(已自归一,跨股可比)
    "northbound_hold_shares_chg_1d",  # 北向持股日变动(股)
    "northbound_buy_value_1d",        # 北向当日买入额
)


@lru_cache(maxsize=1)
def _load_nb_cache():
    """加载北向面板 {field: date×code}(loader 已 PIT shift(1))。缓存一次。"""
    from lake.load_lake import load_capital_panel

    trade_dates = pd.date_range("2010-01-01", "2030-01-01", freq="B")
    return load_capital_panel(trade_dates, fields=list(_NB_FIELDS))


def _align_to_close(panel: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    """把北向面板对齐到 close 的交易日 index 与股票 columns。"""
    out = panel.reindex(close.index)
    common = close.columns.intersection(out.columns)
    return out[common].reindex(columns=close.columns)


@register_factor(
    "northbound_accumulation",
    definition=(
        "北向持股占流通比(loader 已 PIT shift(1))的 window 日差分,"
        "MAD截尾+截面z;正=外资近窗净增持"
    ),
    params={"window": (5, 120)},
    data=("capital/northbound",),
    input="close",
    arg_map={"window": "window"},
    searchable=True,
    evidence=(
        "research_ledger:e6e655401623899d;"
        "knowledge/direction_registry:northbound-holder-flow-weak-longonly"
    ),
)
def northbound_accumulation(close, window: int = 20, **_):
    """北向持股比例的 ``window`` 日变化(累积/流入)。高值 = 外资近期净增持。

    L0 验证的正交主信号:残差 IC(去 size/流动性)不塌、与小盘 corr ≈ 0。
    """
    hold_pct = _align_to_close(_load_nb_cache()["northbound_hold_pct"], close)
    acc = hold_pct - hold_pct.shift(window)
    return safe_zscore(mad_clip(acc.replace([np.inf, -np.inf], np.nan)))


@register_factor(
    "northbound_hold_level",
    definition=(
        "北向持股占流通比水平(loader 已 PIT shift(1)),MAD截尾+截面z;正=外资重仓"
    ),
    data=("capital/northbound",),
    input="close",
    searchable=True,
    evidence=(
        "research_ledger:e6e655401623899d;"
        "knowledge/direction_registry:northbound-holder-flow-weak-longonly"
    ),
)
def northbound_hold_level(close, **_):
    """北向持股比例**水平**(外资青睐度)。高值 = 外资重仓。"""
    hold_pct = _align_to_close(_load_nb_cache()["northbound_hold_pct"], close)
    return safe_zscore(mad_clip(hold_pct.replace([np.inf, -np.inf], np.nan)))


@register_factor(
    "northbound_flow_strength",
    definition=(
        "北向持股占流通比短窗 window 日差分(默认5,同源 accumulation 不同默认窗),"
        "MAD截尾+截面z;正=近端外资净流入强"
    ),
    params={"window": (3, 20)},
    data=("capital/northbound",),
    input="close",
    arg_map={"window": "window"},
    searchable=True,
    evidence=(
        "research_ledger:e6e655401623899d;"
        "knowledge/direction_registry:northbound-holder-flow-weak-longonly"
    ),
)
def northbound_flow_strength(close, window: int = 5, **_):
    """短窗北向流入强度:持股比例 ``window`` 日变化(默认 5d,近端动量)。

    与 ``northbound_accumulation`` 同源不同窗,捕捉更快的资金切换。
    """
    return northbound_accumulation(close, window=window)


# 工厂/autoresearch 可发现的族成员(name → callable),供 L0-L3 cheap-first 筛选取用。
NORTHBOUND_FACTORS = {
    "northbound_accumulation_20d": lambda close, **k: northbound_accumulation(close, window=20),
    "northbound_accumulation_60d": lambda close, **k: northbound_accumulation(close, window=60),
    "northbound_hold_level": northbound_hold_level,
    "northbound_flow_strength_5d": lambda close, **k: northbound_flow_strength(close, window=5),
}
