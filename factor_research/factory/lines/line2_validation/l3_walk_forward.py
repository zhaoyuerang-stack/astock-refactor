"""L3 Walk-Forward — 按年切 OOS 测稳定性。

对 factory mutation 的 hypothesis（参数固定的因子定义），"walk-forward"
退化为"年度 OOS 样本稳定性测试"：跑全期回测后按 calendar year 切片。

判定 (GATES_L3):
  - positive_year_ratio >= 0.50 (≥ 一半年份 sharpe > 0)
  - avg_sharpe >= 0.5

通过的候选才真正是"经年稳定"的，单年高 sharpe 不算。
"""
import importlib
import time
import uuid
from datetime import date

import numpy as np
import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factory.ontology import (
    Decision,
    Experiment,
    ExperimentProtocol,
    ExperimentResult,
    Hypothesis,
    check_f1_economic_thesis,
    check_f2_cheap_first,
)

from .gates import GATES_L3


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
    raise ValueError(f"L3 无法识别数据依赖: {deps}")


def _year_metrics(ret: pd.Series) -> dict:
    if len(ret) < 50:
        return {"n": len(ret), "annual": 0.0, "sharpe": 0.0, "maxdd": 0.0}
    annual = float(ret.mean() * 252)
    vol = float(ret.std() * np.sqrt(252))
    sharpe = annual / vol if vol > 0 else 0.0
    cum = (1 + ret).cumprod()
    maxdd = float((cum / cum.cummax() - 1).min())
    return {
        "n": len(ret),
        "annual": annual,
        "sharpe": sharpe,
        "maxdd": maxdd,
        "vol": vol,
    }


def run_l3(
    hyp: Hypothesis,
    close: pd.DataFrame,
    volume: pd.DataFrame,
    amount: pd.DataFrame,
    direction: int,
    vintage_id: str,
    start: str = "2010-01-01",
    top_n: int = 25,
    rebalance_freq: str = "20D",
    factor: pd.DataFrame | None = None,
) -> Experiment:
    """L3 walk-forward = 按年切片稳定性测试。

    跑全期回测一次，按 calendar year 切 → 每年独立算 metrics。
    """
    check_f1_economic_thesis(hyp)
    check_f2_cheap_first(hyp.status, ExperimentProtocol.L3_WALK_FORWARD)

    t0 = time.time()

    try:
        if factor is None:
            fn = _resolve_factor_fn(hyp.factor_fn_name)
            args = _dispatch_args(hyp.data_dependencies, close, volume, amount)
            factor = fn(*args, **hyp.factor_params)

        close_w = close.loc[start:]
        amount_w = amount.loc[start:]

        # Check if the candidate has timing_gate or execution specified in its AST
        timing_series = None
        ast = hyp.factor_params.get("ast", {}) if isinstance(hyp.factor_params, dict) else {}

        # 1. Parse Timing Gate Overlay
        timing_gate = ast.get("timing_gate")
        if timing_gate:
            ma_window = timing_gate.get("ma_window", 16)
            volume_rank_threshold = timing_gate.get("volume_rank_threshold", 0.5)
            from core.overlays.moving_average_overlay import MovingAverageOverlay
            overlay = MovingAverageOverlay(
                ma_window=ma_window,
                volume_rank_threshold=volume_rank_threshold
            )
            timing_series = overlay.exposure_series(close_w, amount_w)

        # 2. Parse Evolved Execution parameters
        exec_params = ast.get("execution", {})
        actual_top_n = int(exec_params.get("portfolio_size", top_n))
        actual_rebalance = str(exec_params.get("rebalance_freq", rebalance_freq))

        # Apply factor smoothing window if defined
        factor_w = factor.reindex(close_w.index)
        smooth_w = exec_params.get("smoothing_window")
        if smooth_w:
            factor_w = factor_w.rolling(int(smooth_w), min_periods=1).mean()

        prices = PricePanel(
            close=close_w,
            volume=volume.loc[start:],
            amount=amount_w,
        )
        signal = Signal(
            factor=factor_w,
            top_n=actual_top_n,
            direction=int(direction),
            rebalance_freq=actual_rebalance,
            timing=timing_series,
            family="l3_walk_forward",
            version=hyp.id[:8],
        )
        # 用 LIVE 同 leverage
        cfg = BacktestConfig(start=start, cost=CostModel(), leverage=1.25)
        engine = BacktestEngine(prices=prices, config=cfg)
        result = engine.run(signal)
        returns = result.returns.dropna()

        # Year-by-year split
        per_year = {}
        for year, group in returns.groupby(returns.index.year):
            per_year[int(year)] = _year_metrics(group)

        # Filter out warmup years (< 50 days)
        eligible_years = {y: m for y, m in per_year.items() if m["n"] >= 50}
        if not eligible_years:
            raise RuntimeError("no eligible years")

        sharpes = [m["sharpe"] for m in eligible_years.values()]
        annuals = [m["annual"] for m in eligible_years.values()]
        positive_years = sum(1 for s in sharpes if s > 0)
        positive_ratio = positive_years / len(sharpes)
        avg_sharpe = float(np.mean(sharpes))
        avg_annual = float(np.mean(annuals))
        std_sharpe = float(np.std(sharpes))

        # Decision
        if (positive_ratio >= GATES_L3["wf_positive_ratio_min"]
                and avg_sharpe >= GATES_L3["wf_avg_sharpe_min"]):
            decision = Decision.PROMOTE
            reason = (f"{positive_years}/{len(sharpes)} years pos, "
                      f"avg_sharpe={avg_sharpe:.2f}")
        else:
            decision = Decision.SHELVE
            reason = (f"{positive_years}/{len(sharpes)} years pos, "
                      f"avg_sharpe={avg_sharpe:.2f} (gates not met)")

        result_obj = ExperimentResult(
            metrics={
                "positive_year_ratio": positive_ratio,
                "avg_sharpe": avg_sharpe,
                "avg_annual": avg_annual,
                "std_sharpe": std_sharpe,
                "n_years": float(len(sharpes)),
                "positive_years": float(positive_years),
            },
            details={
                "per_year": per_year,
                "decision_reason": reason,
                "direction": int(direction),
                "worst_year": min(eligible_years.items(),
                                  key=lambda kv: kv[1]["sharpe"])[0],
                "best_year": max(eligible_years.items(),
                                  key=lambda kv: kv[1]["sharpe"])[0],
            },
        )

    except Exception as e:
        decision = Decision.DISCARD
        result_obj = ExperimentResult(error=f"{type(e).__name__}: {str(e)[:200]}")
        reason = f"error: {str(e)[:80]}"

    return Experiment(
        experiment_id=uuid.uuid4().hex[:12],
        hypothesis_id=hyp.id,
        protocol=ExperimentProtocol.L3_WALK_FORWARD,
        vintage_id=vintage_id,
        result=result_obj,
        decision=decision,
        cost_spent_seconds=time.time() - t0,
        run_at=date.today().isoformat(),
        notes=reason,
    )
