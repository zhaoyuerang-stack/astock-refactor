"""Strategy lifecycle read-model tests.

Run:
    cd factor_research && python3 tests/test_strategy_lifecycle_view.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.read.strategy_lifecycle import get_strategy_lifecycle, list_strategy_lifecycles


def test_list_strategy_lifecycles_returns_plain_dicts():
    rows = list_strategy_lifecycles()
    assert isinstance(rows, list)
    if rows:
        row = rows[0]
        assert {"family", "version", "status", "allowed_agent_actions", "blocked_agent_actions"}.issubset(row)


def test_missing_strategy_returns_blocked_view():
    row = get_strategy_lifecycle("missing-family", "v0")
    assert row["status"] == "missing"
    assert "promote" not in row["allowed_agent_actions"]
    assert "direct_registry_write" in row["blocked_agent_actions"]


if __name__ == "__main__":
    test_list_strategy_lifecycles_returns_plain_dicts()
    test_missing_strategy_returns_blocked_view()
    print("strategy lifecycle view tests passed")
