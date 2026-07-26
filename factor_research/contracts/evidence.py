"""Evidence Envelope (ADR-037) — product-facing proof contract.

Formal UI conclusions must carry an envelope. LLM narrative alone is never
``can_claim_valid=True``. Performance metrics without a Strict-tier envelope
must not be presented as system-verified facts.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class EvidenceTier(StrEnum):
    NARRATIVE = "narrative"
    PRECHECK = "precheck"
    L0_PROBE = "l0_probe"
    ENGINE = "engine"
    GATED = "gated"


# Tiers that may surface numeric performance as "system facts" (still not alpha
# admission unless gated fields say so).
_STRICT_PERFORMANCE_TIERS = {EvidenceTier.ENGINE, EvidenceTier.GATED}


class EvidenceEnvelope(BaseModel):
    """Mandatory wrapper for product Agent conclusions (ADR-037 §4)."""

    evidence_tier: EvidenceTier = EvidenceTier.NARRATIVE
    can_claim_valid: bool = False
    fake_curve_allowed: bool = False
    protocol_id: str | None = None
    sources: list[str] = Field(default_factory=list)
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    requires_human_confirmation: bool = False
    limits: list[str] = Field(default_factory=list)

    @field_validator("can_claim_valid")
    @classmethod
    def _default_false_unless_gated(cls, v: bool, info) -> bool:
        # model_validator enforces gated-only True; keep field default False.
        return bool(v)

    @model_validator(mode="after")
    def _enforce_adr037(self) -> EvidenceEnvelope:
        # Never allow fake equity curves in product envelopes.
        object.__setattr__(self, "fake_curve_allowed", False)

        # can_claim_valid only legal at gated tier, and still defaults False
        # unless explicitly set with gated + sources (admission fields only).
        if self.can_claim_valid and self.evidence_tier != EvidenceTier.GATED:
            raise ValueError(
                "can_claim_valid=True is only allowed when evidence_tier=gated "
                "(ADR-037 / R-LLM-001)"
            )
        if self.evidence_tier == EvidenceTier.NARRATIVE and self._payload_has_performance():
            raise ValueError(
                "narrative tier must not carry performance metrics as system facts "
                "(ADR-037 Evidence Envelope)"
            )
        if self.evidence_tier in _STRICT_PERFORMANCE_TIERS and not self.sources:
            raise ValueError(
                f"{self.evidence_tier.value} tier requires non-empty sources[] "
                "(Strict-rail provenance)"
            )
        return self

    def _payload_has_performance(self) -> bool:
        return payload_has_performance_metrics(self.payload)

    def allows_performance_display(self) -> bool:
        """Whether UI may show annual/sharpe/maxdd as system-verified numbers."""
        return (
            self.evidence_tier in _STRICT_PERFORMANCE_TIERS
            and bool(self.sources)
            and not self.fake_curve_allowed
        )

    def as_public_dict(self) -> dict[str, Any]:
        d = self.model_dump(mode="json")
        d["evidence_tier"] = self.evidence_tier.value
        d["allows_performance_display"] = self.allows_performance_display()
        return d


_PERF_KEYS = frozenset(
    {
        "annual",
        "annual_return",
        "sharpe",
        "maxdd",
        "max_drawdown",
        "calmar",
        "nav",
        "equity_curve",
        "total_return",
        "icir",
        "dsr_p",
    }
)


def payload_has_performance_metrics(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    keys = {str(k).lower() for k in payload.keys()}
    if keys & _PERF_KEYS:
        return True
    # nested common shapes
    for nest in ("metrics", "performance", "result", "backtest"):
        inner = payload.get(nest)
        if isinstance(inner, dict) and payload_has_performance_metrics(inner):
            return True
    return False


def make_envelope(
    *,
    evidence_tier: EvidenceTier | str,
    protocol_id: str | None = None,
    sources: list[str] | None = None,
    summary: str = "",
    payload: dict[str, Any] | None = None,
    can_claim_valid: bool = False,
    requires_human_confirmation: bool = False,
    limits: list[str] | None = None,
) -> EvidenceEnvelope:
    tier = EvidenceTier(evidence_tier) if isinstance(evidence_tier, str) else evidence_tier
    return EvidenceEnvelope(
        evidence_tier=tier,
        can_claim_valid=can_claim_valid,
        fake_curve_allowed=False,
        protocol_id=protocol_id,
        sources=list(sources or []),
        summary=summary,
        payload=dict(payload or {}),
        requires_human_confirmation=requires_human_confirmation,
        limits=list(limits or []),
    )


def strip_performance_for_display(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop performance keys so narrative cannot smuggle metrics into UI."""
    if not isinstance(payload, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if str(k).lower() in _PERF_KEYS:
            continue
        if isinstance(v, dict) and str(k).lower() in {"metrics", "performance", "backtest", "result"}:
            # keep non-perf nested metadata only
            cleaned = strip_performance_for_display(v)
            if cleaned:
                out[k] = cleaned
            continue
        out[k] = v
    return out
