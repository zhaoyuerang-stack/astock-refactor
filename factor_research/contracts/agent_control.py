"""Agent control-plane contracts.

These are plain dataclasses so CLI, tests, services, and API routers can share
structured payloads without introducing a new framework dependency.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class ModuleStatus(StrEnum):
    ONLINE = "ONLINE"
    ONLINE_CRITICAL = "ONLINE_CRITICAL"
    ONLINE_CRITICAL_ARTIFACTS = "ONLINE_CRITICAL_ARTIFACTS"
    ONLINE_SUPPORT = "ONLINE_SUPPORT"
    ONLINE_RESEARCH = "ONLINE_RESEARCH"
    ONLINE_GOVERNANCE = "ONLINE_GOVERNANCE"
    ONLINE_CONFIG = "ONLINE_CONFIG"
    ONLINE_DOCS = "ONLINE_DOCS"
    ONLINE_ARTIFACTS = "ONLINE_ARTIFACTS"
    ONLINE_GUARDRAILS = "ONLINE_GUARDRAILS"
    CLI_ENTRYPOINTS = "CLI_ENTRYPOINTS"
    MIXED_ENTRYPOINTS = "MIXED_ENTRYPOINTS"
    RESEARCH_ENTRYPOINTS = "RESEARCH_ENTRYPOINTS"
    RESEARCH_SUPPORT = "RESEARCH_SUPPORT"
    RESEARCH_DIAGNOSTIC = "RESEARCH_DIAGNOSTIC"
    STAGING = "STAGING"
    STAGING_GOVERNANCE = "STAGING_GOVERNANCE"
    ARCHIVE_OR_REHOME = "ARCHIVE_OR_REHOME"
    ARTIFACTS_ONLY = "ARTIFACTS_ONLY"
    TEMP_ONLY = "TEMP_ONLY"


class AgentAction(StrEnum):
    READ = "read"
    WRITE_ARTIFACT = "write_artifact"
    WRITE_REGISTRY = "write_registry"
    WRITE_DATA_LAKE = "write_data_lake"
    PROMOTE_CANDIDATE = "promote_candidate"
    RUN_VALIDATION = "run_validation"
    RUN_DAILY = "run_daily"
    UPDATE_DEPLOYMENT = "update_deployment"
    USE_FORMAL_EVIDENCE = "use_formal_evidence"
    ARCHIVE_MODULE = "archive_module"


@dataclass(frozen=True)
class ModuleInventoryItem:
    module: str
    path: str
    status: str
    role: str
    keep_reason: str
    boundary: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactPolicy:
    name: str
    path: str
    read_allowed: bool
    write_allowed: bool
    formal_evidence_allowed: bool
    writer: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ActionDecision:
    allowed: bool
    action: AgentAction
    target: str
    reason: str
    required_entrypoint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["action"] = self.action.value
        return payload
