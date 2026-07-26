"""Assemble and police Evidence Envelopes for product Agent paths (ADR-037)."""
from __future__ import annotations

from typing import Any

from contracts.agent_control import AgentAction
from contracts.evidence import (
    EvidenceEnvelope,
    EvidenceTier,
    make_envelope,
    payload_has_performance_metrics,
    strip_performance_for_display,
)
from services.read.action_policy import can_agent_do


def wrap_tool_result(
    *,
    tool_name: str,
    result: Any,
    evidence_tier: EvidenceTier | str,
    protocol_id: str | None = None,
    summary: str = "",
    requires_human_confirmation: bool = False,
    can_claim_valid: bool = False,
    limits: list[str] | None = None,
) -> EvidenceEnvelope:
    """Wrap a Strict-rail tool result. Default can_claim_valid=False."""
    payload = result if isinstance(result, dict) else {"value": result}
    tier = EvidenceTier(evidence_tier) if isinstance(evidence_tier, str) else evidence_tier
    # Narrative never carries performance.
    if tier == EvidenceTier.NARRATIVE and payload_has_performance_metrics(payload):
        payload = strip_performance_for_display(payload)
    return make_envelope(
        evidence_tier=tier,
        protocol_id=protocol_id,
        sources=[f"tool:{tool_name}"],
        summary=summary or str(payload)[:200],
        payload=payload,
        can_claim_valid=can_claim_valid,
        requires_human_confirmation=requires_human_confirmation,
        limits=limits
        or [
            "can_claim_valid defaults false unless gated admission fields",
            "fake_curve_allowed=false (ADR-037)",
        ],
    )


def public_view(envelope: EvidenceEnvelope) -> dict[str, Any]:
    """What UI may render. Strips performance when not allowed."""
    d = envelope.as_public_dict()
    if not envelope.allows_performance_display():
        d["payload"] = strip_performance_for_display(envelope.payload)
        d["performance_redacted"] = True
    else:
        d["performance_redacted"] = False
    return d


def assert_formal_evidence_path(path: str) -> None:
    """Raise if path cannot be formal evidence (lab wash prevention)."""
    decision = can_agent_do(AgentAction.USE_FORMAL_EVIDENCE, path)
    if not decision.allowed:
        raise PermissionError(decision.reason)


def is_formal_evidence_path(path: str) -> bool:
    return bool(can_agent_do(AgentAction.USE_FORMAL_EVIDENCE, path).allowed)
