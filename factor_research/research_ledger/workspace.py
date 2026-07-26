"""Append-only research drafts and cross-source human review records."""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "data_lake" / "factory" / "research_workspace"
DEFAULT_DRAFT_PATH = DEFAULT_ROOT / "drafts.jsonl"
DEFAULT_REVIEW_PATH = DEFAULT_ROOT / "reviews.jsonl"
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class ResearchDraftRecord:
    draft_id: str
    title: str
    source: str = "manual"
    mechanism: str = ""
    citation: str = ""
    factor_fn_name: str = ""
    factor_params: dict[str, Any] = field(default_factory=dict)
    timing_fn_name: str | None = None
    timing_params: dict[str, Any] = field(default_factory=dict)
    data_dependencies: list[str] = field(default_factory=list)
    status: str = "active"
    linked_work_id: str = ""
    revision: int = 1
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class ResearchReviewRecord:
    review_id: str
    kind: str
    item_id: str
    action: str
    notes: str = ""
    reviewer: str = "human"
    reviewed_at: str = ""
    migrated_from: str = ""


class _LatestJsonl:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _append(self, payload: dict) -> None:
        with _LOCK:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    def _rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows: list[dict] = []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows


class DraftRepository(_LatestJsonl):
    def __init__(self, path: Path = DEFAULT_DRAFT_PATH):
        super().__init__(path)
        self._cache: dict[str, ResearchDraftRecord] = {}
        for row in self._rows():
            self._cache[row["draft_id"]] = ResearchDraftRecord(**row)

    def create(self, *, title: str, **fields: Any) -> ResearchDraftRecord:
        now = _now()
        record = ResearchDraftRecord(
            draft_id=uuid.uuid4().hex[:16],
            title=title,
            created_at=now,
            updated_at=now,
            **fields,
        )
        self._append(asdict(record))
        self._cache[record.draft_id] = record
        return record

    def update(self, draft_id: str, **fields: Any) -> ResearchDraftRecord:
        current = self.get(draft_id)
        clean = {key: value for key, value in fields.items() if value is not None}
        record = replace(
            current,
            **clean,
            revision=current.revision + 1,
            updated_at=_now(),
        )
        self._append(asdict(record))
        self._cache[draft_id] = record
        return record

    def get(self, draft_id: str) -> ResearchDraftRecord:
        record = self._cache.get(draft_id)
        if record is None:
            raise KeyError(draft_id)
        return record

    def all(self) -> list[ResearchDraftRecord]:
        return list(self._cache.values())


class ResearchReviewRepository(_LatestJsonl):
    def __init__(self, path: Path = DEFAULT_REVIEW_PATH):
        super().__init__(path)
        self._cache: dict[tuple[str, str], ResearchReviewRecord] = {}
        self._migration_keys: set[tuple[str, str, str, str]] = set()
        for row in self._rows():
            record = ResearchReviewRecord(**row)
            self._cache[(record.kind, record.item_id)] = record
            if record.migrated_from:
                self._migration_keys.add(
                    (record.kind, record.item_id, record.action, record.reviewed_at)
                )

    def record(
        self,
        *,
        kind: str,
        item_id: str,
        action: str,
        notes: str = "",
        reviewer: str = "human",
        reviewed_at: str = "",
        migrated_from: str = "",
    ) -> ResearchReviewRecord:
        if action not in {"approve", "reject"}:
            raise ValueError("action must be approve or reject")
        record = ResearchReviewRecord(
            review_id=uuid.uuid4().hex,
            kind=kind,
            item_id=item_id,
            action=action,
            notes=notes,
            reviewer=reviewer,
            reviewed_at=reviewed_at or _now(),
            migrated_from=migrated_from,
        )
        self._append(asdict(record))
        self._cache[(kind, item_id)] = record
        if migrated_from:
            self._migration_keys.add((kind, item_id, action, record.reviewed_at))
        return record

    def latest(self, kind: str, item_id: str) -> ResearchReviewRecord | None:
        return self._cache.get((kind, item_id))

    def all(self) -> list[ResearchReviewRecord]:
        return list(self._cache.values())

    def migrate_autoresearch(self, legacy_rows: list[dict]) -> int:
        migrated = 0
        for row in legacy_rows:
            action = row.get("review_action") or ""
            if action not in {"approve", "reject"}:
                continue
            item_id = str(row.get("fingerprint") or "")
            reviewed_at = str(row.get("reviewed_at") or "")
            key = ("autoresearch", item_id, action, reviewed_at)
            if not item_id or ("autoresearch", item_id) in self._cache or key in self._migration_keys:
                continue
            self.record(
                kind="autoresearch",
                item_id=item_id,
                action=action,
                notes=str(row.get("reviewer_notes") or ""),
                reviewed_at=reviewed_at,
                migrated_from="autoresearch_review_queue",
            )
            migrated += 1
        return migrated
