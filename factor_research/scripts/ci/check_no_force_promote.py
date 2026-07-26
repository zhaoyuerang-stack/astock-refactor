"""防 force-promote 回归守卫(根因分析 #1 / ADR-017)。

历史:`bulk_promote.py` 曾 force=True + run_marginal=False 把 9-Gate REJECTED 的候选强制入册。
本守卫 AST 扫**自动晋级脚本**,禁止其中出现:
  - 任意调用的 `force=True`(跳过 phase1/2 防未来 + 图谱门)
  - `run_marginal=False`(跳过边际残差去冗余)

扫描集 = **仓库级**(守卫审计 #8,2026-07-17):所有 import 了 workflow.promote /
workflow.from_factory 的 .py——新增自动 promoter 自动纳入,不再靠人工目录名单。
排除:`workflow/` 自身(library 层,`--force` 是人工 CLI 逃生口)、`tests/`、`__pycache__`。
注意:library 层 `workflow/promote.py` 的 `--force` 是**人工逃生口**(CLI,带警告),不在扫描集——
自动脚本绝不能用,人工单次可用。

只读 AST,违规则 exit 1。检测函数吃源码字符串/可注入 root,便于 fixture 测试。
存量命中进 PENDING_REMEDIATION(响而不阻),新增即红。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 判定"这是个自动晋级脚本"的 import 目标前缀
PROMOTE_IMPORT_PREFIXES = ("workflow.promote", "workflow.from_factory")
# 排除:library 层 workflow/、tests/、缓存
EXCLUDE_TOP_DIRS = frozenset({"workflow", "tests", "__pycache__", ".pytest_cache", "data_lake"})

# 存量欠债(守卫审计 #8 扩面后扫出)。响而不阻;修复后须从此处移除。
PENDING_REMEDIATION: dict[str, str] = {
    # 主仓预演(2026-07-17)发现:untracked scratch 遗留(06-28 另类流冠军注册会话),
    # worktree 只物化跟踪文件故批量修补时不可见。建议 owner 删除或归档后移除本条。
    "scratch/register_and_promote_champion.py:force=True":
        "untracked scratch 遗留脚本含 force=True;应删除/归档,勿再执行",
}


def _imports_promote_channel(tree: ast.AST) -> bool:
    """AST 判定:文件是否 import 了晋级通道(workflow.promote / workflow.from_factory)。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PROMOTE_IMPORT_PREFIXES):
                    return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.startswith(PROMOTE_IMPORT_PREFIXES):
                return True
            if mod == "workflow" and any(
                a.name in ("promote", "from_factory") for a in node.names
            ):
                return True
    return False


def _should_scan(rel: Path) -> bool:
    """仓库级扫描,排除 workflow/ tests/ 缓存与数据湖。"""
    parts = rel.parts
    if not parts:
        return False
    if parts[0] in EXCLUDE_TOP_DIRS:
        return False
    if any(p in ("__pycache__", ".pytest_cache", "tests") for p in parts):
        return False
    return True


def discover_auto_promote_files(root: Path | None = None) -> list[Path]:
    """仓库级扫描:所有 import 了晋级通道的 .py(排除 workflow/ library、tests/)。"""
    base = root or ROOT
    out: list[Path] = []
    for p in sorted(base.rglob("*.py")):
        try:
            rel = p.relative_to(base)
        except ValueError:
            continue
        if not _should_scan(rel):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        if _imports_promote_channel(tree):
            out.append(p)
    return out


def scan_source(src: str, label: str = "") -> list[str]:
    """AST 扫源码:返回 [violation msg]。检测调用里的 force=True / run_marginal=False。"""
    out = []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"[{label}] 语法错误,无法扫描: {e}"]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "force" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                out.append(f"[{label}:L{kw.value.lineno}] 调用含 force=True —— 自动晋级禁用强制入册(绕过 phase1/2 防未来门)")
            if kw.arg == "run_marginal" and isinstance(kw.value, ast.Constant) and kw.value.value is False:
                out.append(f"[{label}:L{kw.value.lineno}] 调用含 run_marginal=False —— 自动晋级禁用跳过边际残差去冗余")
    return out


def _violation_key(msg: str) -> str:
    """从扫描消息提取 PENDING 键: relpath:force=True|run_marginal=False。"""
    # msg 形如 "[rel:L54] 调用含 force=True —— ..."
    if not msg.startswith("["):
        return msg
    bracket = msg.split("]", 1)[0][1:]  # rel:L54
    rel = bracket.rsplit(":L", 1)[0]
    if "force=True" in msg:
        return f"{rel}:force=True"
    if "run_marginal=False" in msg:
        return f"{rel}:run_marginal=False"
    return msg


def check(root: Path | None = None) -> int:
    base = root or ROOT
    files = discover_auto_promote_files(base)
    raw: list[str] = []
    for p in files:
        rel = str(p.relative_to(base))
        raw += scan_source(p.read_text(encoding="utf-8"), label=rel)

    new_v = []
    pending = []
    for msg in raw:
        key = _violation_key(msg)
        if key in PENDING_REMEDIATION:
            pending.append((key, msg))
        else:
            new_v.append(msg)

    for key, msg in pending:
        print(f"  ⚠️ 待处置(基线): {msg} — {PENDING_REMEDIATION[key]}")

    if new_v:
        print("发现 force-promote 橡皮图章违规:")
        for v in new_v:
            print(f"  {v}")
        return 1
    print(
        f"force-promote 守卫通过:{len(files)} 个自动晋级脚本(仓库级扫描)"
        f"无新增 force=True / run_marginal=False"
        f"({len(pending)} 项待处置已基线)。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(check())
