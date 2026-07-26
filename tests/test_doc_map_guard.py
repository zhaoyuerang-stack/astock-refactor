"""check_doc_map 守卫对抗回归(文档熵增机械强制)。

对抗性验收:每条断言先在**迷你仓库**里造出 2026-07-25 那次收敛前的真实病态
(孤儿文档 / 地图悬空指针 / 断链 / STATUS 涨到 847 行),证明守卫真的拒;
再证明健康仓库通过、真实仓库通过。守卫用 `git ls-files` 定范围,故 fixture
必须是真 git 仓库。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "factor_research"))

from scripts.ci import check_doc_map as guard  # noqa: E402

CONSTITUTION = """# CLAUDE.md

## 2. 文档地图

| 层级 | 文档 | 作用 |
| --- | --- | --- |
| 入口 | `CLAUDE.md` | 宪法 |
| 状态 | `STATUS.md` | 当前进度 |
| 数据 | `factor_research/docs/data_infrastructure.md` | 数据源 |

## 3. 下一节
"""


def _init_repo(tmp_path: Path) -> Path:
    """造一个健康的迷你仓库:地图 3 条,文件齐全,STATUS 短。"""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "CLAUDE.md").write_text(CONSTITUTION, encoding="utf-8")
    (tmp_path / "STATUS.md").write_text("# STATUS\n\n当前在册 = 0\n", encoding="utf-8")
    deep = tmp_path / "factor_research" / "docs"
    deep.mkdir(parents=True)
    (deep / "data_infrastructure.md").write_text("# 数据\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def test_healthy_repo_passes(tmp_path):
    """健康仓库必须绿 —— 否则守卫是噪声,会被忽略(ADR-019 的下场)。"""
    repo = _init_repo(tmp_path)
    assert guard.run_all(repo) == []
    assert guard.main(repo) == 0


def test_orphan_root_doc_fails(tmp_path):
    """孤儿根文档(真实病灶:ROADMAP/BRANCH_REPORT/推荐历史)必须红。"""
    repo = _init_repo(tmp_path)
    (repo / "ROADMAP.md").write_text("# 未登记进地图的孤儿\n", encoding="utf-8")

    failures = guard.run_all(repo)
    assert failures, "根目录多出未登记文档必须被拒"
    assert any("ROADMAP.md" in f and "[A]" in f for f in failures)
    assert guard.main(repo) == 1


def test_map_pointing_at_missing_file_fails(tmp_path):
    """地图列了不存在的文件(真实病灶:LOOP_QUICK_CALLS.md)必须红。"""
    repo = _init_repo(tmp_path)
    text = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    text = text.replace(
        "| 状态 | `STATUS.md` | 当前进度 |",
        "| 状态 | `STATUS.md` | 当前进度 |\n| 速查 | `LOOP_QUICK_CALLS.md` | 不存在 |",
    )
    (repo / "CLAUDE.md").write_text(text, encoding="utf-8")

    failures = guard.run_all(repo)
    assert any("LOOP_QUICK_CALLS.md" in f and "悬空" in f for f in failures)


def test_map_pointing_at_missing_nested_path_fails(tmp_path):
    """地图里**带路径**的引用失效同样必须红(不只管根级)。"""
    repo = _init_repo(tmp_path)
    (repo / "factor_research" / "docs" / "data_infrastructure.md").unlink()
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)

    failures = guard.run_all(repo)
    assert any("data_infrastructure.md" in f and "[B]" in f for f in failures)


def test_dangling_markdown_link_fails(tmp_path):
    """已跟踪文档里的断链必须红。"""
    repo = _init_repo(tmp_path)
    (repo / "STATUS.md").write_text(
        "# STATUS\n\n详见 [归档](docs/archive/nope.md)\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)

    failures = guard.run_all(repo)
    assert any("nope.md" in f and "[B]" in f for f in failures)


def test_oversized_status_fails(tmp_path):
    """STATUS.md 重新涨成日志(收敛前 847 行)必须红。"""
    repo = _init_repo(tmp_path)
    bloat = "\n".join(f"**2026-06-{i % 28 + 1:02d}**: 又一条逐日流水" for i in range(847))
    (repo / "STATUS.md").write_text(bloat, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)

    failures = guard.run_all(repo)
    assert any("[C]" in f and "847" in f for f in failures)


def test_untracked_files_are_not_policed(tmp_path):
    """未跟踪文件(本地私有 / 他人在途)不该被执法 —— 共享工作树纪律。"""
    repo = _init_repo(tmp_path)
    local = repo / ".agents"
    local.mkdir()
    (local / "AGENTS.md").write_text("链接全断 [x](nope.md) [y](gone.md)\n", encoding="utf-8")

    assert guard.run_all(repo) == [], "未 git add 的本地文件不应触发守卫"


def test_real_repo_passes():
    """真实仓库当前必须绿(收敛后基线)。"""
    assert guard.run_all(ROOT) == []


@pytest.mark.parametrize(
    "check", [guard.check_root_whitelist, guard.check_no_dangling, guard.check_status_size]
)
def test_each_check_is_injectable(check, tmp_path):
    """三条断言都必须能对注入的 root 独立执法(可测 = 可信)。"""
    assert check(_init_repo(tmp_path)) == []
