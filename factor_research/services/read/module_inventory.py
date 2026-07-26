"""Build a lightweight inventory from the source tree."""
from __future__ import annotations

from pathlib import Path

from contracts.agent_control import ModuleInventoryItem

ROOT = Path(__file__).resolve().parents[2]


def _source_files(module_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in module_dir.rglob("*")
        if path.is_file()
        and path.suffix != ".md"
        if "__pycache__" not in path.parts and ".mypy_cache" not in path.parts
    )


def _build_inventory_item(module_dir: Path) -> ModuleInventoryItem:
    files = _source_files(module_dir)
    status = "TEMP_ONLY" if module_dir.name == "scratch" else "present"
    return ModuleInventoryItem(
        module=module_dir.name,
        path=str(module_dir.relative_to(ROOT)),
        status=status,
        role=f"{len(files)} source files",
        keep_reason="Discovered from source tree",
        boundary=[],
    )


def get_module_inventory() -> list[ModuleInventoryItem]:
    items = []
    for module_dir in sorted(ROOT.iterdir(), key=lambda p: p.name):
        if not module_dir.is_dir() or module_dir.name.startswith(".") or module_dir.name == "__pycache__":
            continue
        if _source_files(module_dir):
            items.append(_build_inventory_item(module_dir))
    return items


def get_module_status(module: str) -> ModuleInventoryItem:
    for item in get_module_inventory():
        if item.module == module:
            return item
    if module == "scratch":
        return ModuleInventoryItem(
            module="scratch",
            path="scratch",
            status="TEMP_ONLY",
            role="0 source files",
            keep_reason="Temporary workspace only",
            boundary=[],
        )
    raise KeyError(f"Unknown source module: {module}")
