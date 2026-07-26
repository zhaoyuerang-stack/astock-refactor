"""Module status CI guard tests.

Run:
    cd factor_research && python3 tests/test_module_status_guard.py
"""
import subprocess
import sys
from pathlib import Path

from scripts.ci.check_module_status import is_governed_module_dir

ROOT = Path(__file__).resolve().parents[1]


def test_generated_package_metadata_is_not_a_governed_module(tmp_path):
    egg_info = tmp_path / "astcok_factor_research.egg-info"
    egg_info.mkdir()

    assert not is_governed_module_dir(egg_info)


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
