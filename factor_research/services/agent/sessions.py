"""Persistent Agent chat sessions.

Local JSON storage is the v1 persistence layer. The shape is intentionally
multi-user ready (`user_id`) so it can later move to a database without changing
the API contract.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)
DEFAULT_STORE_DIR = ROOT / "data_lake" / "agent" / "sessions"
_SESSION_RE = re.compile(r"^s-[a-f0-9]{12}$")


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _store(store_dir: str | Path | None = None) -> Path:
    return Path(store_dir) if store_dir is not None else DEFAULT_STORE_DIR


def _path(session_id: str, store_dir: str | Path | None = None) -> Path:
    if not _SESSION_RE.match(session_id):
        raise ValueError(f"invalid session_id: {session_id}")
    return _store(store_dir) / f"{session_id}.json"


def _save(session: dict[str, Any], store_dir: str | Path | None = None) -> dict[str, Any]:
    p = _path(session["session_id"], store_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    return session


def create_session(
    *,
    page_context: str = "",
    title: str = "AI 会话",
    user_id: str = "local",
    store_dir: str | Path | None = None,
) -> dict[str, Any]:
    ts = _now()
    session = {
        "session_id": "s-" + uuid.uuid4().hex[:12],
        "user_id": user_id or "local",
        "title": title or "AI 会话",
        "page_context": page_context or "",
        "status": "active",
        "created_at": ts,
        "updated_at": ts,
        "messages": [],
    }
    return _save(session, store_dir)


def get_session(session_id: str, *, store_dir: str | Path | None = None) -> dict[str, Any]:
    p = _path(session_id, store_dir)
    if not p.exists():
        raise FileNotFoundError(session_id)
    return json.loads(p.read_text(encoding="utf-8"))


def list_sessions(*, user_id: str = "local", limit: int = 20, store_dir: str | Path | None = None) -> list[dict[str, Any]]:
    root = _store(store_dir)
    if not root.exists():
        return []
    sessions = []
    for p in root.glob("s-*.json"):
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # 单条会话文件坏则跳过(列表 best-effort),但必须留痕
            logger.warning("skip unreadable agent session %s: %s", p.name, exc)
            continue
        if s.get("user_id", "local") == user_id:
            s = dict(s)
            s["messages"] = s.get("messages", [])[-2:]
            sessions.append(s)
    sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
    return sessions[:limit]


def append_message(
    session_id: str,
    role: str,
    content: str,
    *,
    metadata: dict[str, Any] | None = None,
    store_dir: str | Path | None = None,
) -> dict[str, Any]:
    if role not in {"user", "assistant"}:
        raise ValueError(f"invalid role: {role}")
    session = get_session(session_id, store_dir=store_dir)
    ts = _now()
    session.setdefault("messages", []).append({
        "role": role,
        "content": content,
        "created_at": ts,
        "metadata": metadata or {},
    })
    session["updated_at"] = ts
    return _save(session, store_dir)


def history_for_llm(session: dict[str, Any], *, limit: int = 8) -> list[dict[str, str]]:
    return [
        {"role": m.get("role", ""), "content": m.get("content", "")}
        for m in session.get("messages", [])[-limit:]
        if m.get("role") in {"user", "assistant"} and m.get("content")
    ]
