"""Insight — 实验产生的可复用知识。

F-7 强制：所有 Experiment（含 DISCARDED）必产 ≥1 Insight。
失败的实验也是知识——避免未来重复试错。
"""
from dataclasses import dataclass
from enum import Enum


class InsightKind(Enum):
    REGIME_DEPENDENT = "regime_dependent"   # 只在某 regime 工作
    THRESHOLD = "threshold"                 # 参数有阈值
    DEAD_END = "dead_end"                   # 这类思路不通
    INTERACTION = "interaction"             # 因子两两交互效应
    COST_BINDING = "cost_binding"           # 实盘成本敏感
    DATA_GAP = "data_gap"                   # 缺数据
    REGIME_BLIND_SPOT = "regime_blind_spot" # 某 regime 普遍弱


@dataclass(frozen=True)
class Insight:
    """可复用洞见。"""

    insight_id: str
    statement: str                                  # 一句话陈述
    kind: InsightKind
    confidence: float = 0.5                         # 0-1
    evidence_experiment_ids: tuple[str, ...] = ()
    related_hypothesis_ids: tuple[str, ...] = ()
    superseded_by: str | None = None             # 后来 insight 推翻了它
    created_at: str = ""
    distilled_by: str = "auto"                      # auto | llm | human

    def __repr__(self):
        return f"Insight({self.kind.value}: {self.statement[:60]}...)"
