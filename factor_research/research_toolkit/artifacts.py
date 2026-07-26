"""Artifact models for host-scoped control rules."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ArtifactType(Enum):
    VETO_FILTER = "VetoFilter"
    OVERLAY = "Overlay"
    RISK_FILTER = "RiskFilter"


@dataclass(frozen=True)
class HostSpec:
    family: str
    version: str

    def to_dict(self) -> dict[str, str]:
        return {"family": self.family, "version": self.version}


@dataclass(frozen=True)
class ControlArtifact:
    artifact_id: str
    name: str
    artifact_type: ArtifactType
    host: HostSpec
    hypothesis: str
    config: dict[str, Any] = field(default_factory=dict)
    registry_status: str = "条件假设/观察"
    has_independent_nav: bool = False

    @classmethod
    def veto_filter(
        cls,
        *,
        artifact_id: str,
        name: str,
        host: HostSpec,
        veto_q: float,
        hypothesis: str,
        config: dict[str, Any] | None = None,
    ) -> ControlArtifact:
        merged = {
            "artifact_type": ArtifactType.VETO_FILTER.value,
            "host": host.to_dict(),
            "veto_q": float(veto_q),
            "application": "filter candidate pool before top_n; refill positions; rebalance-day only",
        }
        if config:
            merged.update(config)
        return cls(
            artifact_id=artifact_id,
            name=name,
            artifact_type=ArtifactType.VETO_FILTER,
            host=host,
            hypothesis=hypothesis,
            config=merged,
            registry_status="条件假设/观察",
            has_independent_nav=False,
        )

    def to_registry_config(self) -> dict[str, Any]:
        return dict(self.config)
