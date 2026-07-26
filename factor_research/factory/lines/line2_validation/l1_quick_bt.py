"""L1 Quick Backtest — 30 秒级简化回测。

L0 通过 → L1 包装 BacktestEngine 跑短窗口、标准成本、无杠杆。
判定：年化 / 回撤是否过 GATES_L1。

direction 由 caller 从 L0 experiment 的 details['direction'] 推断后传入。
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

from .gates import GATES_L1


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
    raise ValueError(f"L1 无法识别数据依赖: {deps}")


def run_l1(
    hyp: Hypothesis,
    close: pd.DataFrame,
    volume: pd.DataFrame,
    amount: pd.DataFrame,
    direction: int,
    vintage_id: str,
    start: str = "2020-01-01",
    top_n: int = 25,
    rebalance_freq: str = "20D",
    factor: pd.DataFrame | None = None,
) -> Experiment:
    """L1 quick backtest.

    direction: +1 → long top, -1 → long bottom (来自 L0 ICIR 符号)。
    start: 短窗口（默认 2020 至今），求快不求全。
    """
    check_f1_economic_thesis(hyp)
    check_f2_cheap_first(hyp.status, ExperimentProtocol.L1_QUICK_BT)

    t0 = time.time()

    try:
        if factor is None:
            fn = _resolve_factor_fn(hyp.factor_fn_name)
            args = _dispatch_args(hyp.data_dependencies, close, volume, amount)
            factor = fn(*args, **hyp.factor_params)

        # 窗口裁剪
        close_w = close.loc[start:]
        volume_w = volume.loc[start:]
        amount_w = amount.loc[start:]
        factor_w = factor.reindex(close_w.index)

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
        smooth_w = exec_params.get("smoothing_window")
        if smooth_w:
            factor_w = factor_w.rolling(int(smooth_w), min_periods=1).mean()

        prices = PricePanel(close=close_w, volume=volume_w, amount=amount_w)
        signal = Signal(
            factor=factor_w,
            top_n=actual_top_n,
            direction=int(direction),
            rebalance_freq=actual_rebalance,
            timing=timing_series,
            family="l1_quick_bt",
            version=hyp.id[:8],
        )
        cfg = BacktestConfig(start=start, cost=CostModel(), leverage=1.0)
        engine = BacktestEngine(prices=prices, config=cfg)
        result = engine.run(signal)

        returns = result.returns.dropna()
        if len(returns) < GATES_L1["min_days"]:
            decision = Decision.DISCARD
            reason = f"insufficient days ({len(returns)})"
            metrics = {"n": len(returns)}
        else:
            annual = float(returns.mean() * 252)
            vol = float(returns.std() * np.sqrt(252))
            sharpe = annual / vol if vol > 0 else 0.0
            cum = (1 + returns).cumprod()
            maxdd = float((cum / cum.cummax() - 1).min())
            calmar = annual / abs(maxdd) if maxdd < 0 else 0.0

            metrics = {
                "annual": annual,
                "vol": vol,
                "sharpe": sharpe,
                "maxdd": maxdd,
                "calmar": calmar,
                "n": len(returns),
            }

            if annual < GATES_L1["annual_min"]:
                decision = Decision.DISCARD
                reason = f"annual={annual:.1%} < {GATES_L1['annual_min']:.1%}"
            elif maxdd < GATES_L1["maxdd_max"]:
                decision = Decision.DISCARD
                reason = f"maxdd={maxdd:.1%} > {GATES_L1['maxdd_max']:.1%}"
            else:
                decision = Decision.PROMOTE
                reason = f"ann={annual:.1%} sharpe={sharpe:.2f}"

        result_obj = ExperimentResult(
            metrics=metrics,
            details={
                "direction": int(direction),
                "start": start,
                "top_n": top_n,
                "rebalance_freq": rebalance_freq,
                "decision_reason": reason,
            },
        )

    except Exception as e:
        decision = Decision.DISCARD
        result_obj = ExperimentResult(error=f"{type(e).__name__}: {str(e)[:200]}")
        reason = f"error: {str(e)[:80]}"

    return Experiment(
        experiment_id=uuid.uuid4().hex[:12],
        hypothesis_id=hyp.id,
        protocol=ExperimentProtocol.L1_QUICK_BT,
        vintage_id=vintage_id,
        result=result_obj,
        decision=decision,
        cost_spent_seconds=time.time() - t0,
        run_at=date.today().isoformat(),
        notes=reason,
    )
