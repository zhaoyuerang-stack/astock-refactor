"""Guard: amount reconstruction must use share × raw_close (no hands ×100).

Canonical data-lake units (``lake.units.PriceUnitContract``):
  volume = share, amount = CNY, raw_close = CNY/share
  amount = volume * raw_close  via ``lake.units.implied_amount``

Tushare already converts 手→股 at ingest (``vol * 100`` into volume). Multiplying
again by 100 when rebuilding amount inflates liquidity by ~100x and pollutes
Amihud / small-cap / capacity rankings.

This guard scans formal research/runtime trees for assignments that rebuild
``amount`` with a literal 100 factor and a volume-like operand.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Paths that reconstruct amount for factor/search/backtest. Ingest converters
# (lake/sources, scripts/repair) may still multiply vol by 100 when writing shares.
PROTECTED_ROOTS = (
    "strategies",
    "portfolio",
    "workflow",
    "factory",
    "services/actions",
    "factors",
    "apps",
    "metasearch",
    "core",
)


def _name_hint(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id.lower()
    if isinstance(node, ast.Attribute):
        return node.attr.lower()
    if isinstance(node, ast.Subscript):
        return _name_hint(node.value) + _name_hint(node.slice)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.lower()
    return ""


def _collect_mult_factors(node: ast.AST) -> list[ast.AST]:
    """Flatten left-associated and nested Mult nodes into leaf factors."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return _collect_mult_factors(node.left) + _collect_mult_factors(node.right)
    return [node]


def _is_literal_100(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and float(node.value) == 100.0


def _looks_volume(node: ast.AST) -> bool:
    hint = _name_hint(node)
    return "volume" in hint or hint in {"vol", "v"}


def _looks_price(node: ast.AST) -> bool:
    hint = _name_hint(node)
    return any(k in hint for k in ("raw", "close", "price", "open"))


def _targets_amount(targets: list[ast.AST]) -> bool:
    for target in targets:
        if isinstance(target, ast.Name) and target.id == "amount":
            return True
        if isinstance(target, (ast.Tuple, ast.List)):
            if any(isinstance(elt, ast.Name) and elt.id == "amount" for elt in target.elts):
                return True
        if isinstance(target, ast.Attribute) and target.attr == "amount":
            return True
        if isinstance(target, ast.Subscript) and "amount" in _name_hint(target):
            return True
    return False


def scan_source(src: str, *, rel: str = "") -> list[str]:
    """Return amount unit violations in one Python source."""
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [f"{rel}: syntax error prevents amount-unit audit: {exc}"]

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if isinstance(node, ast.Assign):
            if not _targets_amount(node.targets):
                continue
            value = node.value
        else:
            if node.value is None or not _targets_amount([node.target]):
                continue
            value = node.value

        factors = _collect_mult_factors(value)
        has_100 = any(_is_literal_100(f) for f in factors)
        has_volume = any(_looks_volume(f) for f in factors)
        has_price = any(_looks_price(f) for f in factors)
        if has_100 and has_volume and has_price:
            violations.append(
                f"{rel}:L{node.lineno} rebuilds amount with volume×100×price; "
                "canonical is lake.units.implied_amount(volume, raw_close) "
                "(volume already in shares)"
            )
    return violations


def _protected_files() -> list[Path]:
    files: list[Path] = []
    for root in PROTECTED_ROOTS:
        base = ROOT / root
        if base.is_file():
            files.append(base)
        elif base.exists():
            files.extend(base.rglob("*.py"))
    return sorted(
        p for p in files
        if "__pycache__" not in p.parts and "archive" not in p.parts
    )


def main() -> int:
    violations: list[str] = []
    files = _protected_files()
    for path in files:
        rel = str(path.relative_to(ROOT))
        violations.extend(scan_source(path.read_text(encoding="utf-8"), rel=rel))
    if violations:
        print("amount unit violations (volume already shares; do not ×100):")
        for violation in violations:
            print(f"  - {violation}")
        return 1
    print(f"amount unit guard passed ({len(files)} formal Python files scanned).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
