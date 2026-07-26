"""ADR-037 P6.4 — Agent tool-call append-only audit log (adversarial tests).

All cases use tmp audit dirs; never write real reports/agent_audit.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.agent_cli import AgentCliError, call_capability
from services.agent.audit import audit_event
from services.agent.tools import RISK_MID, RISK_READONLY, Tool


def _audit_lines(audit_dir: Path) -> list[dict]:
    files = sorted(audit_dir.glob("agent_audit_*.jsonl"))
    assert files, f"no audit file under {audit_dir}"
    lines = []
    for p in files:
        text = p.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip():
                lines.append(json.loads(line))
    return lines


def _raw_audit_text(audit_dir: Path) -> str:
    parts = []
    for p in sorted(audit_dir.glob("agent_audit_*.jsonl")):
        parts.append(p.read_text(encoding="utf-8"))
    return "".join(parts)


def _registry_ok() -> dict[str, Tool]:
    return {
        "echo": Tool(
            "echo",
            RISK_READONLY,
            "echo",
            lambda x: {"echo": x, "evidence_envelope": {"evidence_tier": "precheck"}},
            ("x",),
        ),
        "boom": Tool(
            "boom",
            RISK_READONLY,
            "boom",
            lambda: (_ for _ in ()).throw(RuntimeError("boom-fail")),
            (),
        ),
        "mid_tool": Tool(
            "mid_tool",
            RISK_MID,
            "mid",
            lambda: {"ok": True},
            (),
        ),
    }


@pytest.fixture
def audit_dir(tmp_path, monkeypatch):
    d = tmp_path / "agent_audit"
    d.mkdir()
    monkeypatch.setenv("ASTOCK_AGENT_AUDIT_DIR", str(d))
    return d


# ── 1. success → exactly one line, full fields, outcome=ok ──────────────


def test_success_appends_one_complete_ok_event(audit_dir):
    result = call_capability("echo", {"x": "hello"}, _registry_ok())
    assert result["echo"] == "hello"
    events = _audit_lines(audit_dir)
    assert len(events) == 1
    ev = events[0]
    assert ev["outcome"] == "ok"
    assert ev["tool"] == "echo"
    assert "ts" in ev and ev["ts"].endswith("Z")
    assert isinstance(ev["args_digest"], str) and len(ev["args_digest"]) == 16
    assert ev["args_keys"] == ["x"]
    assert ev["risk"] == RISK_READONLY
    assert ev["confirm_token_present"] is False
    assert ev["readonly_only"] is False
    assert "error_type" not in ev
    assert "error_msg" not in ev
    assert ev.get("evidence_tier") == "precheck"


# ── 2. tool raises → error event on disk + exception propagates ─────────


def test_tool_exception_audits_error_and_reraises(audit_dir):
    with pytest.raises(RuntimeError, match="boom-fail"):
        call_capability("boom", {}, _registry_ok())
    events = _audit_lines(audit_dir)
    assert len(events) == 1
    ev = events[0]
    assert ev["outcome"] == "error"
    assert ev["tool"] == "boom"
    assert ev["error_type"] == "RuntimeError"
    assert "boom-fail" in ev["error_msg"]


# ── 3. authorization denials leave traces ────────────────────────────────


def test_readonly_denial_audits_error(audit_dir):
    with pytest.raises(AgentCliError, match="readonly mode"):
        call_capability(
            "mid_tool",
            {},
            _registry_ok(),
            readonly_only=True,
        )
    events = _audit_lines(audit_dir)
    assert len(events) == 1
    assert events[0]["outcome"] == "error"
    assert events[0]["tool"] == "mid_tool"
    assert events[0]["readonly_only"] is True
    assert events[0]["risk"] == RISK_MID
    assert "readonly" in events[0]["error_msg"].lower() or "not available" in events[0]["error_msg"]


def test_confirm_token_missing_audits_error(audit_dir, monkeypatch):
    monkeypatch.setenv("ASTOCK_MID_CONFIRM_TOKEN", "expected-secret")
    with pytest.raises(AgentCliError, match="confirm"):
        call_capability("mid_tool", {}, _registry_ok(), confirm_token=None)
    events = _audit_lines(audit_dir)
    assert len(events) == 1
    assert events[0]["outcome"] == "error"
    assert events[0]["confirm_token_present"] is False
    assert events[0]["risk"] == RISK_MID


# ── 4. no secrets in log text ────────────────────────────────────────────


def test_secrets_never_appear_in_audit_text(audit_dir, monkeypatch):
    monkeypatch.setenv("ASTOCK_MID_CONFIRM_TOKEN", "TOPSECRET_TOKEN_456")
    # mid tool with secret arg + matching confirm so execution can succeed
    reg = {
        "mid_secret": Tool(
            "mid_secret",
            RISK_MID,
            "mid",
            lambda secret: {"got": True},
            ("secret",),
        ),
    }
    call_capability(
        "mid_secret",
        {"secret": "TOPSECRET_ARG_VALUE_123"},
        reg,
        confirm_token="TOPSECRET_TOKEN_456",
    )
    raw = _raw_audit_text(audit_dir)
    assert "TOPSECRET_ARG_VALUE_123" not in raw
    assert "TOPSECRET_TOKEN_456" not in raw
    events = _audit_lines(audit_dir)
    assert len(events) == 1
    assert events[0]["args_digest"]
    assert events[0]["args_keys"] == ["secret"]
    assert events[0]["confirm_token_present"] is True
    assert events[0]["outcome"] == "ok"


# ── 5. performance numbers never land in log ─────────────────────────────


def test_performance_payload_not_in_audit(audit_dir):
    result_payload = {
        "annualized": 0.287654321,
        "sharpe": 1.987654321,
        "evidence_envelope": {
            "evidence_tier": "engine",
            "payload": {"annualized": 0.287654321, "sharpe": 1.987654321},
        },
    }
    reg = {
        "perf": Tool(
            "perf",
            RISK_READONLY,
            "perf",
            lambda: result_payload,
            (),
        ),
    }
    call_capability("perf", {}, reg)
    raw = _raw_audit_text(audit_dir)
    assert "0.287654321" not in raw
    assert "1.987654321" not in raw
    assert "annualized" not in raw
    assert "sharpe" not in raw
    events = _audit_lines(audit_dir)
    assert events[0]["evidence_tier"] == "engine"
    assert events[0]["outcome"] == "ok"


# ── 6. append-only: two events → two lines, first unchanged ──────────────


def test_append_only_two_events(audit_dir):
    call_capability("echo", {"x": "a"}, _registry_ok())
    files = list(audit_dir.glob("agent_audit_*.jsonl"))
    assert len(files) == 1
    first_snapshot = files[0].read_text(encoding="utf-8")
    assert first_snapshot.count("\n") == 1 or (
        first_snapshot.strip() and first_snapshot.count("\n") >= 1
    )
    line1 = first_snapshot.strip().splitlines()[0]

    call_capability("echo", {"x": "b"}, _registry_ok())
    full = files[0].read_text(encoding="utf-8")
    lines = [ln for ln in full.splitlines() if ln.strip()]
    assert len(lines) == 2
    assert lines[0] == line1
    assert json.loads(lines[0])["args_keys"] == ["x"]
    assert json.loads(lines[1])["outcome"] == "ok"


# ── 7. unwritable audit dir → tool still succeeds + stderr warning ───────


def test_unwritable_audit_dir_does_not_break_call(tmp_path, monkeypatch, caplog):
    # Point at a regular file so mkdir / open fails with OSError.
    blocked = tmp_path / "not_a_directory"
    blocked.write_text("block", encoding="utf-8")
    monkeypatch.setenv("ASTOCK_AGENT_AUDIT_DIR", str(blocked))

    result = call_capability("echo", {"x": "still-works"}, _registry_ok())
    assert result["echo"] == "still-works"
    # P1-3 后告警统一走 get_logger,断言 WARNING 记录本身而非输出通道
    assert "agent_audit" in caplog.text


# ── 8. protocol_runner: protocol_id present, single line (no double-log) ─


def test_protocol_runner_single_line_with_protocol_id(audit_dir, monkeypatch):
    from services.agent.protocol_runner import run_protocol_step
    from services.agent.protocols import get_protocol

    # idea_precheck allows strategy_idea_check — inject a pure fake tool.
    def fake_idea(idea: str):
        return {
            "idea": idea,
            "evidence_envelope": {"evidence_tier": "precheck"},
        }

    fake_tools = {
        "strategy_idea_check": Tool(
            "strategy_idea_check",
            RISK_READONLY,
            "idea",
            fake_idea,
            ("idea",),
        ),
    }
    monkeypatch.setattr("services.agent.capability.tool_registry", lambda: fake_tools)
    spec = get_protocol("idea_precheck")
    assert "strategy_idea_check" in spec.allowed_tools

    out = run_protocol_step(
        "idea_precheck",
        "strategy_idea_check",
        {"idea": "低估值 周频"},
    )
    assert out["protocol_id"] == "idea_precheck"
    events = _audit_lines(audit_dir)
    assert len(events) == 1, f"expected single audit line, got {len(events)}: {events}"
    assert events[0]["protocol_id"] == "idea_precheck"
    assert events[0]["tool"] == "strategy_idea_check"
    assert events[0]["outcome"] == "ok"
    assert "default_tier" in events[0]
    assert "requires_hitl" in events[0]


# ── direct unit: audit_event field shape ─────────────────────────────────


def test_audit_event_direct_args_none(audit_dir):
    audit_event(
        "t",
        None,
        outcome="ok",
        context={"risk": "readonly", "confirm_token_present": False, "readonly_only": False},
        audit_dir=audit_dir,
    )
    ev = _audit_lines(audit_dir)[0]
    assert ev["args_keys"] == []
    assert len(ev["args_digest"]) == 16
