"""Pytest boundaries for the source-only public repository."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA_SENTINELS = (
    ROOT / "data_lake" / "price" / "daily_all.parquet",
    ROOT / "data_lake" / "fundamental_batch.parquet",
    ROOT / "data_lake" / "meta" / "trade_calendar.parquet",
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip declared integration tests only when the private lake is absent."""
    if os.environ.get("REQUIRE_DATA_LAKE") == "1":
        return
    if all(path.exists() for path in DATA_SENTINELS):
        return
    skip = pytest.mark.skip(
        reason="private data_lake payload is not installed; run with REQUIRE_DATA_LAKE=1 to enforce"
    )
    for item in items:
        if "data_lake" in item.keywords:
            item.add_marker(skip)
