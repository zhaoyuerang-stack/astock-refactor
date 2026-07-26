"""Read top-level MODULE_STATUS.md files as structured agent inventory."""
from __future__ import annotations

from pathlib import Path

from contracts.agent_control import ModuleInventoryItem

ROOT = Path(__file__).resolve().parents[2]


def _section_value(lines: list[str], prefix: str) -> str:
    for line in lines:
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


def _boundary_lines(lines: list[str]) -> list[str]:
    boundary = []
    in_boundary = False
    for line in lines:
        stripped = line.strip()
        if stripped == "Boundary:":
            in_boundary = True
            continue
        if in_boundary:
            if stripped.startswith("- "):
                boundary.append(stripped[2:])
            elif stripped and not stripped.startswith("- "):
                break
    return boundary


def _read_status_file(module_dir: Path) -> ModuleInventoryItem:
    status_path = module_dir / "MODULE_STATUS.md"
    lines = status_path.read_text(encoding="utf-8").splitlines()
    status = _section_value(lines, "Status")
    role = _section_value(lines, "Role")
    keep_reason = _section_value(lines, "Keep because") or _section_value(lines, "Keep for now because")
    if not keep_reason:
        keep_reason = _section_value(lines, "Current issue") or _section_value(lines, "Decision")
    return ModuleInventoryItem(
        module=module_dir.name,
        path=str(module_dir.relative_to(ROOT)),
        status=status,
        role=role,
        keep_reason=keep_reason,
        boundary=_boundary_lines(lines),
    )


def get_module_inventory() -> list[ModuleInventoryItem]:
    items = []
    for module_dir in sorted(ROOT.iterdir(), key=lambda p: p.name):
        if not module_dir.is_dir() or module_dir.name.startswith(".") or module_dir.name == "__pycache__":
            continue
        status_file = module_dir / "MODULE_STATUS.md"
        if status_file.exists():
            items.append(_read_status_file(module_dir))
    return items


def get_module_status(module: str) -> ModuleInventoryItem:
    for item in get_module_inventory():
        if item.module == module:
            return item
    raise KeyError(f"Unknown module or missing MODULE_STATUS.md: {module}")
