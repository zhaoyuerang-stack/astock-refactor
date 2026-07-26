"""R-DATA-001 守卫：禁止代码使用 data_full 旧口径作为数据源（防"喂出来的高收益"被当真）。

正式研究/入册/汇报必须走 data_lake + core/ 统一口径。历史脚本可在迁移/对照/废弃验证里引用
data_full，但**正式代码不得 import data_full 或从 data_full 目录加载数据**。

两道机械门（AST，零误报于现有合法提及）：
  L1 import：任何 `import data_full` / `from data_full[...] import` = 违规。
  L2 路径加载：非 docstring 的字符串字面量含路径段 `data_full/` 或 `/data_full` = 违规
     （疑似从 data_full 目录读盘）。

刻意放过（合法，不报）：
  · 注释（# …）—— 不进 AST。
  · 模块/类/函数 docstring —— 显式排除（口径说明常写 "data_full/data_lake 是版本属性…"）。
  · 裸标签字符串 "data_full"（无斜杠）—— 如 data_scope={"source":"data_full"} 是历史口径声明，
    诚实记录某版本用过旧口径，不是新加载，不该禁。
  · 白名单目录（migration/archive/tests/deprecated/.bak/本守卫自身）。

只读源码，违规则 exit 1。检测函数吃 (path, source)，便于 fixture 测试。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # factor_research/
PATH_SEG = re.compile(r"/data_full|data_full/")       # 路径段：前或后带斜杠才算"目录引用"

# 允许引用 data_full 的目录/文件（迁移、对照、废弃验证、测试、本守卫）。
ALLOWED_SUBSTR = (
    "/scripts/migration/", "/scripts/archive/", "/archive/",
    "/tests/", "/_deprecated", ".bak",
    "/scripts/ci/check_no_legacy_data.py",
)


def _is_allowed(path: Path) -> bool:
    p = "/" + str(path).replace("\\", "/").lstrip("/")
    name = path.name
    return any(s in p for s in ALLOWED_SUBSTR) or name.startswith("test_")


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    """收集 module/class/function 的 docstring 字符串节点 id，用于排除。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def scan_source(path: Path, source: str) -> list[str]:
    """返回该文件的违规信息列表（空=干净）。语法错误的文件跳过（交给别的检查）。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[str] = []
    rel = path.relative_to(ROOT) if path.is_absolute() and str(path).startswith(str(ROOT)) else path
    doc_ids = _docstring_node_ids(tree)
    for node in ast.walk(tree):
        # L1 import data_full
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "data_full" or a.name.startswith("data_full."):
                    out.append(f"[R-DATA-001 import] {rel}:{node.lineno} import {a.name} — 禁用旧口径模块")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "data_full" or mod.startswith("data_full."):
                out.append(f"[R-DATA-001 import] {rel}:{node.lineno} from {mod} import … — 禁用旧口径模块")
        # L2 路径加载（非 docstring 字符串字面量）
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in doc_ids:
            if PATH_SEG.search(node.value):
                out.append(f"[R-DATA-001 路径] {rel}:{node.lineno} 字符串含 data_full 路径段 "
                           f"{node.value!r} — 疑似从旧口径目录读盘，请改 data_lake")
    return out


def check(root: Path | None = None) -> int:
    root = root or ROOT
    violations: list[str] = []
    for py in sorted(root.rglob("*.py")):
        if _is_allowed(py):
            continue
        try:
            src = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        violations.extend(scan_source(py, src))
    if violations:
        print("R-DATA-001 旧口径检查发现违规：")
        for v in violations:
            print(f"  {v}")
        return 1
    print("R-DATA-001 旧口径检查通过（无代码 import/加载 data_full）。")
    return 0


if __name__ == "__main__":
    sys.exit(check())
