"""文档地图守卫:根目录活文档白名单 + 零悬空引用 + STATUS 行数上限。

缘由(ADR-041):ADR-019(2026-06-21)手工把根目录 16→14 份"职责唯一活文档",
验收标准是"全仓 md 链接零断链"。一个月后复查:根目录回到 16 份、3 份孤儿文档
(ROADMAP/BRANCH_REPORT/推荐历史)不在地图、`CLAUDE.md` 地图列的
`LOOP_QUICK_CALLS.md` 全仓不存在、`STATUS.md` 涨到 847 行且底部仍宣称一个
台账里根本不存在的 LIVE 策略(+37.8%)。结论:文档熵增靠纪律守不住,必须机械强制。

三条断言:
  A. 根目录 *.md 集合 == CLAUDE.md §2 文档地图登记集合(禁孤儿、禁地图悬空)
  B. 已跟踪文档的 markdown 链接 ](*.md) 零悬空 + §2 地图表内带路径引用可寻址
  C. STATUS.md ≤ MAX_STATUS_LINES 行(强制逐日条目外溢到 docs/archive/)

范围只取 **git 跟踪的** .md:未跟踪文件是本地私有(如 `.agents/AGENTS.md` 已被
`git_hygiene_audit.py` 归类 local_ignore)或他人在途半成品,不该由本守卫执法 ——
共享工作树下这也避免卷入别人的改动。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MAX_STATUS_LINES = 200

MD_LINK = re.compile(r"\]\(([^)]+?\.md)(?:#[^)]*)?\)")
BACKTICK_MD = re.compile(r"`([A-Za-z0-9_./-]+\.md)`")


def iter_docs(root: Path):
    """git 跟踪的 .md,排开并行 worktree 快照。"""
    out = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=root, capture_output=True, text=True, check=True
    ).stdout
    for line in out.splitlines():
        if line.startswith(".claude/worktrees/"):
            continue
        path = root / line
        if path.exists():
            yield path


def _map_cells(root: Path):
    """§2 文档地图表格第二列(文档名列)的原文,逐行 yield。"""
    text = (root / "CLAUDE.md").read_text(encoding="utf-8")
    section = text.split("## 2. 文档地图", 1)[-1].split("\n## ", 1)[0]
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) >= 3:
            yield cells[2]


def parse_doc_map(root: Path) -> set[str]:
    """地图里不含路径分隔符的 .md = 根目录活文档白名单。"""
    return {
        m.group(1)
        for cell in _map_cells(root)
        for m in BACKTICK_MD.finditer(cell)
        if "/" not in m.group(1)
    }


def check_root_whitelist(root: Path) -> list[str]:
    failures = []
    declared = parse_doc_map(root)
    actual = {p.name for p in root.glob("*.md")}
    for orphan in sorted(actual - declared):
        failures.append(
            f"[A] 根目录文档 {orphan} 未登记进 CLAUDE.md §2 文档地图 "
            f"(要么登记,要么移出根目录到 docs/archive|reports|factor_research/)"
        )
    for ghost in sorted(declared - actual):
        failures.append(
            f"[A] CLAUDE.md §2 地图登记了 {ghost},但根目录不存在该文件(悬空指针)"
        )
    return failures


def check_no_dangling(root: Path) -> list[str]:
    failures = []
    for path in iter_docs(root):
        rel = path.relative_to(root)
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in MD_LINK.finditer(text):
            target = match.group(1)
            if target.startswith(("http://", "https://")):
                continue
            if not (path.parent / target).resolve().exists():
                failures.append(f"[B] {rel} 的链接 ]({target}) 指向不存在的文件")

    # 反引号裸引用只在 §2 地图表内执法 —— 那里 100% 是指针,不会与正文的
    # 叙述性提及(如 ADR-019 回顾历史命名碰撞 `Task.md`)混淆,故无需例外集。
    for cell in _map_cells(root):
        for match in BACKTICK_MD.finditer(cell):
            target = match.group(1)
            if "/" in target and not (root / target).exists():
                failures.append(f"[B] CLAUDE.md §2 地图登记了 `{target}`,但该路径不存在")
    return failures


def check_status_size(root: Path) -> list[str]:
    lines = len((root / "STATUS.md").read_text(encoding="utf-8").splitlines())
    if lines <= MAX_STATUS_LINES:
        return []
    return [
        f"[C] STATUS.md {lines} 行 > 上限 {MAX_STATUS_LINES}。"
        f"STATUS.md 只写当前状态;逐日条目请 append 到 docs/archive/ 后从本文移除"
        f"(旧绩效数字归档后不得再作证据,见 R-EVIDENCE-001)"
    ]


def run_all(root: Path = ROOT) -> list[str]:
    return check_root_whitelist(root) + check_no_dangling(root) + check_status_size(root)


def main(root: Path = ROOT) -> int:
    failures = run_all(root)
    if failures:
        print("doc map guard failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("doc map guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
