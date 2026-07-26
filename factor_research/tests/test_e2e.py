"""End-to-end smoke tests."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_run_daily_no_update():
    """run_daily.py --no-update completes without error."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "run_daily.py", "--no-update"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode in (0, 2), f"run_daily failed with code {result.returncode}: {result.stderr[:200]}"
    # rc=2 是 DeploymentManifest(Task 7)或 readiness(Task 9)的合法 fail-closed 拒绝,
    # 不是崩溃;三种终态都算"无错误完成"。
    assert (
        "保存信号" in result.stdout
        or "空仓观望" in result.stdout
        or "拒绝生成正式信号" in result.stdout
    )
    print("✅ test_run_daily_no_update passed")


def test_validate_final():
    """validate_final.py runs and reports clean_ratio."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "validate_final.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "干净" in result.stdout
    print("✅ test_validate_final passed")


if __name__ == "__main__":
    test_run_daily_no_update()
    test_validate_final()
    print("\n🎉 All e2e tests passed!")
