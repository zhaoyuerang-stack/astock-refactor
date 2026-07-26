"""原子准入事务(Task 8)。

候选只有在**完整证据**通过后才能一次性成为「在册」;任一缺失/失败 → 目标状态降为
「候选」,不产生短暂假 LIVE。evaluate_admission 是确定性纯函数(防自欺铁律:判断归代码),
把 Nine-Gate 裁决、holdout 单次校验、边际贡献、可复现证据一次性合议。

与 core.analysis.nine_gate_policy 协同:Nine-Gate 是否通过由 decide_nine_gate 裁决,
本模块不重复实现门逻辑,只做准入合议。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.strategy_spec import ExecutableStrategySpec


@dataclass(frozen=True)
class AdmissionEvidence:
    phase1: dict[str, Any] = field(default_factory=dict)
    phase2: dict[str, Any] = field(default_factory=dict)
    phase3: dict[str, Any] = field(default_factory=dict)
    nine_gate: dict[str, Any] = field(default_factory=dict)
    holdout: dict[str, Any] = field(default_factory=dict)
    marginal: dict[str, Any] = field(default_factory=dict)
    experiment_ids: tuple[str, ...] = ()
    data_fingerprint: str = ""
    spec_hash: str = ""


@dataclass(frozen=True)
class AdmissionDecision:
    approved: bool
    target_status: str          # 「在册」或「候选」
    blocking_reasons: tuple[str, ...]


def evaluate_admission(
    spec: ExecutableStrategySpec,
    evidence: AdmissionEvidence,
    *,
    admission_track: str,
    require_marginal: bool,
) -> AdmissionDecision:
    """合议准入。approved=True 仅当全部证据自洽且通过;否则目标状态「候选」并列出阻断原因。

    spec: ExecutableStrategySpec(取 spec_hash 校验证据身份一致)。
    """
    from core.analysis.nine_gate_policy import decide_nine_gate

    reasons: list[str] = []

    if evidence.spec_hash != getattr(spec, "spec_hash", None):
        reasons.append("spec_hash_mismatch")
    if evidence.phase1.get("status") != "PASS":
        reasons.append("phase1_failed")
    if evidence.phase3.get("verdict") != "PASS":
        reasons.append("phase3_failed")

    # Nine-Gate:唯一裁决,不接受 DSR-only
    if not decide_nine_gate(evidence.nine_gate).approved:
        reasons.append("nine_gate_not_passed")

    # holdout:必须恰好消费一次(§5.2 金库单次)
    if evidence.holdout.get("peek_count") != 1:
        reasons.append("holdout_not_single_use")

    if not evidence.experiment_ids:
        reasons.append("evidence_missing")

    if require_marginal and evidence.marginal.get("verdict") != "PASS":
        reasons.append("marginal_failed")

    if admission_track not in {"standalone", "diversifier"}:
        reasons.append("invalid_admission_track")

    approved = not reasons
    return AdmissionDecision(
        approved=approved,
        target_status="在册" if approved else "候选",
        blocking_reasons=tuple(reasons),
    )
