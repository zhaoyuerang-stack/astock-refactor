"""Marginal contribution evaluator — Portfolio-First 核心 (合并版).

合并历史：
  v1: 旧版 — 全样本 Sharpe delta + corr 三档 (LIVE_P/K/C/SHELVE)
  v2: B 路线 — 加配置 grid (top_n × timing)
  v3: G 合并 — 调用 portfolio.marginal.evaluate (regime-aware + LIVE_D 防御档)

职责分工：
  factory.lines.line3_marginal (本模块):
    - 从 Hypothesis 反向构造 candidate returns (因子查找 + 回测)
    - 跑 config grid (top_n × timing) 创造差异化 returns
    - 每个 cfg 调用 portfolio.marginal.evaluate
    - 选 best grade (LIVE_D > LIVE_C 但都 SHELVE 以上)
    - 归档到 Experiment

  portfolio.marginal.evaluate (复用):
    - 5-regime classification (bull/bear/chop/panic/upside_crisis)
    - regime-weighted score
    - DEFENSIVE 档识别 (bear 相对改善)
    - 评级 + recommendation
"""
import importlib
import time
import uuid
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.small_cap import small_cap_timing

# 不对称性审计
from factory.analysis.asymmetry_audit import asymmetry_report
from factory.ontology import (
    Decision,
    Experiment,
    ExperimentProtocol,
    ExperimentResult,
    Hypothesis,
)

# 合并核心：复用 portfolio.marginal.evaluate
from portfolio.marginal import evaluate as portfolio_marginal_evaluate

# ────────────────────────── 配置 grid ──────────────────────────

@dataclass(frozen=True)
class StrategyConfig:
    """候选的回测配置 — 与因子正交的维度。"""
    top_n: int = 25
    rebalance_freq: str = "20D"
    timing_kind: str = "none"     # "none" | "small_cap_ma16" | "small_cap_ma8"

    def __repr__(self):
        return f"cfg(top={self.top_n},reb={self.rebalance_freq},t={self.timing_kind})"


# B008 修复:frozen dataclass 不可变,模块级单例做缺省,与旧内联缺省语义等价。
_DEFAULT_CONFIG = StrategyConfig()


# 默认 grid — 与现有 LIVE 配置故意拉开差距
DEFAULT_CONFIG_GRID: list[StrategyConfig] = [
    StrategyConfig(top_n=25, rebalance_freq="20D", timing_kind="small_cap_ma16"),
    StrategyConfig(top_n=50, rebalance_freq="20D", timing_kind="small_cap_ma16"),
    StrategyConfig(top_n=100, rebalance_freq="20D", timing_kind="small_cap_ma16"),
    StrategyConfig(top_n=25, rebalance_freq="20D", timing_kind="none"),
    StrategyConfig(top_n=50, rebalance_freq="20D", timing_kind="none"),
    StrategyConfig(top_n=25, rebalance_freq="20D", timing_kind="small_cap_ma8"),
]


# ────────────────────────── Grade 优先级 ──────────────────────────
# 来自 portfolio.marginal._auto_grade 的字符串值
GRADE_PRIORITY = {
    "LIVE_P": 0,   # Pillar
    "LIVE_K": 1,   # Core
    "LIVE_C": 2,   # Complement
    "LIVE_D": 3,   # Defensive (低于 LIVE_C 但仍是有效 LIVE)
    "SHELVE": 4,
}
NON_SHELVE_GRADES = {"LIVE_P", "LIVE_K", "LIVE_C", "LIVE_D"}


# ────────────────────────── 候选 returns 计算 ──────────────────────────

def _resolve_factor_fn(fn_name: str):
    module_path, fn = fn_name.rsplit(".", 1)
    return getattr(importlib.import_module(module_path), fn)


def _dispatch_args(deps, close, volume, amount):
    s = {d for d in deps if not d.startswith("fundamental/")}
    if "price/close" in s and "price/volume" in s:
        return [close, volume]
    if "price/close" in s:
        return [close]
    if "price/amount" in s:
        return [amount]
    if "price/volume" in s:
        return [volume]
    raise ValueError(f"未识别数据依赖: {deps}")


def _build_timing(timing_kind: str, close, amount) -> pd.Series | None:
    if timing_kind == "none":
        return None
    if timing_kind == "small_cap_ma16":
        timing, _, _ = small_cap_timing(close, amount, ma_window=16)
        return timing.astype(float)
    if timing_kind == "small_cap_ma8":
        timing, _, _ = small_cap_timing(close, amount, ma_window=8)
        return timing.astype(float)
    raise ValueError(f"unknown timing_kind: {timing_kind}")


def run_candidate_returns(
    hyp: Hypothesis,
    direction: int,
    close: pd.DataFrame,
    volume: pd.DataFrame,
    amount: pd.DataFrame,
    start: str = "2018-01-01",
    config: StrategyConfig = _DEFAULT_CONFIG,
) -> pd.Series:
    """跑候选 hypothesis 的 daily returns，按 config 决定 top_n/timing/rebal。"""
    fn = _resolve_factor_fn(hyp.factor_fn_name)
    args = _dispatch_args(hyp.data_dependencies, close, volume, amount)
    factor = fn(*args, **hyp.factor_params)

    close_w = close.loc[start:]
    factor_w = factor.reindex(close_w.index)
    amount_w = amount.loc[start:]
    timing = _build_timing(config.timing_kind, close_w, amount_w)

    prices = PricePanel(
        close=close_w,
        volume=volume.loc[start:],
        amount=amount_w,
    )
    signal = Signal(
        factor=factor_w,
        top_n=config.top_n,
        direction=int(direction),
        rebalance_freq=config.rebalance_freq,
        timing=timing,
        family="candidate",
        version=hyp.id[:8],
    )
    # leverage 与 LIVE 对齐（1.25x）；不对齐则 bear 表现不可比
    cfg = BacktestConfig(start=start, cost=CostModel(), leverage=1.25)
    engine = BacktestEngine(prices=prices, config=cfg)
    result = engine.run(signal)
    return result.returns.dropna()


# ────────────────────────── 报告对象 ──────────────────────────

@dataclass(frozen=True)
class MarginalReport:
    """边际贡献评估结果 (合并版)."""
    candidate_name: str
    grade: str                          # "LIVE_P" | "LIVE_K" | "LIVE_C" | "LIVE_D" | "SHELVE"
    recommendation: str

    # 全样本 deltas
    delta_sharpe: float
    delta_annual: float
    delta_maxdd: float
    baseline_sharpe: float
    combined_sharpe: float

    # Regime-weighted
    regime_weighted_score: float

    # Defensive metrics
    bear_improvement: float             # +0.019 = +1.9pp 熊市改善
    bear_annual: float
    defensive_grade: str                # portfolio.marginal.defensive_grade 的 grade

    # Correlation
    avg_corr_to_live: float

    # Config grid
    best_config: StrategyConfig
    grid_summary: tuple                 # ((cfg_str, grade, regime_score, bear_impr), ...)

    # Raw report (for downstream tools)
    raw: dict


# ────────────────────────── 评估 ──────────────────────────

def evaluate_candidate(
    hyp: Hypothesis,
    direction: int,
    live_returns: dict[str, pd.Series],
    close: pd.DataFrame,
    volume: pd.DataFrame,
    amount: pd.DataFrame,
    vintage_id: str,
    start: str = "2018-01-01",
    config_grid: list[StrategyConfig] | None = None,
    market_returns: pd.Series | None = None,
) -> tuple[Experiment, MarginalReport | None]:
    """跑 config grid，每个调 portfolio.marginal.evaluate，选 best grade。

    选 best 规则:
      1. 按 GRADE_PRIORITY 升序（P 最优，SHELVE 最差）
      2. 同 grade 按 regime_weighted_score 降序
    这样 LIVE_D 配置也能浮出来（即使有别的 cfg 跑出 SHELVE）。
    """
    t0 = time.time()
    grid = config_grid or DEFAULT_CONFIG_GRID

    # market_returns 默认用截面 mean
    if market_returns is None:
        market_returns = (
            close.loc[start:]
            .pct_change(fill_method=None)
            .replace([np.inf, -np.inf], np.nan)
            .mean(axis=1)
            .fillna(0.0)
        )

    try:
        grid_results = []
        for cfg in grid:
            try:
                cand_ret = run_candidate_returns(
                    hyp, direction, close, volume, amount,
                    start=start, config=cfg,
                )
                rep = portfolio_marginal_evaluate(
                    candidate_returns=cand_ret,
                    candidate_name=f"{hyp.name}::{cfg}",
                    existing_live=live_returns,
                    market_returns=market_returns,
                )
                grid_results.append((cfg, cand_ret, rep))
            except Exception:
                continue

        if not grid_results:
            raise RuntimeError("all grid configs failed")

        # Pick best: grade priority asc + regime_score desc
        grid_results.sort(key=lambda x: (
            GRADE_PRIORITY.get(x[2]["grade"], 9),
            -float(x[2].get("regime_weighted_score", 0)),
        ))
        best_cfg, best_ret, best_rep = grid_results[0]

        defensive = best_rep.get("defensive", {})
        fs = best_rep.get("full_sample", {})

        report = MarginalReport(
            candidate_name=hyp.name,
            grade=best_rep["grade"],
            recommendation=best_rep.get("recommendation", ""),
            delta_sharpe=float(fs.get("delta_sharpe", 0)),
            delta_annual=float(fs.get("delta_annual", 0)),
            delta_maxdd=float(fs.get("delta_maxdd", 0)),
            baseline_sharpe=float(fs.get("baseline_sharpe", 0)),
            combined_sharpe=float(fs.get("combined_sharpe", 0)),
            regime_weighted_score=float(best_rep.get("regime_weighted_score", 0)),
            bear_improvement=float(defensive.get("improvement", 0)),
            bear_annual=float(defensive.get("bear_annual", 0)),
            defensive_grade=str(defensive.get("grade", "")),
            avg_corr_to_live=float(best_rep.get("correlation", {}).get("avg_corr", 1.0)),
            best_config=best_cfg,
            grid_summary=tuple(
                (str(g[0]), g[2]["grade"],
                 float(g[2].get("regime_weighted_score", 0)),
                 float(g[2].get("defensive", {}).get("improvement", 0)))
                for g in grid_results
            ),
            raw=best_rep,
        )

        decision = (
            Decision.PROMOTE if report.grade in NON_SHELVE_GRADES
            else Decision.SHELVE
        )

        # 不对称性审计
        asym = asymmetry_report(best_ret, market_returns, hyp.name)

        result = ExperimentResult(
            metrics={
                "delta_sharpe": report.delta_sharpe,
                "delta_annual": report.delta_annual,
                "delta_maxdd": report.delta_maxdd,
                "baseline_sharpe": report.baseline_sharpe,
                "combined_sharpe": report.combined_sharpe,
                "regime_weighted_score": report.regime_weighted_score,
                "bear_improvement": report.bear_improvement,
                "bear_annual": report.bear_annual,
                "avg_corr_to_live": report.avg_corr_to_live,
                # 不对称性指标
                "asymmetry_score": asym.asymmetry_score,
                "gain_pain_ratio": asym.gain_pain,
                "up_down_capture": asym.up_down_capture,
                "sortino_ratio": asym.sortino,
                "omega_ratio": asym.omega,
            },
            details={
                "grade": report.grade,
                "recommendation": report.recommendation,
                "defensive_grade": report.defensive_grade,
                "best_config": str(best_cfg),
                "live_strategies": list(live_returns.keys()),
                "grid_size": len(grid_results),
                "grid_summary": [
                    {
                        "cfg": str(g[0]),
                        "grade": g[2]["grade"],
                        "regime_score": float(g[2].get("regime_weighted_score", 0)),
                        "bear_impr": float(g[2].get("defensive", {}).get("improvement", 0)),
                    }
                    for g in grid_results
                ],
                "regime_details": best_rep.get("regime_details", {}),
                "regime_summary": best_rep.get("regime_summary", {}),
            },
        )
        reason = f"{report.grade} | {report.recommendation[:60]}"

    except Exception as e:
        decision = Decision.DISCARD
        result = ExperimentResult(error=f"{type(e).__name__}: {str(e)[:200]}")
        report = None
        reason = f"error: {str(e)[:80]}"

    exp = Experiment(
        experiment_id=uuid.uuid4().hex[:12],
        hypothesis_id=hyp.id,
        protocol=ExperimentProtocol.MARGINAL_EVAL,
        vintage_id=vintage_id,
        result=result,
        decision=decision,
        cost_spent_seconds=time.time() - t0,
        run_at=date.today().isoformat(),
        notes=f"marginal_eval: {reason}" if report else reason,
    )
    return exp, report
