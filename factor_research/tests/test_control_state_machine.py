"""Task 15: 状态机合法转换 + append-only 控制事件链。"""
import json

import pytest

from governance import control_events as CE
from governance.state_machine import (
    CN_LABELS,
    CN_TO_STATE,
    IllegalTransition,
    assert_transition,
    can_transition,
    is_terminal,
)

# ───────── 状态机 ─────────

def test_legal_happy_path():
    chain = ["DRAFT", "CANDIDATE", "VALIDATED", "REGISTERED", "DEPLOYED"]
    for a, b in zip(chain, chain[1:], strict=False):
        assert_transition(a, b)  # 不抛即合法


def test_candidate_cannot_jump_to_deployed():
    assert not can_transition("CANDIDATE", "DEPLOYED")
    with pytest.raises(IllegalTransition):
        assert_transition("CANDIDATE", "DEPLOYED")


def test_retired_is_terminal_no_redeploy():
    assert is_terminal("RETIRED")
    with pytest.raises(IllegalTransition):
        assert_transition("RETIRED", "DEPLOYED")


def test_suspended_can_return_to_deployed():
    assert_transition("DEPLOYED", "SUSPENDED")
    assert_transition("SUSPENDED", "DEPLOYED")


def test_cn_label_roundtrip():
    assert CN_LABELS["REGISTERED"] == "在册"
    assert CN_TO_STATE["在册"] == "REGISTERED"


# ───────── 控制事件链 ─────────

def _append(tmp, frm, to, eid):
    return CE.append_event(
        event_id=eid, timestamp="2026-06-20T00:00:00+08:00", actor="test",
        family="illiquidity", version="v3.1", spec_hash="abc",
        from_state=frm, to_state=to, reason_code="test", evidence_refs=("exp-1",),
        path=tmp,
    )


def test_event_chain_links_and_verifies(tmp_path):
    log = tmp_path / "ce.jsonl"
    e1 = _append(log, "DRAFT", "CANDIDATE", "e1")
    e2 = _append(log, "CANDIDATE", "VALIDATED", "e2")
    assert e1.previous_event_hash == CE.GENESIS_HASH
    assert e2.previous_event_hash == e1.event_hash
    assert CE.verify_chain(log) is True


def test_illegal_transition_not_written(tmp_path):
    log = tmp_path / "ce.jsonl"
    _append(log, "DRAFT", "CANDIDATE", "e1")
    with pytest.raises(IllegalTransition):
        _append(log, "CANDIDATE", "DEPLOYED", "bad")
    # 非法事件不得落盘
    lines = log.read_text().splitlines()
    assert len(lines) == 1


def test_tamper_breaks_chain(tmp_path):
    log = tmp_path / "ce.jsonl"
    _append(log, "DRAFT", "CANDIDATE", "e1")
    _append(log, "CANDIDATE", "VALIDATED", "e2")
    assert CE.verify_chain(log) is True
    # 篡改第一条的 reason_code → 链断
    recs = [json.loads(l) for l in log.read_text().splitlines()]
    recs[0]["reason_code"] = "tampered"
    log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n")
    assert CE.verify_chain(log) is False


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
