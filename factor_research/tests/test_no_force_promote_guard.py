"""check_no_force_promote 守卫的 fixture 测试 + 实跑实库。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci.check_no_force_promote import (
    check,
    discover_auto_promote_files,
    scan_source,
)


def test_flags_force_true():
    src = "def f():\n    return promote_hypothesis(h, force=True)\n"
    v = scan_source(src, "x")
    assert len(v) == 1 and "force=True" in v[0]


def test_flags_run_marginal_false():
    src = "promote_pool_l3(version='v1.0', run_marginal=False)\n"
    v = scan_source(src, "x")
    assert len(v) == 1 and "run_marginal=False" in v[0]


def test_clean_passes():
    src = "promote_hypothesis(h, force=False, run_marginal=True, run_nine_gate=True)\n"
    assert scan_source(src, "x") == []


def test_force_false_not_flagged():
    src = "register(fam, ver, force=False)\n"
    assert scan_source(src, "x") == []


def test_live_repo_clean():
    # 存量命中在 PENDING_REMEDIATION → 实库应通过
    assert check() == 0


def test_known_auto_promoters_still_discovered():
    """默认扫描至少覆盖原枚举名单里的两个已知自动晋级脚本。"""
    rels = {str(p.relative_to(Path(__file__).resolve().parents[1]))
            for p in discover_auto_promote_files()}
    assert "scripts/ops/bulk_promote.py" in rels
    assert "scripts/ops/scheduled_factor_search.py" in rels


def test_new_auto_promoter_auto_scanned(tmp_path):
    """对抗性回归:新增自动晋级脚本必须被自动纳入扫描。"""
    ops = tmp_path / "scripts" / "ops"
    ops.mkdir(parents=True)
    evil = ops / "evil_promote.py"
    evil.write_text(
        "from workflow.promote import promote_pool_l3\n"
        "promote_pool_l3(version='v1.0', force=True)\n",
        encoding="utf-8",
    )
    (ops / "innocent.py").write_text("print('no promote here')\n", encoding="utf-8")

    found = discover_auto_promote_files(tmp_path)
    assert evil in found, "import 晋级通道的新脚本必须被默认扫描发现"
    assert (ops / "innocent.py") not in found

    assert check(tmp_path) == 1, "含 force=True 的自动晋级脚本必须让守卫失败"


def test_blind_spot_apps_and_runtime_scanned(tmp_path):
    """审计 #8 对抗:原盲区 apps/、runtime/ 下 force=True 样本必须被抓。"""
    for sub in ("apps", "runtime"):
        d = tmp_path / sub
        d.mkdir(parents=True)
        evil = d / "rogue_promote.py"
        evil.write_text(
            "from workflow.promote import promote_hypothesis\n"
            "promote_hypothesis(h, force=True)\n",
            encoding="utf-8",
        )
    # workflow/ library 层不扫
    wf = tmp_path / "workflow"
    wf.mkdir()
    (wf / "promote.py").write_text(
        "def promote_hypothesis(h, force=False):\n"
        "    pass\n"
        "promote_hypothesis(None, force=True)\n",
        encoding="utf-8",
    )
    # tests/ 不扫
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_evil.py").write_text(
        "from workflow.promote import promote_hypothesis\n"
        "promote_hypothesis(h, force=True)\n",
        encoding="utf-8",
    )

    found = discover_auto_promote_files(tmp_path)
    rels = {str(p.relative_to(tmp_path)) for p in found}
    assert "apps/rogue_promote.py" in rels
    assert "runtime/rogue_promote.py" in rels
    assert "workflow/promote.py" not in rels
    assert "tests/test_evil.py" not in rels

    assert check(tmp_path) == 1, "原盲区 apps/runtime 的 force=True 必须让守卫失败"


def test_workflow_library_excluded_from_scan(tmp_path):
    """workflow/ 自身(人工 --force 逃生口)不得纳入自动晋级扫描集。"""
    wf = tmp_path / "workflow"
    wf.mkdir()
    (wf / "promote.py").write_text(
        "from workflow.promote import x\n"  # self-ish import for channel detect
        "promote(h, force=True)\n",
        encoding="utf-8",
    )
    # 即便 import 了通道,顶层 workflow/ 也排除
    (wf / "from_factory.py").write_text(
        "import workflow.promote\n"
        "workflow.promote.promote_pool_l3(force=True)\n",
        encoding="utf-8",
    )
    found = discover_auto_promote_files(tmp_path)
    assert found == []


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
