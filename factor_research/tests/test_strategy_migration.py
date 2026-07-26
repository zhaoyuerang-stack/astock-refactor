import json

import strategy_registry
from scripts.repair.migrate_deployment import migrate_deployment
from scripts.repair.migrate_strategy_specs import migrate_strategy_specs


def _passed_nine_gate():
    return {
        "passed_all": True,
        "gates": {
            gate: {"verdict": "PASS"}
            for gate in ("0", "1", "2", "3", "4", "5", "6", "7", "7A", "8")
        },
    }


def _registry(path):
    path.write_text(json.dumps({
        "families": [{
            "id": "illiquidity",
            "versions": [{
                "version": "v3.1",
                "status": "在册",
                "config": {"factor": "Amihud", "top_n": 25, "rebal_days": 20},
                "evidence": {"old": "not-bound-to-new-spec"},
                "nine_gate": _passed_nine_gate(),
            }],
        }],
    }))


def test_strategy_spec_migration_is_dry_run_and_idempotent(tmp_path, monkeypatch):
    registry = tmp_path / "strategy_versions.json"
    _registry(registry)
    monkeypatch.setattr(strategy_registry, "REGISTRY", registry)
    before = registry.read_text()

    dry = migrate_strategy_specs(apply=False)
    assert registry.read_text() == before
    assert dry["mapped"][0]["family"] == "illiquidity"

    first = migrate_strategy_specs(apply=True)
    second = migrate_strategy_specs(apply=True)
    assert first["changed"] == 1
    assert second["changed"] == 0
    version = json.loads(registry.read_text())["families"][0]["versions"][0]
    assert len(version["executable_spec"]["spec_hash"]) == 64
    assert version["evidence"]["production_blocked"] is True


def test_unknown_config_requires_manual_review(tmp_path, monkeypatch):
    registry = tmp_path / "strategy_versions.json"
    registry.write_text(json.dumps({
        "families": [{
            "id": "unknown",
            "versions": [{"version": "v9", "status": "在册", "config": {}}],
        }],
    }))
    monkeypatch.setattr(strategy_registry, "REGISTRY", registry)

    report = migrate_strategy_specs(apply=False)
    assert report["manual_review_required"] == [{"family": "unknown", "version": "v9"}]


def test_deployment_migration_requires_explicit_validated_identity(tmp_path, monkeypatch):
    registry = tmp_path / "strategy_versions.json"
    _registry(registry)
    monkeypatch.setattr(strategy_registry, "REGISTRY", registry)
    migrate_strategy_specs(apply=True)
    manifest = tmp_path / "production.json"

    report = migrate_deployment(
        equity="illiquidity/v3.1",
        defensive=None,
        manifest_path=manifest,
        apply=True,
    )
    assert report["ready"] is True
    assert manifest.exists()
    payload = json.loads(manifest.read_text())
    assert payload["legs"][0]["family"] == "illiquidity"
    assert payload["legs"][0]["spec_hash"]
