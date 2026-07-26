"""Agent-readable strategy lifecycle view."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "strategy_versions.json"


def _load_registry() -> dict[str, Any]:
    if not REGISTRY.exists():
        return {"families": []}
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def _actions_for_status(status: str) -> tuple[list[str], list[str]]:
    blocked = ["direct_registry_write", "bypass_workflow", "use_scratch_as_evidence"]
    if status in {"候选", "candidate", "CANDIDATE"}:
        return ["run_validation", "request_review"], blocked
    if status in {"参考", "REGISTERED_REFERENCE", "reference"}:
        return ["monitor", "rerun_gates"], blocked + ["deploy_without_gate"]
    if status in {"在册", "ACTIVE", "active"}:
        return ["monitor", "run_daily_if_deployed", "decay_check"], blocked
    if status in {"退役", "RETIRED", "retired"}:
        return ["explain_retirement"], blocked + ["reactivate_without_new_workflow"]
    return ["inspect"], blocked


def list_strategy_lifecycles() -> list[dict[str, Any]]:
    data = _load_registry()
    rows: list[dict[str, Any]] = []
    for family in data.get("families", []):
        family_id = family.get("id", "")
        for version in family.get("versions", []):
            version_id = version.get("version", "")
            status = str(version.get("status", "unknown"))
            allowed, blocked = _actions_for_status(status)
            rows.append({
                "family": family_id,
                "version": version_id,
                "status": status,
                "family_status": family.get("status", ""),
                "has_metrics": bool(version.get("metrics")),
                "has_nine_gate": bool(version.get("nine_gate")),
                "allowed_agent_actions": allowed,
                "blocked_agent_actions": blocked,
            })
    return rows


def get_strategy_lifecycle(family: str, version: str) -> dict[str, Any]:
    for row in list_strategy_lifecycles():
        if row["family"] == family and row["version"] == version:
            return row
    return {
        "family": family,
        "version": version,
        "status": "missing",
        "family_status": "missing",
        "has_metrics": False,
        "has_nine_gate": False,
        "allowed_agent_actions": ["inspect"],
        "blocked_agent_actions": ["direct_registry_write", "promote", "deploy", "use_scratch_as_evidence"],
    }
