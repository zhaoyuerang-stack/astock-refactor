"""Naming taxonomy guard tests.

Run:
    cd factor_research && python3 tests/test_naming_taxonomy_guard.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_guard():
    return subprocess.run(
        [sys.executable, "scripts/ci/check_naming_taxonomy.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def test_naming_taxonomy_guard_passes_current_tree():
    proc = _run_guard()
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_naming_taxonomy_guard_rejects_new_ambiguous_module():
    """Negative case: guard must FAIL when a forbidden basename appears
    outside the compatibility whitelist. Without this, a no-op guard would
    still satisfy the positive test."""
    probe_dir = ROOT / "factory" / "_taxonomy_guard_probe"
    try:
        probe_dir.mkdir(parents=True, exist_ok=True)
        (probe_dir / "composer.py").write_text('"""probe"""\n', encoding="utf-8")
        (probe_dir / "filter.py").write_text('"""probe"""\n', encoding="utf-8")
        proc = _run_guard()
        assert proc.returncode == 1, "guard did not reject a new ambiguous module"
        assert "composer.py" in proc.stdout
        assert "filter.py" in proc.stdout
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)


def test_naming_taxonomy_guard_ignores_scratch_dir():
    """False-positive guard: a forbidden basename under scratch/ must NOT trip
    the guard, so migration/experiment dirs stay unblocked."""
    probe_dir = ROOT / "scratch" / "_taxonomy_guard_probe"
    try:
        probe_dir.mkdir(parents=True, exist_ok=True)
        (probe_dir / "composer.py").write_text('"""probe"""\n', encoding="utf-8")
        proc = _run_guard()
        assert proc.returncode == 0, "guard wrongly flagged a scratch/ file: " + proc.stdout
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)


if __name__ == "__main__":
    test_naming_taxonomy_guard_passes_current_tree()
    test_naming_taxonomy_guard_rejects_new_ambiguous_module()
    test_naming_taxonomy_guard_ignores_scratch_dir()
    print("naming taxonomy guard tests passed")
