"""Read optional experiment artifacts without hiding corrupt evidence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


class ArtifactReadError(RuntimeError):
    """An artifact exists but cannot be trusted as valid JSON."""


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactReadError(f"invalid experiment artifact: {path}") from exc


def shadow_incubation(root: Path = ROOT) -> dict:
    return {
        "incubation": _read_json(
            root / "data_lake" / "agent" / "shadow_incubation_log.json", {}
        ),
        "predictions": _read_json(
            root / "data_lake" / "research_signals" / "ontology_predictions.json", {}
        ),
        "performance": _read_json(
            root / "reports" / "islands" / "shadow_ontology_performance.json", {}
        ),
    }


def amount_timing_validation(root: Path = ROOT) -> dict:
    return _read_json(
        root / "reports" / "ops" / "amount_timing_validation.json",
        {
            "latest_signal": None,
            "all_market": [],
            "ex688": [],
            "cost_sensitivity": [],
            "walk_forward": [],
        },
    )


def logical_chains(root: Path = ROOT) -> list[dict]:
    logic_dir = root / "data_lake" / "research_signals" / "logic_chains"
    if not logic_dir.exists():
        return []
    return [_read_json(path, {}) for path in sorted(logic_dir.glob("*.json"))]


def industry_knowledge_graph(root: Path = ROOT) -> dict:
    return _read_json(
        root / "data_lake" / "research_signals" / "industry_knowledge_graph.json",
        {"nodes": [], "links": [], "meta": {"total_nodes": 0, "total_links": 0}},
    )
