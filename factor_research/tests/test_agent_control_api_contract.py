"""Agent control API contract smoke tests.

Run:
    cd factor_research && python3 tests/test_agent_control_api_contract.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.routers.agent_control import action_policy, artifact_inventory, module_inventory


def test_module_inventory_endpoint_shape():
    payload = module_inventory()
    assert isinstance(payload, list)
    assert payload
    assert {"module", "status", "role"}.issubset(payload[0])


def test_artifact_inventory_endpoint_shape():
    payload = artifact_inventory()
    names = {row["name"] for row in payload}
    assert "data_lake" in names
    assert "scratch" in names


def test_action_policy_endpoint_shape():
    payload = action_policy(action="write_registry", target="strategy_versions.json")
    assert payload["allowed"] is False
    assert payload["required_entrypoint"] == "strategy_registry.register"


def test_action_policy_endpoint_rejects_unknown_action_with_400():
    from fastapi import HTTPException

    try:
        action_policy(action="delete_everything", target="x")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "delete_everything" in str(exc.detail)
    else:
        raise AssertionError("unknown action should raise HTTPException(400), not 500")


if __name__ == "__main__":
    test_module_inventory_endpoint_shape()
    test_artifact_inventory_endpoint_shape()
    test_action_policy_endpoint_shape()
    test_action_policy_endpoint_rejects_unknown_action_with_400()
    print("agent control API contract tests passed")
