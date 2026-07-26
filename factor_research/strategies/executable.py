"""Spec 驱动的 canonical 策略构建器(Task 6)。

同一个 ExecutableStrategySpec 必须生成同一 factor / timing / weights / Signal。
研究组合编排(strategy_runners)与生产日信号(run_daily)共用此入口,杜绝公式复制。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core.engine import PricePanel, Signal
from core.strategy_spec import ExecutableStrategySpec
from research_toolkit import apply_veto_filter
from strategies.catalog import (
    resolve_factor_builder,
    resolve_policy_builder,
    resolve_timing_builder,
)
from strategies.small_cap import build_rebalance_weights


def select_holdings(factor_row: pd.Series, veto_row: pd.Series | None, top_n: int, veto_q: float) -> list[str]:
    """生产日信号的当日选股——与回测调仓同源(apply_veto_filter 语义)。

    run_daily 此前手写 veto 过滤 + nlargest(公式复制,2026-07-11 review);
    本函数是唯一入口。语义与 build_rebalance_weights 对齐:
      · veto 缺失或 veto_q<=0 → 退化 nlargest;
      · veto 过滤后存活 < top_n → 返回空(凑不满不出仓,与回测一致)。
    """
    f = pd.Series(factor_row).dropna()
    veto = pd.Series(veto_row).dropna() if veto_row is not None else pd.Series(dtype=float)
    if veto.empty or veto_q <= 0:
        return f.nlargest(top_n).index.tolist()
    weights = apply_veto_filter(f, veto, top_n=top_n, veto_q=veto_q)
    return weights.index.tolist()


@dataclass(frozen=True)
class ExecutableStrategy:
    factor: pd.DataFrame
    timing: pd.Series
    scheduled_weights: dict
    signal: Signal
    spec_hash: str
    diagnostics: dict


def build_executable_strategy(spec: ExecutableStrategySpec, prices: PricePanel) -> ExecutableStrategy:
    """从 spec + 价格面板构建可执行策略(因子→择时→政策过滤→调仓权重→Signal)。"""
    spec.validate()

    factor_builder = resolve_factor_builder(spec.factor["type"])
    timing_builder = resolve_timing_builder(spec.timing["type"])
    policy_builder = resolve_policy_builder(spec.policy.get("veto", "none"))

    factor = factor_builder(prices, spec.factor)
    timing, timing_diag = timing_builder(prices, spec.timing)
    veto_factor, veto_q = policy_builder(prices, spec.policy)

    weights = build_rebalance_weights(
        factor,
        prices.close,
        top_n=int(spec.selection["top_n"]),
        rebalance_days=int(spec.selection["rebalance_days"]),
        veto_factor=veto_factor,
        veto_q=veto_q,
    )

    signal = Signal(
        decision_weights=weights,
        timing=timing,
        family=spec.family,
        version=spec.version,
        exposure_cap=float(spec.timing.get("cap", 1.0)),
        execution_timing=spec.execution["fill"],
    )

    return ExecutableStrategy(
        factor=factor,
        timing=timing,
        scheduled_weights=weights,
        signal=signal,
        spec_hash=spec.spec_hash,
        diagnostics={
            "timing": timing_diag,
            "veto_factor": veto_factor,
            "veto_q": veto_q,
        },
    )
