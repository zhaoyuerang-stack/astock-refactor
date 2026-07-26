"""L2 Multi-Regime — 把 candidate returns 切 4 个 regime 看分布。

输入: Hypothesis (L1_PASSED) + direction + 数据
输出: Experiment (含 per-regime metrics)

判定: 至少 N 个 regime 单独年化 ≥ 0 (GATES_L2)
洞见: 自动识别 "REGIME_DEPENDENT" insight 候选 (只在某 regime 工作)
"""
import importlib
import time
import uuid
from datetime import date

import numpy as np
import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factory.analysis import REGIME_LABELS, classify_regime
from factory.ontology import (
    Decision,
    Experiment,
    ExperimentProtocol,
    ExperimentResult,
    Hypothesis,
    check_f1_economic_thesis,
    check_f2_cheap_first,
)

from .gates import GATES_L2


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
    raise ValueError(f"L2 无法识别数据依赖: {deps}")


def _regime_metrics(ret: pd.Series) -> dict:
    if len(ret) < 20:
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


def run_l2(
    hyp: Hypothesis,
    close: pd.DataFrame,
    volume: pd.DataFrame,
    amount: pd.DataFrame,
    direction: int,
    vintage_id: str,
    start: str = "2018-01-01",
    top_n: int = 25,
    rebalance_freq: str = "20D",
    factor: pd.DataFrame | None = None,
) -> Experiment:
    """L2 multi-regime: 跑同 L1 的 backtest，按 regime 切片报告。"""
    check_f1_economic_thesis(hyp)
    check_f2_cheap_first(hyp.status, ExperimentProtocol.L2_MULTI_REGIME)

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
            family="l2_multi_regime",
            version=hyp.id[:8],
        )
        cfg = BacktestConfig(start=start, cost=CostModel(), leverage=1.0)
        engine = BacktestEngine(prices=prices, config=cfg)
        result = engine.run(signal)
        returns = result.returns.dropna()

        # Regime split
        regime = classify_regime(close_w).reindex(returns.index)
        per_regime = {}
        for r in REGIME_LABELS:
            mask = regime == r
            if mask.sum() < 20:
                per_regime[r] = {"n": int(mask.sum()), "annual": 0.0, "sharpe": 0.0, "maxdd": 0.0, "vol": 0.0}
                continue
            per_regime[r] = _regime_metrics(returns[mask])

        # Global metrics
        global_m = _regime_metrics(returns)

        # Decision
        n_pos_regimes = sum(
            1 for r in REGIME_LABELS
            if per_regime[r].get("annual", 0) >= GATES_L2["regime_annual_min"]
            and per_regime[r].get("n", 0) >= 20
        )

        # 不强制升级 status；这里仅用于诊断 + insight 候选
        # F-2 已检查 L1_PASSED；我们记录 decision 但 status 推进由 caller 决定
        if n_pos_regimes >= GATES_L2["regime_pass_min"]:
            decision = Decision.PROMOTE
            reason = f"{n_pos_regimes}/{len(REGIME_LABELS)} regimes pos"
        else:
            decision = Decision.SHELVE
            reason = f"only {n_pos_regimes}/{len(REGIME_LABELS)} regimes pos"

        # Detect regime_dependent insight pattern
        regime_dep = None
        positive_regimes = [r for r in REGIME_LABELS
                             if per_regime[r].get("annual", 0) > 0
                             and per_regime[r].get("n", 0) >= 60]
        negative_regimes = [r for r in REGIME_LABELS
                             if per_regime[r].get("annual", 0) < -0.05
                             and per_regime[r].get("n", 0) >= 60]
        if len(positive_regimes) == 1 and negative_regimes:
            regime_dep = positive_regimes[0]

        result_obj = ExperimentResult(
            metrics={
                "global_annual": global_m["annual"],
                "global_sharpe": global_m["sharpe"],
                "global_maxdd": global_m["maxdd"],
                "n_pos_regimes": float(n_pos_regimes),
            },
            details={
                "per_regime": per_regime,
                "decision_reason": reason,
                "regime_dependent_on": regime_dep,
                "direction": int(direction),
            },
        )

    except Exception as e:
        decision = Decision.DISCARD
        result_obj = ExperimentResult(error=f"{type(e).__name__}: {str(e)[:200]}")
        reason = f"error: {str(e)[:80]}"

    return Experiment(
        experiment_id=uuid.uuid4().hex[:12],
        hypothesis_id=hyp.id,
        protocol=ExperimentProtocol.L2_MULTI_REGIME,
        vintage_id=vintage_id,
        result=result_obj,
        decision=decision,
        cost_spent_seconds=time.time() - t0,
        run_at=date.today().isoformat(),
        notes=reason,
    )
