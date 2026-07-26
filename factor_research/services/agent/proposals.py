"""High-risk action proposals only (ADR-037). Never executes register/promote/deploy."""
from __future__ import annotations

from typing import Any

from contracts.agent_control import AgentAction
from services.read.action_policy import can_agent_do

_ALLOWED_PROPOSALS = {
    "promote": {
        "action": AgentAction.PROMOTE_CANDIDATE,
        "entrypoint": "workflow.promote / apps/factory_cli.py promote",
    },
    "register": {
        "action": AgentAction.WRITE_REGISTRY,
        "entrypoint": "strategy_registry.register",
    },
    "onboarding_write_lake": {
        "action": AgentAction.WRITE_DATA_LAKE,
        "entrypoint": "lake/ or scripts/data/ + data_source_onboarding S0-S7",
    },
    "deploy": {
        "action": AgentAction.UPDATE_DEPLOYMENT,
        "entrypoint": "deployments/production.json via human ops",
    },
}


def propose_high_risk_action(
    *,
    action_kind: str,
    target: str = "",
    rationale: str = "",
) -> dict[str, Any]:
    if action_kind not in _ALLOWED_PROPOSALS:
        return {
            "proposed": False,
            "error": f"unknown action_kind={action_kind!r}",
            "allowed_kinds": sorted(_ALLOWED_PROPOSALS),
            "can_claim_valid": False,
            "executed": False,
        }
    meta = _ALLOWED_PROPOSALS[action_kind]
    policy_note = ""
    try:
        act = meta["action"]
        if isinstance(act, AgentAction):
            d = can_agent_do(act, target or action_kind)
            policy_note = d.reason
            entry = d.required_entrypoint or meta["entrypoint"]
        else:
            entry = meta["entrypoint"]
    except Exception as exc:
        entry = meta["entrypoint"]
        policy_note = str(exc)

    return {
        "proposed": True,
        "executed": False,
        "action_kind": action_kind,
        "target": target,
        "rationale": rationale,
        "required_entrypoint": entry,
        "policy_note": policy_note,
        "can_claim_valid": False,
        "requires_human_confirmation": True,
        "limits": [
            "Agent must not execute this action",
            "Human runs canonical entrypoint after review",
            "R-WF-001 / R-REG-001 / R-PROD-001 unchanged",
        ],
    }
