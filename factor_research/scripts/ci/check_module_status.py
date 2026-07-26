"""Ensure every top-level module has parseable MODULE_STATUS.md."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = ["# MODULE_STATUS", "Status:", "Role:"]


def is_governed_module_dir(path: Path) -> bool:
    """Return whether a directory is source-owned and needs a status contract."""
    return (
        path.is_dir()
        and not path.name.startswith(".")
        and path.name != "__pycache__"
        and not path.name.endswith(".egg-info")
    )


def main() -> int:
    failures = []
    for module_dir in sorted(ROOT.iterdir(), key=lambda p: p.name):
        if not is_governed_module_dir(module_dir):
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
