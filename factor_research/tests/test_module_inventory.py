"""Module inventory reader tests.

Run:
    cd factor_research && python3 tests/test_module_inventory.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.read.module_inventory import get_module_inventory, get_module_status


def test_all_top_level_directories_have_module_status():
    inventory = get_module_inventory()
    modules = {item.module for item in inventory}
    top_dirs = {
        p.name for p in ROOT.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name != "__pycache__"
    }
    assert top_dirs == modules


def test_core_status_parsed_and_execution_archived():
    core = get_module_status("core")
    assert core.status == "ONLINE_CRITICAL"
    assert "BacktestEngine" in core.role
    # execution 已归档至 docs/archive/execution(2026-07-18);无 ADR 不得复活(R-ARCH-005)
    modules = {item.module for item in get_module_inventory()}
    assert "execution" not in modules


def test_inventory_items_are_plain_dict_serializable():
    payload = [item.to_dict() for item in get_module_inventory()]
    first = payload[0]
    assert set(first) == {"module", "path", "status", "role", "keep_reason", "boundary"}


if __name__ == "__main__":
    test_all_top_level_directories_have_module_status()
    test_core_status_parsed_and_execution_archived()
    test_inventory_items_are_plain_dict_serializable()
    print("module inventory tests passed")
