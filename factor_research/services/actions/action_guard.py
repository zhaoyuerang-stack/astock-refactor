"""Local confirmation-token guard for write/costly UI actions."""
from __future__ import annotations

import json
import os
import secrets
import stat
from datetime import date
from pathlib import Path

from fastapi import HTTPException, Request

ROOT = Path(__file__).resolve().parents[2]
ACTION_TOKEN_ENV = "ASTCOK_ACTION_TOKEN"
ACTION_TOKEN_FILE = ROOT / "data_lake" / "agent" / "action_token"
ACTION_AUDIT_FILE = ROOT / "data_lake" / "agent" / "action_audit.jsonl"
ACTION_HEADER = "X-Action-Token"


def current_action_token() -> tuple[str, str]:
    """Return the local action token, creating a gitignored file token if needed."""
    env_token = os.environ.get(ACTION_TOKEN_ENV, "").strip()
    if env_token:
        return env_token, "env"

    if ACTION_TOKEN_FILE.exists():
        token = ACTION_TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token, "file"

    token = secrets.token_urlsafe(32)
    ACTION_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACTION_TOKEN_FILE.write_text(token + "\n", encoding="utf-8")
    try:
        ACTION_TOKEN_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return token, "file"


def verify_action_token(token: str | None) -> None:
    expected, _source = current_action_token()
    supplied = (token or "").strip()
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail=f"missing or invalid {ACTION_HEADER}")


def is_loopback_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def audit_action(summary: str, detail: str = "", *, status: str = "accepted", actor: str = "human") -> None:
    """Append action audit without recording secrets or payload contents."""
    try:
        ACTION_AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "date": str(date.today()),
            "summary": summary,
            "detail": detail,
            "status": status,
            "actor": actor,
        }
        with ACTION_AUDIT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass
