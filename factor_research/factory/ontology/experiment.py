"""Experiment — Hypothesis 的一次测试记录。

append-only：归档后不可改。同 fingerprint 重跑 → 写新 experiment_id 但 returns_hash 必须一致。
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExperimentProtocol(Enum):
    """Cheap-First 流水线的 4 关 + Line 3 边际贡献。F-2 强制 L0-L3 顺序；
    MARGINAL_EVAL 不在 F-2 强制顺序内，可在 L1_PASSED 之后任意触发。"""
    L0_IC_SCAN = "l0_ic_scan"          # 5s
    L1_QUICK_BT = "l1_quick_bt"        # 30s
    L2_MULTI_REGIME = "l2_multi_regime"  # 5min
    L3_WALK_FORWARD = "l3_walk_forward"  # 30min
    MARGINAL_EVAL = "marginal_eval"      # Line 3: 边际贡献评估


class Decision(Enum):
    """Experiment 完成后的决策。"""
    PROMOTE = "promote"      # 升 status 到下一关 (Hypothesis status 推进)
    DISCARD = "discard"
    SHELVE = "shelve"
    REFINE = "refine"


@dataclass(frozen=True)
class ExperimentResult:
    """L0/L1/L2/L3 共享的结果数据。"""
    metrics: dict[str, float] = field(default_factory=dict)  # ic_ir / annual / sharpe / maxdd
    details: dict[str, Any] = field(default_factory=dict)    # 自由扩展
    error: str | None = None


@dataclass(frozen=True)
class Experiment:
    """一次实验。"""

    experiment_id: str                 # UUID 或时间戳 hash
    hypothesis_id: str
    protocol: ExperimentProtocol
    vintage_id: str                    # 数据快照标识（先用 data_lake 最新时间戳）

    result: ExperimentResult
    decision: Decision

    cost_spent_seconds: float = 0.0
    run_at: str = ""
    notes: str = ""

    def __repr__(self):
        return (f"Experiment({self.protocol.value}, hyp={self.hypothesis_id[:8]}, "
                f"decision={self.decision.value})")
