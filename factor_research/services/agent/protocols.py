"""Validation protocol registry (ADR-037 Workflow-as-Protocol).

Agent may only select a registered protocol; it may not invent validation paths.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contracts.evidence import EvidenceTier
from services.agent.tools import RISK_HIGH, RISK_MID, RISK_READONLY


@dataclass(frozen=True)
class ProtocolSpec:
    protocol_id: str
    description: str
    allowed_tools: tuple[str, ...]
    default_tier: EvidenceTier
    risk: str
    requires_hitl: bool = False
    forbidden_claims: tuple[str, ...] = (
        "策略有效",
        "可入册",
        "可实盘",
        "已验证 alpha",
    )


_PROTOCOLS: dict[str, ProtocolSpec] = {
    "idea_precheck": ProtocolSpec(
        protocol_id="idea_precheck",
        description="Strategy idea boundary precheck (no backtest)",
        allowed_tools=("strategy_idea_check", "data_quality", "factors", "strategies", "experiments"),
        default_tier=EvidenceTier.PRECHECK,
        risk=RISK_READONLY,
    ),
    "data_gap_audit": ProtocolSpec(
        protocol_id="data_gap_audit",
        description="Lake/registry field reachability audit",
        allowed_tools=("data_gap_audit", "strategy_idea_check", "factors"),
        default_tier=EvidenceTier.PRECHECK,
        risk=RISK_READONLY,
    ),
    "proxy_or_signal_probe": ProtocolSpec(
        protocol_id="proxy_or_signal_probe",
        description="L0 signal probe receipt (not alpha, not admission)",
        allowed_tools=("run_signal_probe", "data_gap_audit", "factors"),
        default_tier=EvidenceTier.L0_PROBE,
        risk=RISK_READONLY,
    ),
    "engine_backtest": ProtocolSpec(
        protocol_id="engine_backtest",
        description="Formal BacktestEngine run with fixed CostModel",
        allowed_tools=("run_backtest", "data_quality"),
        default_tier=EvidenceTier.ENGINE,
        risk=RISK_MID,
        requires_hitl=True,
    ),
    "nine_gate_or_promote": ProtocolSpec(
        protocol_id="nine_gate_or_promote",
        description="Proposal only: 9-Gate / promote requires human",
        allowed_tools=("propose_high_risk_action", "strategies", "experiments"),
        default_tier=EvidenceTier.NARRATIVE,
        risk=RISK_HIGH,
        requires_hitl=True,
    ),
    "data_source_onboarding": ProtocolSpec(
        protocol_id="data_source_onboarding",
        description="Proposal only: S0-S7 onboarding playbook",
        allowed_tools=("propose_high_risk_action", "data_gap_audit"),
        default_tier=EvidenceTier.NARRATIVE,
        risk=RISK_HIGH,
        requires_hitl=True,
    ),
    "stock_diagnosis": ProtocolSpec(
        protocol_id="stock_diagnosis",
        description="Single-stock readonly diagnosis",
        allowed_tools=("resolve_stock_code", "stock_profile", "data_quality"),
        default_tier=EvidenceTier.PRECHECK,
        risk=RISK_READONLY,
    ),
}


class UnknownProtocolError(KeyError):
    pass


def list_protocols() -> list[dict[str, Any]]:
    return [
        {
            "protocol_id": p.protocol_id,
            "description": p.description,
            "allowed_tools": list(p.allowed_tools),
            "default_tier": p.default_tier.value,
            "risk": p.risk,
            "requires_hitl": p.requires_hitl,
            "forbidden_claims": list(p.forbidden_claims),
        }
        for p in _PROTOCOLS.values()
    ]


def get_protocol(protocol_id: str) -> ProtocolSpec:
    try:
        return _PROTOCOLS[protocol_id]
    except KeyError as exc:
        raise UnknownProtocolError(
            f"unknown protocol_id={protocol_id!r}; registered={sorted(_PROTOCOLS)}"
        ) from exc


def assert_tool_allowed(protocol_id: str, tool_name: str) -> None:
    spec = get_protocol(protocol_id)
    if tool_name not in spec.allowed_tools:
        raise PermissionError(
            f"tool {tool_name!r} not allowed under protocol {protocol_id!r}; "
            f"allowed={list(spec.allowed_tools)}"
        )
