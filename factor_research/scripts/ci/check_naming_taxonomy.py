"""Guard against new ambiguous ontology names.

This guard is intentionally conservative: it allows known compatibility wrappers
but blocks new modules with ambiguous names that already caused confusion.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ALLOWED_AMBIGUOUS_FILES = {
    "engine/composer.py",
    "portfolio/composer.py",
    "factors/veto.py",
}

FORBIDDEN_NEW_BASENAMES = {
    "composer.py": "Use factor_composer.py or portfolio_composer.py.",
    "veto.py": "Use policy/candidate_filters.py or factors/illiquidity_components.py.",
    "filter.py": "Use a domain-specific name such as candidate_filters.py.",
}


def main() -> int:
    failures = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if any(part in {"__pycache__", ".pytest_cache", ".ruff_cache", "scratch"} for part in path.parts):
            continue
        reason = FORBIDDEN_NEW_BASENAMES.get(path.name)
        if reason and rel not in ALLOWED_AMBIGUOUS_FILES:
            failures.append(f"{rel}: {reason}")

    if failures:
        print("Naming taxonomy guard failed:")
        for item in failures:
            print(f"  - {item}")
        return 1

    print("Naming taxonomy guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
