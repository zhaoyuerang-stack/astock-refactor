"""Cross-process regression tests for the two canonical append/update stores."""
from __future__ import annotations

import json
import multiprocessing
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _register_version(path: str, index: int) -> None:
    import strategy_registry

    strategy_registry.REGISTRY = Path(path)
    strategy_registry.register(
        family="family",
        version=f"v{index}",
        desc=f"worker {index}",
        config={"worker": index},
        data_scope={},
        metrics={"annual": 0.0, "maxdd": -0.1},
        status="候选",
    )


def _append_ledger(path: str, index: int) -> None:
    from research_ledger.ledger import ResearchLedger, ResearchRunRecord

    ResearchLedger(Path(path)).log_research_run(
        ResearchRunRecord(
            script=f"worker-{index}.py",
            hypothesis=f"concurrent write {index}",
            data_vintage={"worker": index},
            metrics={"worker": index},
            verdict="REJECT",
            artifact_paths=[],
            next_action="none",
            run_at=f"2026-01-01T00:00:{index:02d}",
        )
    )


def _run_processes(worker, path: Path, count: int = 8) -> None:
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=worker, args=(str(path), index))
        for index in range(count)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0


def test_registry_concurrent_updates_do_not_drop_versions(tmp_path):
    registry_path = tmp_path / "strategy_versions.json"
    registry_path.write_text(
        json.dumps({"families": [{"id": "family", "versions": []}]}),
        encoding="utf-8",
    )

    _run_processes(_register_version, registry_path)

    data = json.loads(registry_path.read_text(encoding="utf-8"))
    versions = data["families"][0]["versions"]
    assert {version["version"] for version in versions} == {f"v{i}" for i in range(8)}


def test_ledger_concurrent_appends_keep_hash_chain_intact(tmp_path):
    from research_ledger.ledger import ResearchLedger

    ledger_path = tmp_path / "research_ledger.jsonl"
    _run_processes(_append_ledger, ledger_path)

    assert len(ledger_path.read_text(encoding="utf-8").splitlines()) == 8
    assert ResearchLedger(ledger_path).verify_chain() == (True, [])
