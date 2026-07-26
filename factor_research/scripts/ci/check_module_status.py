"""Ensure every top-level module has parseable MODULE_STATUS.md."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = ["# MODULE_STATUS", "Status:", "Role:"]


def main() -> int:
    failures = []
    for module_dir in sorted(ROOT.iterdir(), key=lambda p: p.name):
        if not module_dir.is_dir() or module_dir.name.startswith(".") or module_dir.name == "__pycache__":
            continue
        path = module_dir / "MODULE_STATUS.md"
        if not path.exists():
            failures.append(f"{module_dir.name}: missing MODULE_STATUS.md")
            continue
        text = path.read_text(encoding="utf-8")
        for marker in REQUIRED:
            if marker not in text:
                failures.append(f"{module_dir.name}: missing marker {marker}")

    if failures:
        print("MODULE_STATUS guard failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("MODULE_STATUS guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
