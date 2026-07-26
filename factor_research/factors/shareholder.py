"""股东行为因子 — 与价量(illiquidity/momentum/volatility)簇正交的独立数据族。

口径(LOOP_ENGINEERING.md #5 独立数据族隔离岛):
  holder_count_chg  户数环比变化(状态量,anndate ffill;减少→筹码集中→因子值升高)
  holdertrade_net   高管/重要股东滚动净增减持比例(事件流;不可 ffill——事件落在公告日
                     当天即耗尽,须 snap 到下一交易日再 rolling sum,否则 ffill 会把
                     单日事件重复计入后续多天,虚增信号)

数据来源:holder/holdernumber_all.parquet(季度披露)、holder/holdertrade_all.parquet
(逐笔披露),经 lake.load_lake 统一 anndate 对齐(防未来:T 日只用 T 日前已公告)。
"""
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from factors.registry import register_factor
from factors.utils import mad_clip, safe_zscore

_LAKE = Path(__file__).resolve().parent.parent / "data_lake"


@lru_cache(maxsize=1)
def _load_holdernumber_cache():
    from lake.load_lake import load_tushare_panel
    trade_dates = pd.date_range("2010-01-01", "2030-01-01", freq="B")
    return load_tushare_panel("holdernumber", trade_dates, fields=["holder_num"])["holder_num"]


def _align_to_close(panel: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    out = panel.reindex(close.index).ffill()
    common = close.columns.intersection(out.columns)
    return out[common].reindex(columns=close.columns)


@register_factor("holder_count_chg",
                 definition="股东户数 window 期环比变化取负,anndate ffill 对齐交易日后 MAD截尾+截面z;正=户数减少(筹码集中,预期正超额)",
                 params={"window": (40, 240)},
                 data=("holder/holdernumber",), input="close", arg_map={"window": "window"},
                 searchable=True,
                 evidence="research_ledger:e6e655401623899d;knowledge/direction_registry:northbound-holder-flow-weak-longonly(残差IC真正交但量级弱,DEPRIORITIZE)")
def holder_count_chg(close, window: int = 60, **_):
    """股东户数环比变化,负号:户数减少 → 筹码集中 → 因子值升高(买入)。"""
    panel = _align_to_close(_load_holdernumber_cache(), close)
    chg = -panel.pct_change(window)
    return safe_zscore(mad_clip(chg.replace([np.inf, -np.inf], np.nan)))


@lru_cache(maxsize=1)
def _load_holdertrade_signed() -> pd.DataFrame:
    """date×code 净增减持比例(公告日原始事件,未对齐交易日历,未 rolling)。"""
    fp = _LAKE / "holder" / "holdertrade_all.parquet"
    df = pd.read_parquet(fp, columns=["ts_code", "ann_date", "in_de", "change_ratio"])
    df = df.dropna(subset=["ann_date", "change_ratio"])
    df["code"] = df["ts_code"].str.split(".").str[0]
    df["date"] = pd.to_datetime(df["ann_date"].astype(str))
    sign = df["in_de"].map({"IN": 1.0, "DE": -1.0}).fillna(0.0)
    df["signed"] = sign * df["change_ratio"]
    return df.groupby(["date", "code"])["signed"].sum().unstack("code")


def _snap_to_next_trading_day(df: pd.DataFrame, trade_idx: pd.DatetimeIndex) -> pd.DataFrame:
    """事件落在非交易日(周末/节假日)时 snap 到下一交易日,同日多事件求和聚合。

    不可用 ffill 替代:ffill 会把单日事件的值在下次公告前的每一天重复计入,
    rolling sum 会严重虚增——这是状态量(户数)与事件流(增减持)口径的关键区别。
    """
    trade_idx = trade_idx.sort_values()
    df = df.sort_index()
    pos = trade_idx.searchsorted(df.index, side="left")
    mask = pos < len(trade_idx)
    out = df.loc[mask].copy()
    out.index = trade_idx[pos[mask]]
    return out.groupby(level=0).sum()


@register_factor(
    "holdertrade_net",
    definition=(
        "高管/重要股东增减持 signed change_ratio 按公告日 snap 到下一交易日,"
        "window 内 rolling sum 后截面 rank(pct);正=窗口内净增持(事件流,不可 ffill)"
    ),
    params={"window": (40, 250)},
    data=("holder/holdertrade",),
    input="close",
    arg_map={"window": "window"},
    searchable=True,
    evidence=(
        "research_ledger:e6e655401623899d;"
        "knowledge/direction_registry:northbound-holder-flow-weak-longonly"
    ),
)
def holdertrade_net(close, window: int = 120, **_):
    """高管/重要股东滚动净增减持比例(窗口内事件求和;净增持 → 因子值升高)。

    事件极稀疏(多数股票多数窗口内无任何披露,横截面 >50% 为 0)——mad_clip 的
    MAD 在该情形下恒为 0,会把整行压成常数,抹掉全部信号。用 rank(pct) 代替
    mad_clip+zscore:0 的并列质量只占一段中间秩,不吞掉少数非零事件的排序信息。
    """
    raw = _load_holdertrade_signed()
    snapped = _snap_to_next_trading_day(raw, close.index)
    aligned = snapped.reindex(close.index, fill_value=0.0)
    common = close.columns.intersection(aligned.columns)
    aligned = aligned[common].reindex(columns=close.columns, fill_value=0.0)
    rolled = aligned.rolling(window).sum()
    return rolled.rank(axis=1, pct=True)
