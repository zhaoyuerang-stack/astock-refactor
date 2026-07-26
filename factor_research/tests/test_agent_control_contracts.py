"""Agent control contract tests.

Run:
    cd factor_research && python3 tests/test_agent_control_contracts.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contracts.agent_control import ActionDecision, AgentAction, ArtifactPolicy, ModuleStatus


def test_module_status_values_include_expected_taxonomy():
    expected = {
        "ONLINE",
        "ONLINE_CRITICAL",
        "ONLINE_GOVERNANCE",
        "RESEARCH_SUPPORT",
        "STAGING",
        "ARCHIVE_OR_REHOME",
        "ARTIFACTS_ONLY",
        "TEMP_ONLY",
    }
    assert expected.issubset({item.value for item in ModuleStatus})


def test_action_decision_serializes_to_plain_dict():
    decision = ActionDecision(
        allowed=False,
        action=AgentAction.WRITE_REGISTRY,
        target="strategy_versions.json",
        reason="registry writes must go through strategy_registry.register",
        required_entrypoint="strategy_registry.register",
    )
    payload = decision.to_dict()
    assert payload["allowed"] is False
    assert payload["action"] == "write_registry"
    assert payload["target"] == "strategy_versions.json"
    assert payload["required_entrypoint"] == "strategy_registry.register"


def test_artifact_policy_serializes_boundaries():
    policy = ArtifactPolicy(
        name="scratch",
        path="scratch/",
        read_allowed=True,
        write_allowed=True,
        formal_evidence_allowed=False,
        writer="temporary only",
    )
    payload = policy.to_dict()
    assert payload["formal_evidence_allowed"] is False
    assert payload["writer"] == "temporary only"


if __name__ == "__main__":
    test_module_status_values_include_expected_taxonomy()
    test_action_decision_serializes_to_plain_dict()
    test_artifact_policy_serializes_boundaries()
    print("agent control contract tests passed")
