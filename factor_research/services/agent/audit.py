"""Append-only Agent tool-call audit log (ADR-037 P6.4).

Strict-rail executions pass through ``services.agent.capability.call_capability``;
this module records one JSONL line per attempt (ok or error), including
authorization denials. Values and tokens are never written — only digests,
key names, and non-sensitive context.

Path: ``reports/agent_audit/agent_audit_YYYYMM.jsonl`` under the research root
(not data_lake — operational observability, no lake-writer surface).
Override via ``audit_dir`` or env ``ASTOCK_AGENT_AUDIT_DIR`` (tests).
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app_config.log import get_logger

logger = get_logger(__name__)

# factor_research/ — same root as sessions.py / agent_cli ROOT
_RESEARCH_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_AUDIT_DIR = _RESEARCH_ROOT / "reports" / "agent_audit"

# Protocol/session keys: omit when not provided (do not force null).
_OPTIONAL_CONTEXT_KEYS = frozenset(
    {"protocol_id", "default_tier", "requires_hitl", "session_id"}
)


def _now() -> str:
    """UTC ISO8601, same style as sessions._now."""
    return (
        datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _args_digest(arguments: dict[str, Any] | None) -> str:
    payload = arguments if arguments is not None else {}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _args_keys(arguments: dict[str, Any] | None) -> list[str]:
    if not arguments:
        return []
    return sorted(str(k) for k in arguments.keys())


def _extract_evidence_tier(result: Any) -> str | None:
    """Pull tier string only; never copy performance payload numbers."""
    if result is None:
        return None
    # EvidenceEnvelope instance
    if hasattr(result, "evidence_tier"):
        tier = getattr(result, "evidence_tier", None)
        if tier is not None:
            return tier.value if hasattr(tier, "value") else str(tier)
    if not isinstance(result, dict):
        return None
    # tools.py wraps as evidence_envelope; also accept evidence / envelope
    for key in ("evidence_envelope", "evidence", "envelope"):
        env = result.get(key)
        if env is None:
            continue
        if isinstance(env, dict) and "evidence_tier" in env:
            t = env["evidence_tier"]
            return t.value if hasattr(t, "value") else str(t)
        if hasattr(env, "evidence_tier"):
            t = env.evidence_tier
            return t.value if hasattr(t, "value") else str(t)
    if "evidence_tier" in result:
        t = result["evidence_tier"]
        return t.value if hasattr(t, "value") else str(t)
    return None


def _resolve_dir(audit_dir: str | Path | None) -> Path:
    if audit_dir is not None:
        return Path(audit_dir)
    env = os.environ.get("ASTOCK_AGENT_AUDIT_DIR")
    if env:
        return Path(env)
    return _DEFAULT_AUDIT_DIR


def audit_event(
    tool: str,
    arguments: dict[str, Any] | None,
    *,
    outcome: str,
    error: BaseException | None = None,
    context: dict[str, Any] | None = None,
    result: Any = None,
    audit_dir: str | Path | None = None,
) -> None:
    """Append one audit JSON line. Never raises on I/O failure (warn via logger)."""
    ctx = dict(context or {})
    event: dict[str, Any] = {
        "ts": _now(),
        "tool": tool,
        "args_digest": _args_digest(arguments),
        "args_keys": _args_keys(arguments),
        "risk": ctx.get("risk", None),
        "confirm_token_present": bool(ctx.get("confirm_token_present", False)),
        "readonly_only": bool(ctx.get("readonly_only", False)),
        "outcome": outcome,
    }
    for key in _OPTIONAL_CONTEXT_KEYS:
        if key in ctx:
            event[key] = ctx[key]
    # Any other allowed context keys already handled above; ignore unknowns.
    if outcome == "error":
        event["error_type"] = type(error).__name__ if error is not None else "Error"
        msg = str(error) if error is not None else ""
        event["error_msg"] = msg[:200]
    tier = _extract_evidence_tier(result)
    if tier is not None:
        event["evidence_tier"] = tier

    try:
        root = _resolve_dir(audit_dir)
        root.mkdir(parents=True, exist_ok=True)
        ym = datetime.now(UTC).strftime("%Y%m")
        path = root / f"agent_audit_{ym}.jsonl"
        line = json.dumps(event, ensure_ascii=False, default=str)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        logger.warning(
            f"agent_audit: failed to write audit event for tool={tool!r}: {exc}"
        )
        return
