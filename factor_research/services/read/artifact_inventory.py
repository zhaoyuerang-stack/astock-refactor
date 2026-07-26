"""Agent-readable artifact boundary inventory."""
from __future__ import annotations

from contracts.agent_control import ArtifactPolicy

_POLICIES = {
    "data_lake": ArtifactPolicy(
        name="data_lake",
        path="data_lake/",
        read_allowed=True,
        write_allowed=False,
        formal_evidence_allowed=True,
        writer="lake/ or scripts/data/ controlled writers only",
    ),
    "reports": ArtifactPolicy(
        name="reports",
        path="reports/",
        read_allowed=True,
        write_allowed=True,
        formal_evidence_allowed=True,
        writer="report-generation tools and approved workflows",
    ),
    "signals": ArtifactPolicy(
        name="signals",
        path="signals/",
        read_allowed=True,
        write_allowed=False,
        formal_evidence_allowed=True,
        writer="run_daily.py only",
    ),
    "paper": ArtifactPolicy(
        name="paper",
        path="paper/",
        read_allowed=True,
        write_allowed=False,
        formal_evidence_allowed=True,
        writer="portfolio.paper_engine or approved ops scripts",
    ),
    "scratch": ArtifactPolicy(
        name="scratch",
        path="scratch/",
        read_allowed=True,
        write_allowed=True,
        formal_evidence_allowed=False,
        writer="temporary experiments only",
    ),
    "results": ArtifactPolicy(
        name="results",
        path="results/",
        read_allowed=True,
        write_allowed=False,
        formal_evidence_allowed=False,
        writer="deprecated; rehome to reports or archive",
    ),
    "logs": ArtifactPolicy(
        name="logs",
        path="logs/",
        read_allowed=True,
        write_allowed=False,
        formal_evidence_allowed=False,
        writer="runtime logging only",
    ),
}


def get_artifact_inventory() -> list[ArtifactPolicy]:
    return list(_POLICIES.values())


def get_artifact_policy(name: str) -> ArtifactPolicy:
    try:
        return _POLICIES[name]
    except KeyError as exc:
        raise KeyError(f"Unknown artifact policy: {name}") from exc
