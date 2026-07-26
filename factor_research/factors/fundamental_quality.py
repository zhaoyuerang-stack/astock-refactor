"""资产负债表运营质量因子族(孤岛回收①:深层基本面 → frontier 空白区)。

Disposition: probe-pending — 已立项(TASKS「probe 执行·需数据湖」),体检通过才接 DSL 白名单。

为什么(ADR-034 后续):产业基本面 Phase 1 已把资产负债表科目(应收/应付/存货/
票据,`data_lake/financials/balancesheet_all.parquet`,anndate 公告日 PIT)摄取进湖,
但从未进入因子层——而 information_map 的 frontier 恰好指向基本面族是现有池最独立
的空白区。这是"枯竭外探"最便宜的内部矿:数据零抓取,PIT 口径现成。

状态:**probe 前候选**——本模块只提供可计算的因子函数,**尚未**接入 DSL 白名单/
种子(probe-signal-source 步骤 3 正交性+IS/OOS 体检通过后才走步骤 4 接工厂,
证据先行,勿倒置;R-LLM-001:本文件不含任何有效性声明)。probe 命令(须在有
数据湖的机器上跑):
  python scripts/research/signal_source_probe.py \
    --factor factors.fundamental_quality:bargaining_power --start 2018-01-01 \
    --cutoff 2022-12-31 --end 2024-12-31

PIT 纪律(R-DATA-003):数据经 lake.load_tushare_panel("balancesheet")(anndate
公告日 ffill,T 日只见 T 日前已披露的报表);本模块不再额外 shift。
经济机制(每因子 docstring 一句话):存量占款结构与其变化,与价格/量能族信息源正交。
"""
from __future__ import annotations

from functools import lru_cache

import pandas as pd

from factors.utils import mad_clip, safe_zscore

_FIELDS = ["total_assets", "accounts_receiv", "notes_receiv",
           "acct_payable", "notes_payable", "inventories"]


@lru_cache(maxsize=1)
def _load_balancesheet_cache():
    """加载资负表 panel(anndate PIT ffill),缓存避免重复 I/O(同 fundamental.py 模式)。"""
    from lake.load_lake import load_tushare_panel

    trade_dates = pd.date_range("2010-01-01", "2030-01-01", freq="B")
    return load_tushare_panel("balancesheet", trade_dates, fields=_FIELDS)


def _require(bs: dict, fields: list[str]) -> None:
    """缺字段/空面板必须显式失败——静默给零分 = 半截口径混进候选池(R-DATA 系)。"""
    missing = [f for f in fields if f not in bs or getattr(bs[f], "empty", True)]
    if missing:
        raise ValueError(f"balancesheet 面板缺字段 {missing};先跑 update_tushare --interface balancesheet")


def _align(panel: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    """anndate ffill 面板 → 对齐 close 交易日与股票池(基本面独有票丢弃)。"""
    out = panel.reindex(close.index).ffill()
    return out.reindex(columns=close.columns)


# ── 纯核心(注入面板,可无数据单测) ───────────────────────────────────────────

def bargaining_power_core(bs: dict, close: pd.DataFrame) -> pd.DataFrame:
    """净占款能力 = (应付+应付票据 − 应收−应收票据) / 总资产。

    机制:对上下游净占用资金 = 产业链议价权(供应商愿意被欠款、客户先款后货);
    纯存量截面比较,无需流量年化。高 = 议价权强。
    """
    _require(bs, _FIELDS[:5])
    payable = _align(bs["acct_payable"], close).fillna(0.0) + _align(bs["notes_payable"], close).fillna(0.0)
    receiv = _align(bs["accounts_receiv"], close).fillna(0.0) + _align(bs["notes_receiv"], close).fillna(0.0)
    assets = _align(bs["total_assets"], close)
    raw = (payable - receiv) / assets.where(assets > 0)
    return safe_zscore(mad_clip(raw))


def receivable_intensity_chg_core(bs: dict, close: pd.DataFrame, window: int = 252) -> pd.DataFrame:
    """应收强度改善 = −Δ(应收/总资产, window 日)。

    机制:应收占比抬升 = 靠放宽信用赊销撑收入 = 盈余质量恶化(取负号,高 = 改善)。
    Δ 的两端取值在各自时点均已披露(ffill 面板),无前瞻。
    """
    _require(bs, ["total_assets", "accounts_receiv", "notes_receiv"])
    receiv = _align(bs["accounts_receiv"], close).fillna(0.0) + _align(bs["notes_receiv"], close).fillna(0.0)
    assets = _align(bs["total_assets"], close)
    intensity = receiv / assets.where(assets > 0)
    return safe_zscore(mad_clip(-intensity.diff(window)))


def inventory_intensity_chg_core(bs: dict, close: pd.DataFrame, window: int = 252) -> pd.DataFrame:
    """存货强度改善 = −Δ(存货/总资产, window 日)。

    机制:存货占比抬升 = 压库存/滞销风险(减值前兆);去库存 = 经营改善(高 = 改善)。
    """
    _require(bs, ["total_assets", "inventories"])
    inv = _align(bs["inventories"], close)
    assets = _align(bs["total_assets"], close)
    intensity = inv / assets.where(assets > 0)
    return safe_zscore(mad_clip(-intensity.diff(window)))


# ── probe 入口(签名对齐 signal_source_probe:(close, **params)) ─────────────

def bargaining_power(close, **_):
    return bargaining_power_core(_load_balancesheet_cache(), close)


def receivable_intensity_chg(close, window: int = 252, **_):
    return receivable_intensity_chg_core(_load_balancesheet_cache(), close, window=int(window))


def inventory_intensity_chg(close, window: int = 252, **_):
    return inventory_intensity_chg_core(_load_balancesheet_cache(), close, window=int(window))
