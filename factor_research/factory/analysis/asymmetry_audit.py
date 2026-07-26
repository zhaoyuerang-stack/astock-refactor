"""不对称性审计层 — 对通过 L3 的候选评估回报结构的不对称性.

核心理念: 我们要找的不是"夏普更高"的因子, 而是"正收益端厚度 > 负收益端厚度"的因子.

指标:
  gain_pain    : mean(正日收益) / |mean(负日收益)|  — 赚的时候平均赚多少 vs 亏的时候亏多少
  up_down_cap  : bull_capture / bear_capture       — 牛市跟上 vs 熊市躲掉
  pos_neg_var  : 正收益方差 / 负收益方差            — 好的波动 vs 坏的波动
  skew_daily   : 日收益偏度                         — >0 = 正偏 (好)
  sortino      : 年化收益 / 下行波动率               — Sharpe 的下行版
  omega_ratio  : sum(正收益) / sum(|负收益|)        — 总盈利 / 总亏损

用法:
  from factory.analysis.asymmetry_audit import asymmetry_report
  report = asymmetry_report(daily_returns, market_returns)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class AsymmetryReport:
    """不对称性审计结果."""
    name: str

    # 核心不对称指标
    gain_pain: float           # >1 = 赚的时候比亏的时候多
    up_down_capture: float     # >1 = 牛市比熊市跟得好
    pos_neg_var: float         # >1 = 好的波动比坏的波动大
    skew_daily: float          # >0 = 正偏

    # 下行风险指标
    sortino: float             # Sharpe 的下行版
    omega: float               # 总盈利 / 总亏损

    # 传统指标 (对照)
    sharpe: float
    annual: float
    maxdd: float

    # 分 regime 的不对称比
    regime_gain_pain: dict     # {bull: ..., bear: ..., chop: ...}

    # 综合评价
    asymmetry_score: float     # 0-1, 越高越好
    verdict: str               # "强不对称" / "中等" / "近对称" / "负偏"


def _classify_regime(market_ret: pd.Series) -> pd.Series:
    """简化的 regime 分类: 用 60 日滚动收益."""
    roll = market_ret.rolling(60).mean()
    regimes = pd.Series("chop", index=market_ret.index)
    regimes[roll > 0.003] = "bull"    # 年化 ~75%+
    regimes[roll < -0.003] = "bear"   # 年化 ~-75%-
    return regimes


def asymmetry_report(
    returns: pd.Series,
    market_returns: pd.Series | None = None,
    name: str = "candidate",
    rf: float = 0.025,
) -> AsymmetryReport:
    """计算不对称性审计报告.

    Args:
        returns: 日收益序列
        market_returns: 市场日收益 (用于 regime 分类), None 则只用全样本
        name: 候选名称
        rf: 无风险利率 (用于 Sortino)

    Returns:
        AsymmetryReport
    """
    r = returns.dropna()
    if len(r) < 100:
        return AsymmetryReport(
            name=name, gain_pain=0, up_down_capture=0, pos_neg_var=0,
            skew_daily=0, sortino=0, omega=0, sharpe=0, annual=0, maxdd=0,
            regime_gain_pain={}, asymmetry_score=0, verdict="数据不足",
        )

    # ── gain/pain ──
    pos = r[r > 0]; neg = r[r < 0]
    pos_mean = float(pos.mean()) if len(pos) > 0 else 0.0
    neg_mean = float(abs(neg.mean())) if len(neg) > 0 else 0.001
    gain_pain = pos_mean / neg_mean

    # ── up/down capture ──
    up_down_capture = 1.0
    regime_gp = {}
    if market_returns is not None:
        regimes = _classify_regime(market_returns.reindex(r.index).fillna(0))
        bull_mask = regimes == "bull"; bear_mask = regimes == "bear"
        if bull_mask.sum() > 20 and bear_mask.sum() > 20:
            bull_ann = float(r[bull_mask].mean() * 252)
            bear_ann = float(r[bear_mask].mean() * 252)
            up_down_capture = bull_ann / abs(bear_ann) if abs(bear_ann) > 0.001 else 99

        for regime in ["bull", "bear", "chop"]:
            mask = regimes == regime
            if mask.sum() < 20:
                continue
            rp = r[mask]
            pp = rp[rp > 0]; np_ = rp[rp < 0]
            pm = float(pp.mean()) if len(pp) > 0 else 0.0
            nm = float(abs(np_.mean())) if len(np_) > 0 else 0.001
            regime_gp[regime] = pm / nm

    # ── pos/neg variance ──
    pos_var = float(pos.var()) if len(pos) > 0 else 0.0
    neg_var = float(neg.var()) if len(neg) > 0 else 0.001
    pos_neg_var = pos_var / neg_var

    # ── skew ──
    skew_daily = float(r.skew())

    # ── Sortino ──
    annual = float(r.mean() * 252)
    downside = r[r < 0]
    down_vol = float(downside.std() * np.sqrt(252)) if len(downside) > 0 else float(r.std() * np.sqrt(252))
    sortino = (annual - rf) / down_vol if down_vol > 0 else 0.0

    # ── Omega ──
    omega = pos.sum() / abs(neg.sum()) if abs(neg.sum()) > 0 else 99

    # ── Sharpe & maxdd (对照) ──
    vol = float(r.std() * np.sqrt(252))
    sharpe = (annual - rf) / vol if vol > 0 else 0.0
    cum = (1 + r).cumprod()
    maxdd = float((cum / cum.cummax() - 1).min())

    # ── 综合不对称性评分 ──
    score = _asymmetry_score(gain_pain, up_down_capture, pos_neg_var, skew_daily, sortino, annual)

    # ── 判定 ──
    if score >= 0.7:
        verdict = "强不对称"
    elif score >= 0.5:
        verdict = "中等不对称"
    elif score >= 0.3:
        verdict = "近对称"
    else:
        verdict = "负偏"

    return AsymmetryReport(
        name=name, gain_pain=gain_pain, up_down_capture=up_down_capture,
        pos_neg_var=pos_neg_var, skew_daily=skew_daily,
        sortino=sortino, omega=omega, sharpe=sharpe,
        annual=annual, maxdd=maxdd,
        regime_gain_pain=regime_gp, asymmetry_score=score, verdict=verdict,
    )


def _asymmetry_score(gain_pain, up_down_cap, pos_neg_var, skew, sortino, annual=0.0) -> float:
    """0-1 综合评分, 权重向 gain_pain 和 up_down_cap 倾斜.

    重要: 不对称性只在"正期望收益"的前提下有意义.
    一个年化为负的因子即使不对称性结构好, 也是无用的.
    """
    # 可行性门槛: 年化 ≤0 → score=0; 年化 >0 才有资格
    if annual <= 0:
        return 0.0

    score = 0.0
    # gain_pain: 1.0→0, 1.3→0.6, 1.5→1.0
    score += min(max((gain_pain - 1.0) / 0.5, 0), 1) * 0.30
    # up_down_cap: 1.0→0, 3.0→1.0
    score += min(max((up_down_cap - 1.0) / 2.0, 0), 1) * 0.25
    # pos_neg_var: 0.8→0, 1.5→1.0
    score += min(max((pos_neg_var - 0.8) / 0.7, 0), 1) * 0.10
    # skew: -0.5→0, 0→0.5, 1→1.0
    score += min(max((skew + 0.5) / 1.5, 0), 1) * 0.10
    # sortino: 0→0, 2→1.0
    score += min(max(sortino / 2.0, 0), 1) * 0.15
    # 绝对收益 bonus: 年化>5% → 0.10, >15% → 0.10
    score += min(max(annual / 0.30, 0), 1) * 0.10
    return float(score)


def compare_candidates(
    candidates: dict[str, pd.Series],
    market_returns: pd.Series | None = None,
) -> pd.DataFrame:
    """批量比较候选的不对称性, 返回排序好的 DataFrame."""
    reports = []
    for name, ret in candidates.items():
        rep = asymmetry_report(ret, market_returns, name)
        reports.append({
            "name": name,
            "gain_pain": rep.gain_pain,
            "up_down_cap": rep.up_down_capture,
            "pos_neg_var": rep.pos_neg_var,
            "skew": rep.skew_daily,
            "sortino": rep.sortino,
            "omega": rep.omega,
            "sharpe": rep.sharpe,
            "annual": rep.annual,
            "maxdd": rep.maxdd,
            "asym_score": rep.asymmetry_score,
            "verdict": rep.verdict,
        })
    df = pd.DataFrame(reports)
    return df.sort_values("asym_score", ascending=False)
