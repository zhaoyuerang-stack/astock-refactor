"""L0 IC scan — 秒级 IC IR 粗筛。

策略：
  factor_fn 按 data_dependencies 动态调用
  → factor 截面对齐 forward_return
  → calc_ic + ic_summary
  → ICIR 与 GATES_L0 比对
  → Decision: PROMOTE / DISCARD

不依赖 BacktestEngine，纯因子诊断。
"""
import importlib
import time
import uuid
from datetime import date

import pandas as pd

from engine.factor_analysis import calc_ic, ic_summary, newey_west_icir
from factory.ontology import (
    Decision,
    Experiment,
    ExperimentProtocol,
    ExperimentResult,
    Hypothesis,
    check_f1_economic_thesis,
    check_f2_cheap_first,
)

from .gates import GATES_L0


def _resolve_factor_fn(fn_name: str):
    """'factors.small_cap.small_cap_factor' -> callable."""
    module_path, fn = fn_name.rsplit(".", 1)
    mod = importlib.import_module(module_path)
    return getattr(mod, fn)


def _dispatch_args(deps: tuple[str, ...], close, volume, amount) -> list[pd.DataFrame]:
    """根据 data_dependencies 选择喂给 factor_fn 的位置参数。
    fundamental/* 由 factor 内部 lru_cache 加载,只需喂 close 拿日历。"""
    deps_set = {d for d in deps if not d.startswith("fundamental/")}
    if "price/close" in deps_set and "price/volume" in deps_set:
        return [close, volume]
    if "price/close" in deps_set:
        return [close]
    if "price/amount" in deps_set:
        return [amount]
    if "price/volume" in deps_set:
        return [volume]
    raise ValueError(f"L0 无法识别数据依赖: {deps}")


def precompute_forward_returns(close: pd.DataFrame, horizon: int = 20) -> pd.DataFrame:
    """T 日因子 vs T+1..T+horizon 收益率（shift -horizon 对齐）。"""
    return close.pct_change(horizon).shift(-horizon)


def run_l0(
    hyp: Hypothesis,
    close: pd.DataFrame,
    volume: pd.DataFrame,
    amount: pd.DataFrame,
    forward_ret: pd.DataFrame,
    vintage_id: str,
    sample_dates: int | None = None,
    horizon: int = 20,
    factor: pd.DataFrame | None = None,
) -> Experiment:
    """运行单 Hypothesis 的 L0 IC scan。

    sample_dates: 若给定，从 forward_ret 等距抽样 N 个日期跑（更快但精度下降）。
    """
    check_f1_economic_thesis(hyp)
    check_f2_cheap_first(hyp.status, ExperimentProtocol.L0_IC_SCAN)

    t0 = time.time()

    try:
        if factor is None:
            fn = _resolve_factor_fn(hyp.factor_fn_name)
            args = _dispatch_args(hyp.data_dependencies, close, volume, amount)
            factor = fn(*args, **hyp.factor_params)

        if sample_dates and sample_dates < len(forward_ret):
            step = len(forward_ret) // sample_dates
            sampled = forward_ret.iloc[::step]
        else:
            sampled = forward_ret

        ic = calc_ic(factor, sampled, method="rank").dropna()
        summary = ic_summary(ic)

        ic_ir = summary.get("ICIR", float("nan"))
        ic_count = summary.get("count", 0)

        # NW 重叠校正口径(horizon>1 的 raw ICIR 因重叠虚高,h=20 实测 ~3.5x)。
        # 闸门仍用 raw ic_ir:同 horizon 下相对排序有效 + 阈值按 raw 标定(内部自洽);
        # nw_icir 仅落账供诚实绝对量级解读,不改判决口径(避免破坏既有阈值校准)。
        # max_lag 按 IC 序列自相关长度设——全量评估=horizon;采样时按采样步长折算。
        nw_lag = horizon
        if sample_dates and sample_dates < len(forward_ret):
            step = max(1, len(forward_ret) // sample_dates)
            nw_lag = max(1, round(horizon / step))
        nw_icir = newey_west_icir(ic.values, max_lag=nw_lag) if len(ic) > 1 else float("nan")

        # 决策
        if pd.isna(ic_ir) or ic_count < GATES_L0["min_ic_count"]:
            decision = Decision.DISCARD
            reason = f"insufficient IC samples ({ic_count})"
        elif abs(ic_ir) < GATES_L0["ic_ir_min"]:
            decision = Decision.DISCARD
            reason = f"|ICIR|={abs(ic_ir):.3f} < {GATES_L0['ic_ir_min']}"
        else:
            decision = Decision.PROMOTE
            reason = f"ICIR={ic_ir:+.3f} pass"

        metrics = {
            k: (float(v) if not pd.isna(v) else 0.0)
            for k, v in summary.items()
        }
        metrics["ICIR_nw"] = float(nw_icir) if not pd.isna(nw_icir) else 0.0
        result = ExperimentResult(
            metrics=metrics,
            details={
                "ic_ir": float(ic_ir) if not pd.isna(ic_ir) else None,
                "ic_ir_nw": float(nw_icir) if not pd.isna(nw_icir) else None,
                "icir_口径": "ICIR=raw(闸门口径) / ICIR_nw=NW 重叠校正(诚实绝对量级)",
                "ic_mean": float(summary.get("IC_mean", 0.0)),
                "ic_count": int(ic_count),
                "decision_reason": reason,
                "direction": "long" if ic_ir > 0 else "short",
            },
        )

    except Exception as e:
        decision = Decision.DISCARD
        result = ExperimentResult(error=f"{type(e).__name__}: {str(e)[:200]}")
        reason = f"error: {str(e)[:80]}"

    return Experiment(
        experiment_id=uuid.uuid4().hex[:12],
        hypothesis_id=hyp.id,
        protocol=ExperimentProtocol.L0_IC_SCAN,
        vintage_id=vintage_id,
        result=result,
        decision=decision,
        cost_spent_seconds=time.time() - t0,
        run_at=date.today().isoformat(),
        notes=reason,
    )
