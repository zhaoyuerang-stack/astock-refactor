#!/usr/bin/env python3
"""因子词表守卫 —— 把"结论端"的免疫模式对称铺到"词表端"(因子层)。

背景(2026-07-12 因子库机制分析):守卫全押在结论端(台账/回测/holdout),
词表端(因子定义/白名单/接线)零守卫,导致口径分叉(两种同名 illiquidity)、
退化因子白烧算力、手工三面接线漂移、死因子沉淀。本守卫补四项机械断言:

C1 手工接线冻结:catalog FACTOR_BUILDERS / DSL _FACTOR_CALLS / autoresearch
   ALLOWED_FACTORS 三面 dict 字面量里的手工条目必须恰好等于下方 LEGACY_* 冻结清单。
   新增手工条目 = FAIL(新因子必须走 @register_factor);已迁移的条目必须同 commit
   从 LEGACY 清单删除(清单只减不增,减到空 = 迁移完成)。
C2 注册表完整性:每条 FactorRecord 必须有非空 definition(口径);searchable=True
   必须带 evidence(probe/research_ledger/方向登记簿指针);不同名字禁止共享同一
   source_hash(复制粘贴重注册 = 同信息算两遍,虚增 n_trials)。
C3 同名冲突:注册名与任一手工字面量撞名 = FAIL(防 registry 与手工条目对同一名字
   给出不同 spec —— holder_count_chg (20,120)vs(40,240) 参数漂移的教训)。
C4 死模块处置:factors/ 下零消费者模块必须在模块 docstring 带
   `Disposition: dormant|probe-pending|deprecated` 标记;反向也查——带标记的模块
   若实际有消费者,标记说谎 = FAIL(R-ARCH-005 精神:被取代/未接线者不得匿名永生)。

与 registry.py 的注册期校验(import 即炸)构成纵深:注册期挡增量,本守卫抓存量与旁路。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ── C1 冻结清单:2026-07-12 存量手工接线(迁移一条删一条,只减不增) ──────────
_LEGACY_ALPHA101 = frozenset(
    f"alpha_{i:03d}" for i in (
        1, 2, 3, 6, 8, 9, 12, 13, 14, 15, 17, 18, 19, 21, 23, 25, 28, 30,
        32, 34, 37, 38, 40, 44, 50, 55))

# 基本面 5 + momentum/illiquidity 已迁;volume_ratio/volatility 仍手工(证据缺口)
_LEGACY_PRICE_FUND = frozenset({
    "volume_ratio", "volatility",
})

# 隔离岛/北向 5 条已迁 @register_factor(searchable=True + direction_registry 证据)
_LEGACY_ISOLATED: frozenset[str] = frozenset()

LEGACY_HANDWIRED = {
    "dsl": _LEGACY_PRICE_FUND | _LEGACY_ALPHA101 | _LEGACY_ISOLATED,
    "whitelist": _LEGACY_PRICE_FUND | _LEGACY_ALPHA101 | _LEGACY_ISOLATED,
    # amihud_illiquidity / small_cap_amount 已迁 @register_factor(searchable=False)
    "catalog": frozenset(),
}

SURFACE_FILES = {
    "dsl": ("factors/autoresearch_dsl.py", "_FACTOR_CALLS"),
    "whitelist": ("factory/autoresearch/registry.py", "ALLOWED_FACTORS"),
    "catalog": ("strategies/catalog.py", "FACTOR_BUILDERS"),
}

# ── C4 处置标记 ───────────────────────────────────────────────────────────
ALLOWED_DISPOSITIONS = ("dormant", "probe-pending", "deprecated")
_DISPOSITION_RE = re.compile(
    r"^Disposition:\s*(dormant|probe-pending|deprecated)\b", re.MULTILINE)
# 消费者扫描排除区:测试/草稿/文档/归档/守卫自身不算"活着"的证据
_SCAN_EXCLUDE = ("tests/", "scratch/", "docs/", "/archive/", "scripts/ci/", ".bak")
# 不参与死活判定的基座模块
_SKIP_MODULES = {"__init__", "registry", "utils"}


def literal_dict_keys(rel_path: str, var_name: str, root: Path | None = None) -> set[str]:
    """AST 解析 <rel_path> 里 `<var_name> = {...}` 字面量的字符串键(手工条目)。

    只看 dict 字面量,不执行代码 —— setdefault 自动接线的条目天然不在其中。
    """
    src = ((root or ROOT) / rel_path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif (isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Dict)
              and isinstance(node.target, ast.Name)):
            names = [node.target.id]
        else:
            continue
        if var_name in names:
            return {k.value for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    raise AssertionError(f"{rel_path} 里找不到 {var_name} 的 dict 字面量赋值——守卫需随重构更新")


def check_handwired_frozen(surfaces: dict[str, set[str]] | None = None) -> list[str]:
    """C1:三面手工条目 == LEGACY 冻结清单(双向)。"""
    errors: list[str] = []
    if surfaces is None:
        surfaces = {k: literal_dict_keys(p, v) for k, (p, v) in SURFACE_FILES.items()}
    for surface, current in surfaces.items():
        legacy = LEGACY_HANDWIRED[surface]
        added = current - legacy
        removed = legacy - current
        if added:
            errors.append(
                f"[C1:{surface}] 新增手工接线条目 {sorted(added)} —— 新因子必须走 "
                f"@register_factor(definition=..., evidence=...),不得再手写三面 dict")
        if removed:
            errors.append(
                f"[C1:{surface}] LEGACY 清单含已不存在的条目 {sorted(removed)} —— "
                f"迁移完成后须同 commit 从本守卫 LEGACY_HANDWIRED 删除(清单只减不增)")
    return errors


def check_registry_integrity(registry: dict | None = None) -> list[str]:
    """C2:definition 非空 / searchable⇒evidence / source_hash 全库唯一。"""
    errors: list[str] = []
    if registry is None:
        sys.path.insert(0, str(ROOT))
        from factors.registry import discover
        registry = discover()
    by_hash: dict[str, str] = {}
    for name, rec in registry.items():
        definition = getattr(rec, "definition", "") or ""
        evidence = getattr(rec, "evidence", "") or ""
        src_hash = getattr(rec, "source_hash", "") or ""
        if not definition.strip():
            errors.append(f"[C2] {name}: definition 为空(口径一句话必填)")
        if getattr(rec, "searchable", False) and not evidence.strip():
            errors.append(f"[C2] {name}: searchable=True 但无 evidence(词表入口证据门)")
        if src_hash:
            prev = by_hash.get(src_hash)
            if prev is not None:
                errors.append(
                    f"[C2] {name} 与 {prev} 共享同一 source_hash({src_hash})——"
                    f"同一实现重复注册 = 同信息算两遍,虚增 n_trials,收敛为一个名字")
            else:
                by_hash[src_hash] = name
    return errors


def check_name_collision(registry: dict | None = None,
                         surfaces: dict[str, set[str]] | None = None) -> list[str]:
    """C3:注册名不得与任何手工字面量撞名(防同名双 spec 漂移)。"""
    errors: list[str] = []
    if registry is None:
        sys.path.insert(0, str(ROOT))
        from factors.registry import discover
        registry = discover()
    if surfaces is None:
        surfaces = {k: literal_dict_keys(p, v) for k, (p, v) in SURFACE_FILES.items()}
    for surface, handwired in surfaces.items():
        overlap = set(registry) & handwired
        if overlap:
            errors.append(
                f"[C3:{surface}] 注册名与手工条目撞名 {sorted(overlap)} —— 同一名字两处 spec "
                f"必漂移(holder_count_chg 参数分叉教训);删手工条目,以 @register_factor 为准")
    return errors


def _module_disposition(text: str) -> str | None:
    m = _DISPOSITION_RE.search(text)
    return m.group(1) if m else None


def _consumer_count(module: str, texts: dict[str, str]) -> int:
    pat = re.compile(
        rf"factors\.{module}\b|from factors import (?:[\w, ]*\b{module}\b)|from factors\.{module}\b")
    return sum(1 for path, text in texts.items()
               if pat.search(text) and not path.endswith(f"factors/{module}.py"))


def check_dispositions(root: Path | None = None) -> list[str]:
    """C4:零消费者模块必须带 Disposition;带 Disposition 的必须真是零消费者。"""
    errors: list[str] = []
    root = root or ROOT
    texts: dict[str, str] = {}
    for p in root.rglob("*.py"):
        rel = p.relative_to(root).as_posix()
        if any(x in f"/{rel}" or rel.startswith(x) for x in _SCAN_EXCLUDE):
            continue
        try:
            texts[rel] = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    factors_dir = root / "factors"
    for p in sorted(factors_dir.glob("*.py")):
        module = p.stem
        if module in _SKIP_MODULES:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        tag = _module_disposition(text)
        consumers = _consumer_count(module, texts)
        if consumers == 0 and tag is None:
            errors.append(
                f"[C4] factors/{module}.py 零消费者且无 Disposition 标记 —— 在模块 docstring "
                f"加 `Disposition: dormant|probe-pending|deprecated — 理由`(匿名沉淀禁止)")
        elif consumers > 0 and tag is not None:
            errors.append(
                f"[C4] factors/{module}.py 标 Disposition: {tag} 但有 {consumers} 个消费者 —— "
                f"标记说谎:要么摘标记,要么先摘消费者")
    return errors


def check(root: Path | None = None) -> int:
    errors: list[str] = []
    errors += check_handwired_frozen()
    errors += check_registry_integrity()
    errors += check_name_collision()
    errors += check_dispositions(root)
    if errors:
        print("check_factor_registry: FAIL")
        for e in errors:
            print("  " + e)
        return 1
    n_legacy = sum(len(v) for v in LEGACY_HANDWIRED.values())
    print(f"check_factor_registry: OK(legacy 手工条目余 {n_legacy},迁移目标 0)")
    return 0


if __name__ == "__main__":
    sys.exit(check())
