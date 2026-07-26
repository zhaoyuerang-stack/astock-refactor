"""数据湖唯一写入口守卫——湖区只允许数据层模块写入。

背景(2026-06-12 事故):ad-hoc 修复脚本直写 daily_all 且不更新 manifest,
造成数据与台账失联;类比策略侧"台账唯一写入口 = strategy_registry"铁律,
数据侧同样需要:**写 data_lake 任意子目录的代码必须住在 lake/ 或
scripts/data/(含 scripts/repair/ 修复工具)**。

静态检查(ADR-038 决策一+三):
  - 写动词:{to_parquet, to_csv, to_pickle, write_table}(方法或 Name/Attribute 调用)
  - 仅当写调用的**实参路径表达式**可追溯到 data_lake 引用才判违规
    (字符串字面量,或经简单赋值传播的变量——照 check_layer_deps
    registry_write_violations 变量绑定范式 + 固定点传播)
  - **scoped 区**(决策三):`factor_store/` 前缀模块仅可写
    `data_lake/factor_store/` 子树;写其他湖区照禁。其他模块写
    `data_lake/factor_store/` 照禁(与写 price/ 等同等违规)。
  - 文件枚举:磁盘 rglob;排除 data_lake/、__pycache__、.pytest_cache;
    tests/ 与 scripts/ci/ 豁免;ALLOWED_PREFIXES 内放行
  - 哲学:防的是**写湖**,不是提及湖(读湖 + to_csv 到 reports/ 必绿)

存量命中进 PENDING_REMEDIATION(响而不阻),新增即红。
ADR-038 落地后 PENDING 清零(真写归 scoped / canonical)。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ALLOWED_PREFIXES = ("lake/", "scripts/data/", "scripts/repair/", "tests/", "scripts/ci/")
# ADR-038 决策三:仅这些前缀可写 data_lake/factor_store/ 子树
FACTOR_STORE_MODULE_PREFIX = "factor_store/"
FACTOR_STORE_LAKE_ZONE = "data_lake/factor_store"

WRITE_METHODS = frozenset({"to_parquet", "to_csv", "to_pickle", "write_table"})
WRITE_FUNCS = frozenset({"write_table"})

# ADR-038 落地后清零;保留空表以兼容测试/主流程的 pending 分支。
PENDING_REMEDIATION: dict[str, str] = {}

# 写目标分区
ZONE_FACTOR_STORE = "factor_store"
ZONE_OTHER_LAKE = "other_lake"


def _is_exempt(rel: str) -> bool:
    rel = rel.replace("\\", "/")
    if rel.startswith(ALLOWED_PREFIXES):
        return True
    if "/tests/" in f"/{rel}" or rel.startswith("tests/"):
        return True
    return False


def _is_factor_store_module(rel: str) -> bool:
    return rel.replace("\\", "/").startswith(FACTOR_STORE_MODULE_PREFIX)


def iter_py_files(root: Path) -> list[Path]:
    """磁盘 rglob 全部 .py,排除 data_lake/、缓存目录。"""
    out: list[Path] = []
    for p in sorted(root.rglob("*.py")):
        if any(part in {"data_lake", "__pycache__", ".pytest_cache"} for part in p.parts):
            continue
        out.append(p)
    return out


def _str_mentions_lake(value: object) -> bool:
    """字符串是否含 data_lake 路径引用(字面量 data_lake/... 或组件 'data_lake')。"""
    if not isinstance(value, str):
        return False
    return "data_lake/" in value or value == "data_lake" or "/data_lake/" in value


def _str_is_factor_store_zone(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return FACTOR_STORE_LAKE_ZONE in value or value == "factor_store"


def _node_mentions_lake_literal(node: ast.AST | None) -> bool:
    if node is None:
        return False
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and _str_mentions_lake(n.value):
            return True
    return False


def _expr_refs_lake(node: ast.AST | None, lake_vars: set[str]) -> bool:
    """路径表达式是否可追溯到 data_lake(字面量或已绑定变量)。"""
    if node is None:
        return False
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and _str_mentions_lake(n.value):
            return True
        if isinstance(n, ast.Name) and n.id in lake_vars:
            return True
    return False


def _path_zone(node: ast.AST | None, lake_vars: set[str], fs_vars: set[str]) -> str | None:
    """对可追溯到湖的路径表达式,返回 ZONE_FACTOR_STORE / ZONE_OTHER_LAKE / None。"""
    if node is None or not _expr_refs_lake(node, lake_vars):
        return None
    # 直接字面量或已绑定 fs 变量
    strs: list[str] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            strs.append(n.value)
            if _str_is_factor_store_zone(n.value):
                return ZONE_FACTOR_STORE
        if isinstance(n, ast.Name) and n.id in fs_vars:
            return ZONE_FACTOR_STORE
    # Path 组件式:同一表达式内同时出现 'data_lake' 与 'factor_store'
    if "data_lake" in strs and "factor_store" in strs:
        return ZONE_FACTOR_STORE
    if any(FACTOR_STORE_LAKE_ZONE in s for s in strs):
        return ZONE_FACTOR_STORE
    return ZONE_OTHER_LAKE


def _assigned_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names: list[str] = []
    for tgt in targets:
        if isinstance(tgt, ast.Name):
            names.append(tgt.id)
    return names


def _collect_lake_bindings(tree: ast.AST) -> tuple[set[str], set[str]]:
    """简单赋值传播 → (lake_vars, factor_store_vars)。"""
    assignments: list[ast.Assign | ast.AnnAssign] = [
        n for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign))
    ]
    lake_vars: set[str] = set()
    fs_vars: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in assignments:
            value = node.value
            if value is None:
                continue
            if not (_expr_refs_lake(value, lake_vars) or _node_mentions_lake_literal(value)):
                continue
            zone = _path_zone(value, lake_vars, fs_vars)
            # 若 _path_zone 因 lake_vars 尚未含自身而返回 other,再用字面量重判
            if zone is None:
                strs = [
                    n.value
                    for n in ast.walk(value)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)
                ]
                if any(_str_is_factor_store_zone(s) for s in strs) or (
                    "data_lake" in strs and "factor_store" in strs
                ):
                    zone = ZONE_FACTOR_STORE
                elif any(_str_mentions_lake(s) for s in strs) or _expr_refs_lake(value, lake_vars):
                    zone = ZONE_OTHER_LAKE
            for name in _assigned_names(node):
                if name not in lake_vars:
                    lake_vars.add(name)
                    changed = True
                if zone == ZONE_FACTOR_STORE and name not in fs_vars:
                    fs_vars.add(name)
                    changed = True
    return lake_vars, fs_vars


def _call_write_path_args(node: ast.Call) -> list[ast.AST]:
    """提取写调用中可能承载路径的实参(含关键字)。"""
    args: list[ast.AST] = []
    func = node.func
    is_method = isinstance(func, ast.Attribute) and func.attr in WRITE_METHODS
    is_func = (
        (isinstance(func, ast.Name) and func.id in WRITE_FUNCS)
        or (isinstance(func, ast.Attribute) and func.attr in WRITE_FUNCS)
    )
    if not (is_method or is_func):
        return args
    # to_*(path) 首参即路径;write_table(table, where) 第二参起为路径
    if isinstance(func, ast.Attribute) and func.attr == "write_table":
        args.extend(node.args[1:] if len(node.args) > 1 else [])
    elif isinstance(func, ast.Name) and func.id == "write_table":
        args.extend(node.args[1:] if len(node.args) > 1 else [])
    else:
        args.extend(node.args)
    for kw in node.keywords:
        if kw.arg in {
            None,
            "path",
            "path_or_buf",
            "filepath_or_buffer",
            "where",
            "fname",
        } or kw.arg is None:
            args.append(kw.value)
        elif is_method or is_func:
            args.append(kw.value)
    return args


def source_lake_write_zones(src: str) -> list[str]:
    """AST:返回写调用命中的湖区列表(ZONE_FACTOR_STORE / ZONE_OTHER_LAKE)。"""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    lake_vars, fs_vars = _collect_lake_bindings(tree)
    zones: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for arg in _call_write_path_args(node):
            zone = _path_zone(arg, lake_vars, fs_vars)
            if zone is not None:
                zones.append(zone)
    return zones


def source_has_lake_write(src: str) -> bool:
    """任一写调用实参可追溯到 data_lake → True(不论分区)。"""
    return bool(source_lake_write_zones(src))


def source_is_violation_for_module(src: str, rel: str) -> bool:
    """结合模块身份与 scoped 规则,判断是否违规。"""
    zones = source_lake_write_zones(src)
    if not zones:
        return False
    if _is_factor_store_module(rel):
        # factor_store 模块只禁写非 factor_store 湖区
        return any(z == ZONE_OTHER_LAKE for z in zones)
    # 其他模块:写任何湖区(含 factor_store 子树)均违规
    return True


# 向后兼容:默认按「非 factor_store 模块」判定(测试里的裸源码片段)
def file_is_violation(text: str) -> bool:
    return source_is_violation_for_module(text, "apps/anonymous.py")


def scan(root: Path | None = None) -> list[str]:
    """返回全部违规相对路径(含 PENDING 候选),供测试断言。"""
    base = root or ROOT
    hits: list[str] = []
    for p in iter_py_files(base):
        try:
            rel = str(p.relative_to(base)).replace("\\", "/")
        except ValueError:
            continue
        if _is_exempt(rel):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if source_is_violation_for_module(text, rel):
            hits.append(rel)
    return hits


def main(root: Path | None = None) -> int:
    base = root or ROOT
    files = iter_py_files(base)
    raw_hits = scan(base)

    new_v = [h for h in raw_hits if h not in PENDING_REMEDIATION]
    pending = [h for h in raw_hits if h in PENDING_REMEDIATION]
    no_longer = [k for k in PENDING_REMEDIATION if k not in raw_hits]

    for h in pending:
        print(f"  ⚠️ 待处置(基线): {h} — {PENDING_REMEDIATION[h]}")
    for k in no_longer:
        print(f"  ℹ️ 基线项已修复或不再命中,请从 PENDING_REMEDIATION 移除: {k}")

    if new_v:
        print(
            "🚨 数据湖唯一写入口违规"
            "(写湖须 lake/|scripts/data/;factor_store 仅可写 data_lake/factor_store/):"
        )
        for v in new_v:
            print(f"  - {v}")
        return 1
    print(
        f"数据湖写入口检查通过({len(files)} 个文件扫描,"
        f"{len(pending)} 项待处置已基线)。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
