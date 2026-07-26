"""Artifact inventory tests.

Run:
    cd factor_research && python3 tests/test_artifact_inventory.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.read.artifact_inventory import get_artifact_inventory, get_artifact_policy


def test_canonical_artifacts_are_declared():
    policies = {p.name: p for p in get_artifact_inventory()}
    for name in ["data_lake", "reports", "signals", "paper", "scratch", "results", "logs"]:
        assert name in policies


def test_scratch_and_results_are_not_formal_evidence():
    assert get_artifact_policy("scratch").formal_evidence_allowed is False
    assert get_artifact_policy("results").formal_evidence_allowed is False


def test_data_lake_write_is_restricted():
    policy = get_artifact_policy("data_lake")
    assert policy.read_allowed is True
    assert policy.write_allowed is False
    assert "scripts/data" in policy.writer


if __name__ == "__main__":
    test_canonical_artifacts_are_declared()
    test_scratch_and_results_are_not_formal_evidence()
    test_data_lake_write_is_restricted()
    print("artifact inventory tests passed")
