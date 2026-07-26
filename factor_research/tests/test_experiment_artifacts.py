from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.routers import experiments as router  # noqa: E402
from services.read.experiment_artifacts import (  # noqa: E402
    ArtifactReadError,
    amount_timing_validation,
    industry_knowledge_graph,
    logical_chains,
    shadow_incubation,
)


def test_missing_optional_artifacts_return_explicit_empty_shapes(tmp_path):
    assert shadow_incubation(tmp_path) == {
        "incubation": {},
        "predictions": {},
        "performance": {},
    }
    assert amount_timing_validation(tmp_path)["latest_signal"] is None
    assert logical_chains(tmp_path) == []
    assert industry_knowledge_graph(tmp_path)["meta"]["total_nodes"] == 0


def test_valid_artifacts_are_read_in_deterministic_order(tmp_path):
    logic_dir = tmp_path / "data_lake" / "research_signals" / "logic_chains"
    logic_dir.mkdir(parents=True)
    (logic_dir / "b.json").write_text(json.dumps({"id": "b"}), encoding="utf-8")
    (logic_dir / "a.json").write_text(json.dumps({"id": "a"}), encoding="utf-8")
    assert logical_chains(tmp_path) == [{"id": "a"}, {"id": "b"}]


def test_corrupt_existing_artifact_is_not_reported_as_empty(tmp_path):
    graph = (
        tmp_path
        / "data_lake"
        / "research_signals"
        / "industry_knowledge_graph.json"
    )
    graph.parent.mkdir(parents=True)
    graph.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ArtifactReadError):
        industry_knowledge_graph(tmp_path)


def test_api_translates_corrupt_artifact_to_service_unavailable(monkeypatch):
    def broken_reader():
        raise ArtifactReadError("invalid artifact")

    monkeypatch.setattr(router, "shadow_incubation", broken_reader)
    with pytest.raises(HTTPException) as exc_info:
        router.get_shadow_incubation()
    assert exc_info.value.status_code == 503
