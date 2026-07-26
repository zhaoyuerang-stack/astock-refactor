"""Performance metrics from a return series."""
import numpy as np
import pandas as pd

TARGET_ANNUAL = 0.15   # 单母策略入册门槛（年化>15% / 回撤<20%）
TARGET_MAXDD = 0.20    # 原 35%/15% 锚定 data_full 水分，已退役


def compute_hit(annual: float, maxdd: float,
                target_annual: float = TARGET_ANNUAL,
                target_maxdd: float = TARGET_MAXDD) -> bool:
    """单体入册「达标」的唯一权威判定（口径锚定 CLAUDE.md 宪法：年化>15% / 回撤<20%）。

    所有需要 hit 的地方都必须调本函数，禁止各自重写阈值或手填 hit——这是防止
    「修记分牌」的机械执行点。注意是严格不等号：年化恰好 15% 或回撤恰好 20% 均不达标。
    阈值可被回测 config 覆盖（per-run），但不等号语义恒为严格，杜绝边界口径分叉。
    """
    if annual is None or maxdd is None:
        return False
    return (float(annual) > target_annual) and (abs(float(maxdd)) < target_maxdd)


def max_drawdown(ret):
    """Maximum drawdown from a return series."""
    if len(ret) == 0:
        return np.nan
    cum = (1 + ret).cumprod()
    return float((cum / cum.cummax() - 1).min())


# ── canonical 标量公式:全仓绩效口径的唯一权威 ──────────────────────────
# BacktestResult(core.engine) 与本模块 metrics() 此前各自内联同一套公式,靠注释
# 承诺一致(架构评审:口径静默分叉风险)。现两者均委托下面四个函数;改年化/夏普/
# 卡玛口径只许改这里(并按 §17 走文档同步),禁止在出口处再写一遍公式。


def annual_return(ret) -> float:
    """年化收益 = 日均收益 × 252。"""
    return float(ret.mean() * 252)


def annual_vol(ret) -> float:
    """年化波动 = 日波动 × √252(pandas std,ddof=1)。"""
    return float(ret.std() * np.sqrt(252))


def sharpe_ratio(ret) -> float:
    """Sharpe = 年化收益 / 年化波动(rf=0 口径);波动非正返回 0.0。"""
    vol = annual_vol(ret)
    return annual_return(ret) / vol if vol > 0 else 0.0


def calmar_ratio(ret) -> float:
    """Calmar = 年化收益 / |最大回撤|;回撤非负(无回撤/空序列)返回 0.0。"""
    dd = max_drawdown(ret)
    return annual_return(ret) / abs(dd) if dd < 0 else 0.0


def institutional_metrics(ret, bench=None) -> dict:
    """机构级风险/分布指标（Sortino/VaR/CVaR/偏度峰度/尾比；给基准则加 IR/TE/Alpha/Beta/捕获率）。

    与 ``metrics`` 的核心字段(annual/vol/sharpe/maxdd/calmar)互补，不重复。
    全部带零除/空序列保护，返回值均为原生 float / bool。
    """
    out: dict = {}
    ret = pd.Series(ret).dropna()
    if len(ret) < 2:
        return out

    annual = float(ret.mean() * 252)
    # 下行风险：Sortino（目标 0），仅惩罚负收益波动
    downside = ret[ret < 0]
    downside_vol = float(np.sqrt((downside ** 2).mean()) * np.sqrt(252)) if len(downside) else 0.0
    out["sortino"] = annual / downside_vol if downside_vol > 0 else 0.0
    out["downside_vol"] = downside_vol

    # 尾部风险：日度 95% VaR / CVaR（正数 = 损失幅度）
    q05 = float(np.quantile(ret, 0.05))
    out["var_95"] = float(-q05)
    tail = ret[ret <= q05]
    out["cvar_95"] = float(-tail.mean()) if len(tail) else float(-q05)

    # 分布形状
    out["skew"] = float(ret.skew())
    out["kurtosis_excess"] = float(ret.kurt())  # pandas 返回超额峰度（正态=0）
    q95 = float(np.quantile(ret, 0.95))
    out["tail_ratio"] = float(abs(q95) / abs(q05)) if q05 != 0 else 0.0

    # 右尾：best 5% 日均收益（右尾暴利厚度，正数=收益幅度；与 cvar_95 左尾损失对称）
    rtail = ret[ret >= q95]
    out["cvar_right"] = float(rtail.mean()) if len(rtail) else q95

    # 基准相对指标
    if bench is not None:
        bench = pd.Series(bench).dropna()
        common = ret.index.intersection(bench.index)
        if len(common) >= 2:
            r, b = ret.reindex(common), bench.reindex(common)
            excess = r - b
            te = float(excess.std() * np.sqrt(252))
            out["excess_annual"] = float(excess.mean() * 252)
            out["tracking_error"] = te
            out["info_ratio"] = float(excess.mean() * 252 / te) if te > 0 else 0.0
            var_b = float(b.var())
            beta = float(np.cov(r, b)[0, 1] / var_b) if var_b > 0 else 0.0
            out["beta"] = beta
            out["alpha_annual"] = float(r.mean() * 252 - beta * b.mean() * 252)
            up, down = b > 0, b < 0
            out["up_capture"] = float(r[up].mean() / b[up].mean()) if up.any() and b[up].mean() != 0 else 0.0
            out["down_capture"] = float(r[down].mean() / b[down].mean()) if down.any() and b[down].mean() != 0 else 0.0
            # 右尾捕获能力的核心标量：涨时跟得紧、跌时不必跟 → 越大越好（≈0 = 对称漏血）
            out["capture_spread"] = out["up_capture"] - out["down_capture"]
    return out


def winner_concentration(weights_history, close, win_start=None) -> dict:
    """选股层右尾画像：每个 (持仓 × 持有期) 单元的前向收益分布 + 赢家贡献集中度。

    与 ``institutional_metrics``(组合层日收益) 互补，回答两个问题：
    1. 篮子有没有真抓到暴涨龙头（name_period_ret_* / pct_ret_gt_*）；
    2. 赢家是否被等权/调仓规则稀释（winners_top*_share —— 越高=右尾被吃住）。

    口径全透明：仅用调仓权重 + 收盘价，不碰引擎内部。

    Parameters
    ----------
    weights_history : dict[Timestamp, pd.Series]
        调仓生效日 -> 目标权重(index=code)。即策略 ``scheduled_weights``。
    close : pd.DataFrame
        date x code 收盘价(与回测同口径)。
    win_start : optional
        只统计 >= win_start 的调仓日（切样本窗口用）。
    """
    dates = sorted(d for d in weights_history if win_start is None or d >= win_start)
    cells = []  # (fwd_ret, contribution = weight * fwd_ret)
    for i, d in enumerate(dates):
        if d not in close.index:
            continue
        nxt = dates[i + 1] if i + 1 < len(dates) else close.index[-1]
        p0 = close.loc[d]
        idx_nxt = close.index[close.index <= nxt]
        if len(idx_nxt) == 0:
            continue
        p1 = close.loc[idx_nxt[-1]]
        for name, wt in weights_history[d].items():
            r0, r1 = p0.get(name), p1.get(name)
            if pd.isna(r0) or pd.isna(r1) or r0 <= 0:
                continue
            fwd = float(r1) / float(r0) - 1.0
            cells.append((fwd, float(wt) * fwd))
    if not cells:
        return {"n_cells": 0}

    rets = np.array([c[0] for c in cells])
    pos = np.sort(np.array([c[1] for c in cells if c[1] > 0]))[::-1]
    gross = float(pos.sum())

    def share(frac: float) -> float:
        if gross <= 0 or len(pos) == 0:
            return 0.0
        k = max(1, int(np.ceil(frac * len(pos))))
        return float(pos[:k].sum() / gross)

    return {
        "n_cells": len(cells),
        "name_period_ret_p95": float(np.quantile(rets, 0.95)),
        "name_period_ret_p99": float(np.quantile(rets, 0.99)),
        "name_period_ret_max": float(rets.max()),
        "pct_ret_gt_30pct": float((rets > 0.30).mean()),
        "pct_ret_gt_50pct": float((rets > 0.50).mean()),
        "pct_ret_gt_100pct": float((rets > 1.00).mean()),
        "winners_top1_share": float(pos[0] / gross) if gross > 0 and len(pos) else 0.0,
        "winners_top5pct_share": share(0.05),
        "winners_top10pct_share": share(0.10),
    }


def metrics(ret, bench=None):
    """Standard performance metrics dict（核心 + 机构级指标合并）。"""
    if len(ret) < 100:
        return {
            "annual": -1.0,
            "vol": 0.0,
            "sharpe": -1.0,
            "maxdd": -1.0,
            "calmar": 0.0,
            "hit": False,
            "n": len(ret),
        }
    annual = annual_return(ret)
    vol = annual_vol(ret)
    sharpe = sharpe_ratio(ret)
    maxdd = max_drawdown(ret)
    calmar = calmar_ratio(ret)
    out = {
        "annual": annual,
        "vol": vol,
        "sharpe": sharpe,
        "maxdd": maxdd,
        "calmar": calmar,
        "hit": compute_hit(annual, maxdd),
        "n": len(ret),
    }
    out.update(institutional_metrics(ret, bench=bench))
    return out


def yearly_returns(ret):
    """Annual returns from a daily return series."""
    return ret.groupby(ret.index.year).apply(lambda x: (1 + x).prod() - 1)
