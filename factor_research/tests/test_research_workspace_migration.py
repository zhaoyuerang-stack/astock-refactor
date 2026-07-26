"""Idempotent migration of legacy reviews, shadow identity and artifact links."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def test_shadow_registration_is_idempotent_and_does_not_invent_metrics():
    import strategy_registry
    from scripts.repair.migrate_research_workspace import ensure_ontology_shadow_registered

    root = Path(tempfile.mkdtemp())
    registry_path = root / "strategy_versions.json"
    registry_path.write_text('{"families":[]}', encoding="utf-8")
    original = strategy_registry.REGISTRY
    strategy_registry.REGISTRY = registry_path
    try:
        assert ensure_ontology_shadow_registered() is True
        assert ensure_ontology_shadow_registered() is False
    finally:
        strategy_registry.REGISTRY = original

    data = json.loads(registry_path.read_text(encoding="utf-8"))
    family = next(row for row in data["families"] if row["id"] == "ontology_industry")
    version = family["versions"][0]
    assert version["version"] == "v1.0-shadow"
    assert version["status"] == "候选"
    assert version["metrics"] == {}
    assert version["nine_gate"] == {}


def test_artifact_link_migration_records_each_strategy_once():
    from research_ledger.ledger import ResearchLedger
    from scripts.repair.migrate_research_workspace import migrate_artifact_links

    root = Path(tempfile.mkdtemp())
    ledger = ResearchLedger(path=root / "ledger.jsonl")
    index_path = root / "index.json"

    assert migrate_artifact_links(ledger=ledger, index_path=index_path) == 2
    assert migrate_artifact_links(ledger=ledger, index_path=index_path) == 0
    runs = ledger.list_research_runs()
    by_hypothesis = {row.hypothesis: row for row in runs}
    assert set(by_hypothesis) == {"amount-timing/v1.0", "ontology_industry/v1.0-shadow"}
    assert len(by_hypothesis["ontology_industry/v1.0-shadow"].artifact_paths) == 3


if __name__ == "__main__":
    test_shadow_registration_is_idempotent_and_does_not_invent_metrics()
    test_artifact_link_migration_records_each_strategy_once()
    print("research workspace migration tests passed")
