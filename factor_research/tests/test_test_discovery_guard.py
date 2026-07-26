"""Task 16: 测试发现完整性守卫的自测。"""
import pytest

from scripts.ci.check_test_discovery import discover_test_files, main


def test_discovers_pytest_style_files():
    files = discover_test_files()
    # 本整改新增的测试必须在发现集中(否则会被静默排除)
    assert "tests/test_strategy_spec.py" in files
    assert "tests/test_nine_gate_policy.py" in files
    assert "tests/test_deployment_manifest.py" in files


def test_guard_passes_on_current_repo():
    # 全仓 pytest 风格测试都应可被收集(无静默排除)
    assert main() == 0


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
