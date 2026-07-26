"""Strategy lifecycle: status enum, valid transitions, gate conditions."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class Lifecycle(Enum):
    """策略生命周期状态."""
    INCUBATING = "incubating"   # 研发中，仅研究脚本可见
    CANDIDATE = "candidate"     # 通过 IC + 简单回测，待 Walk-Forward + 审计
    LIVE = "live"               # Walk-Forward 通过 + 审计 ≥ 10 PASS，生产中
    RETIRED = "retired"         # 连续失效或 IC 归零，退役保留记录


# ── 合法状态转换（只管理"晋升"路径，退役由运行时监控函数处理）──
TRANSITIONS: dict[Lifecycle, list[Lifecycle]] = {
    Lifecycle.INCUBATING: [Lifecycle.CANDIDATE],
    Lifecycle.CANDIDATE:  [Lifecycle.LIVE, Lifecycle.RETIRED],
    Lifecycle.LIVE:       [Lifecycle.RETIRED],
    Lifecycle.RETIRED:    [],  # 不可逆
}


# ── 晋升门槛 ──
@dataclass
class MonitorMetrics:
    """运行时监控指标，供 GATES 和 should_retire 使用."""
    ic_ir: float = 0.0
    simple_backtest_ok: bool = False
    wf_passed: bool = False
    audit_pass: int = 0
    months_underperforming: int = 0
    ic_decayed: bool = False


GATES: dict[tuple[Lifecycle, Lifecycle], Callable[[MonitorMetrics], bool]] = {
    # incubating → candidate: IC IR > 0.2 且 简单回测年化 > 15%
    (Lifecycle.INCUBATING, Lifecycle.CANDIDATE):
        lambda m: m.ic_ir >= 0.2 and m.simple_backtest_ok,
    # candidate → live: Walk-Forward 通过 且 17 关审计 ≥ 10 PASS
    (Lifecycle.CANDIDATE, Lifecycle.LIVE):
        lambda m: m.wf_passed and m.audit_pass >= 10,
}


# ── 退役检查（独立于 GATES，运行时调用）──
def should_retire(monitor_metrics: MonitorMetrics) -> bool | str:
    """退役检查。返回 False 表示不需要退役，或返回退役原因 string."""
    if monitor_metrics.months_underperforming >= 6:
        return f"连续 {monitor_metrics.months_underperforming} 月跑输基准"
    if monitor_metrics.ic_decayed:
        return "因子 IC 归零或反转"
    return False

