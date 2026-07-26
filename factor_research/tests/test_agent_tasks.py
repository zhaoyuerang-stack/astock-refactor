"""Agent task action wrapper tests.

Run:
    cd factor_research && python3 tests/test_agent_tasks.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.actions.agent_tasks import describe_agent_task, guard_agent_action


def test_guard_blocks_direct_registry_write():
    decision = guard_agent_action("write_registry", "strategy_versions.json")
    assert decision["allowed"] is False
    assert decision["required_entrypoint"] == "strategy_registry.register"


def test_describe_module_cleanup_task_uses_inventory_and_policy():
    payload = describe_agent_task("module_cleanup", target="scratch")
    assert payload["task"] == "module_cleanup"
    assert payload["target"] == "scratch"
    assert payload["module"]["status"] == "TEMP_ONLY"
    assert payload["archive_policy"]["allowed"] is False


if __name__ == "__main__":
    test_guard_blocks_direct_registry_write()
    test_describe_module_cleanup_task_uses_inventory_and_policy()
    print("agent task tests passed")
