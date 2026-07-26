"""Auto Factor Research Engine data models.

This module keeps the autonomous research surface narrow: agents submit JSON
AST candidates, and the fixed engine decides what can move forward.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any


class CandidateStatus(Enum):
    GENERATED = "generated"
    L0_PASSED = "l0_passed"
    L1_PASSED = "l1_passed"
    L2_PASSED = "l2_passed"
    L3_PASSED = "l3_passed"
    SHELVED = "shelved"
    DISCARDED = "discarded"
    PROMOTED_TO_REVIEW = "promoted_to_review"
    APPROVED = "approved"
    PROMOTING = "promoting"
    PROMOTED_SHADOW = "promoted_shadow"
    PROMOTE_FAILED = "promote_failed"
    REJECTED_BY_HUMAN = "rejected_by_human"
    RETIRED = "retired"


class CandidateDecision(Enum):
    KEEP = "keep"
    DISCARD = "discard"
    SHELVE = "shelve"
    PROMOTE = "promote"


@dataclass(frozen=True)
class Candidate:
    fingerprint: str
    ast: dict[str, Any]
    status: CandidateStatus = CandidateStatus.GENERATED
    source: str = "agent"
    created_at: str = ""
    notes: str = ""
    # 种子来源溯源(ADR-022):记该候选的种子起源,用于审视「搜索空间是否含金库语义」。
    #   确定性种子: {"origin": "deterministic_seed", "catalog": ...}
    #   LLM 种子:   {"origin": "llm_seed", "theme": ..., "model": ..., "generated_at": ...}
    #   变异/交叉子代: {"origin": "derived", "ancestors": [<父代 origin>...], ...}
    # LLM 起源不可机械证否(其先验可能含 2025+ 行情认知)→ 晋级时进 evidence,供人工额外审视。
    provenance: dict[str, Any] = field(default_factory=dict)

    def with_status(self, status: CandidateStatus, notes: str = "") -> Candidate:
        return replace(self, status=status, notes=notes or self.notes)


@dataclass(frozen=True)
class ComplexityReport:
    score: float
    max_auto_stage: str
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LeakageReport:
    passed: bool
    checks: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RedundancyReport:
    score: float
    components: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateEvaluationResult:
    fingerprint: str
    status: CandidateStatus
    decision: CandidateDecision
    metrics: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
