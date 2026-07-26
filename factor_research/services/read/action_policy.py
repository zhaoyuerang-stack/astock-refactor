"""Agent action policy.

This module answers whether an agent may perform an action and which canonical
entrypoint must be used.
"""
from __future__ import annotations

from contracts.agent_control import ActionDecision, AgentAction
from services.read.artifact_inventory import get_artifact_inventory

# Canonical formal-evidence sources beyond the artifact areas (governance/registry).
_EVIDENCE_ALLOWED_EXTRA = {"workflow", "registry", "research_ledger"}
_REGISTRY_EVIDENCE_FILES = {"strategy_versions.json", "strategy_families.json"}


def _path_segments(target: str) -> set[str]:
    """Normalize a target into lowercase path segments.

    Case-insensitive, separator-agnostic (``/`` and ``\\``), and drops empty /
    ``.`` / ``..`` segments so a forbidden area is caught anywhere in the path,
    not just as a leading prefix.
    """
    raw = target.lower().replace("\\", "/")
    return {seg for seg in raw.split("/") if seg not in ("", ".", "..")}


def _evidence_verdict(target: str) -> tuple[bool, str, str | None]:
    """Decide if ``target`` may be treated as a formal evidence source.

    Forbidden areas win over everything (fail-safe), and unknown paths are
    rejected (positive whitelist / fail-closed) so scratch-derived data cannot
    be laundered into evidence by relocating or re-casing the path.
    """
    inventory = {p.name: p for p in get_artifact_inventory()}
    forbidden = {name for name, p in inventory.items() if not p.formal_evidence_allowed}
    allowed = {name for name, p in inventory.items() if p.formal_evidence_allowed}
    allowed |= _EVIDENCE_ALLOWED_EXTRA

    segments = _path_segments(target)
    if segments & forbidden:
        return (
            False,
            "Scratch, results, and logs are not formal evidence sources.",
            "workflow/registry/reports/research_ledger",
        )
    if (segments & allowed) or (segments & {f.lower() for f in _REGISTRY_EVIDENCE_FILES}):
        return (True, "Target is within a known formal-evidence area.", None)
    return (
        False,
        "Formal evidence must come from a known evidence area "
        "(data_lake/reports/signals/paper/workflow/registry/research_ledger).",
        "workflow/registry/reports/research_ledger",
    )


def can_agent_do(
    action: AgentAction | str,
    target: str,
    context: dict | None = None,
) -> ActionDecision:
    action = AgentAction(action)
    context = context or {}

    if action == AgentAction.WRITE_REGISTRY:
        return ActionDecision(
            allowed=False,
            action=action,
            target=target,
            reason="Direct registry writes are forbidden.",
            required_entrypoint="strategy_registry.register",
        )

    if action == AgentAction.WRITE_DATA_LAKE:
        return ActionDecision(
            allowed=False,
            action=action,
            target=target,
            reason="Data lake writes must use controlled lake or scripts/data writers.",
            required_entrypoint="lake/ or scripts/data/",
        )

    if action == AgentAction.PROMOTE_CANDIDATE:
        return ActionDecision(
            allowed=True,
            action=action,
            target=target,
            reason="Candidate promotion is allowed only through canonical workflow.",
            required_entrypoint="workflow.promote",
        )

    if action == AgentAction.RUN_VALIDATION:
        return ActionDecision(
            allowed=True,
            action=action,
            target=target,
            reason="Validation is allowed through canonical factory/workflow runners.",
            required_entrypoint="factory.lines or workflow.nine_gate_runner",
        )

    if action == AgentAction.RUN_DAILY:
        return ActionDecision(
            allowed=True,
            action=action,
            target=target,
            reason="Daily signal generation is allowed through production entrypoint.",
            required_entrypoint="run_daily.py",
        )

    if action == AgentAction.UPDATE_DEPLOYMENT:
        return ActionDecision(
            allowed=False,
            action=action,
            target=target,
            reason="Deployment changes require explicit human approval and registry consistency.",
            required_entrypoint="runtime.deployment with human approval",
        )

    if action == AgentAction.USE_FORMAL_EVIDENCE:
        allowed, reason, entrypoint = _evidence_verdict(target)
        return ActionDecision(
            allowed=allowed,
            action=action,
            target=target,
            reason=reason,
            required_entrypoint=entrypoint,
        )

    if action == AgentAction.ARCHIVE_MODULE:
        return ActionDecision(
            allowed=False,
            action=action,
            target=target,
            reason="Module archival requires inventory audit and explicit human approval.",
            required_entrypoint="module cleanup plan + approval",
        )

    if action == AgentAction.WRITE_ARTIFACT:
        return ActionDecision(
            allowed=False,
            action=action,
            target=target,
            reason="Generic artifact writes are blocked; use a domain-specific writer.",
            required_entrypoint="runtime.artifacts + approved writer",
        )

    return ActionDecision(
        allowed=True,
        action=action,
        target=target,
        reason="Read-only action allowed.",
        required_entrypoint=None,
    )
