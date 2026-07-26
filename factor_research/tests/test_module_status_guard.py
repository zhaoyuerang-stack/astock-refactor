"""Module status CI guard tests.

Run:
    cd factor_research && python3 tests/test_module_status_guard.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_module_status_guard_passes_current_tree():
    proc = subprocess.run(
        [sys.executable, "scripts/ci/check_module_status.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


if __name__ == "__main__":
    test_module_status_guard_passes_current_tree()
    print("module status guard tests passed")
