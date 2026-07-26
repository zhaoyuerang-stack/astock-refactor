"""Factor-analysis utilities: IC calculation & stratified return tests.

This module contains **research-level** factor diagnostics (IC, stratify,
long-short).  For production portfolio backtests use ``core/engine.BacktestEngine``.

Renamed from ``engine/backtest.py`` (2026-06) to eliminate the naming clash
with ``core/backtest.py`` — "backtest" now refers exclusively to the portfolio-
weight simulation in ``core/``.
"""
import numpy as np
import pandas as pd
from scipy.stats import spearmanr


def calc_ic(factor: pd.DataFrame, forward_ret: pd.DataFrame, method: str = "rank") -> pd.Series:
    """
    计算每个截面日期的IC
    factor/forward_ret: date×code 的DataFrame
    method: 'rank'=RankIC(ICIR更稳定), 'pearson'=PearsonIC
    """
    dates = factor.index.intersection(forward_ret.index)
    ics = {}
    for dt in dates:
        f = factor.loc[dt].dropna()
        r = forward_ret.loc[dt].dropna()
        common = f.index.intersection(r.index)
        if len(common) < 30:
            continue
        fv, rv = f[common].values, r[common].values
        if np.nanstd(fv) == 0 or np.nanstd(rv) == 0:
            ic = np.nan
        elif method == "rank":
            ic, _ = spearmanr(fv, rv)
        else:
            ic = np.corrcoef(fv, rv)[0, 1]
        ics[dt] = ic
    return pd.Series(ics).sort_index()


def ic_summary(ic: pd.Series) -> dict:
    return {
        "IC_mean": ic.mean(),
        "IC_std": ic.std(),
        "ICIR": ic.mean() / ic.std() if ic.std() > 0 else np.nan,
        "IC>0_ratio": (ic > 0).mean(),
        "|IC|>0.02_ratio": (ic.abs() > 0.02).mean(),
        "count": len(ic),
    }


def newey_west_icir(daily_ic, max_lag: int | None = None) -> float:
    """Newey-West 重叠校正的 ICIR(Bartlett 核长期方差作分母)。

    horizon>1 的 IC 序列因每日重叠强自相关——同一信息被重复计入,raw ICIR=mean/std
    的分母被压低,系统性虚高(全市场 h=20 实测 raw/nw≈3.5x,见 LESSONS 2026-06-14)。
    NW 用长期方差给出诚实绝对量级。max_lag 按 **IC 序列**自相关长度(≈horizon)设,
    不按因子不变天数设(两者差一个数量级)。机制 port 自第二系统 src/eval/overlap.py
    (结论本地重算,见 auto-memory)。
    """
    ic = np.asarray(daily_ic, dtype=float)
    ic = ic[~np.isnan(ic)]
    n = len(ic)
    if n < 2:
        return float("nan")
    if max_lag is None:
        max_lag = int(n ** 0.25)
    max_lag = max(1, min(max_lag, n - 1))
    mean = ic.mean()
    var = ic.var()
    lr_var = var  # lag 0
    for lag in range(1, max_lag + 1):
        w = 1.0 - lag / (max_lag + 1)  # Bartlett 权重
        ac = np.corrcoef(ic[:-lag], ic[lag:])[0, 1]
        if not np.isnan(ac):
            lr_var += 2 * w * ac * var
    return abs(mean) / np.sqrt(max(lr_var, 1e-12))


def stratify_return(
    factor: pd.DataFrame,
    forward_ret: pd.DataFrame,
    n_quantile: int = 5,
) -> pd.DataFrame:
    """
    分层回测：每个截面按因子值分成n_quantile组，计算各组平均收益
    返回 date×group 的收益率DataFrame
    """
    dates = factor.index.intersection(forward_ret.index)
    records = []
    for dt in dates:
        f = factor.loc[dt].dropna()
        r = forward_ret.loc[dt].dropna()
        common = f.index.intersection(r.index)
        if len(common) < n_quantile * 5:
            continue
        labels = pd.qcut(f[common], n_quantile, labels=False, duplicates="drop")
        group_ret = r[common].groupby(labels).mean()
        row = {"date": dt}
        for g, v in group_ret.items():
            row[f"Q{int(g)+1}"] = v
        records.append(row)
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records).set_index("date").sort_index()
    return df


def cumulative_return(group_ret: pd.DataFrame) -> pd.DataFrame:
    """将分层日度收益转为累计净值"""
    return (1 + group_ret).cumprod()


def long_short_return(group_ret: pd.DataFrame) -> pd.Series:
    """多空组合收益 = Q5 - Q1"""
    cols = sorted(group_ret.columns)
    return group_ret[cols[-1]] - group_ret[cols[0]]


def factor_summary(
    factor: pd.DataFrame,
    forward_ret: pd.DataFrame,
    factor_name: str = "factor",
    n_quantile: int = 5,
) -> dict:
    ic = calc_ic(factor, forward_ret)
    strat = stratify_return(factor, forward_ret, n_quantile)
    summary = ic_summary(ic)
    summary["factor"] = factor_name
    if strat.empty:
        summary["LS_annual"] = np.nan
        summary["LS_sharpe"] = np.nan
        summary["LS_maxdd"] = np.nan
    else:
        ls = long_short_return(strat)
        summary["LS_annual"] = ls.mean() * 252
        summary["LS_sharpe"] = ls.mean() / ls.std() * np.sqrt(252) if ls.std() > 0 else np.nan
        summary["LS_maxdd"] = ((1 + ls).cumprod() / (1 + ls).cumprod().cummax() - 1).min()
    return summary
