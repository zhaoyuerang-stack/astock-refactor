"""retire_version() —— 唯一的注册表退役通道(ADR-017 处置 + Task 15 状态机接线)。

退役必须:① 经状态机校验合法转换(非法源状态拒绝);② 落 control_events 链式审计;
③ 把 evidence.retirement 写入版本(原因 + 证据指针),不删除历史字段。
"""
import json

import pytest

import strategy_registry as sr
from governance.state_machine import IllegalTransition


def _seed(tmp_path, monkeypatch, status="在册"):
    reg = tmp_path / "strategy_versions.json"
    log = tmp_path / "control_events.jsonl"
    monkeypatch.setattr(sr, "REGISTRY", reg)
    sr.register_family("toyfam", "玩具策略", hypothesis="h", regime="r", decay_signal="d")
    sr.register(
        "toyfam", "v1.0", "desc", {}, {}, {"annual": 0.3, "maxdd": -0.1},
        status=status, admission={"track": "standalone"},
        nine_gate={"dsr_p": 0.01},  # ADR-020:在册 standalone 须 DSR 显著;本测试验退役流程与之正交
    )
    return reg, log


def test_retire_version_flips_status_and_appends_evidence(tmp_path, monkeypatch):
    reg, log = _seed(tmp_path, monkeypatch)
    sr.retire_version(
        "toyfam", "v1.0",
        reason="evidence_plagiarized_across_family",
        evidence_refs=["scripts/research/illiq_largecap_audit.py"],
        control_event_path=log,
    )
    data = json.loads(reg.read_text())
    v = next(x for x in data["families"][0]["versions"] if x["version"] == "v1.0")
    assert v["status"] == "退役"
    assert v["evidence"]["retirement"]["reason"] == "evidence_plagiarized_across_family"
    assert v["evidence"]["retirement"]["evidence_refs"] == [
        "scripts/research/illiq_largecap_audit.py"
    ]


def test_retire_version_writes_control_event(tmp_path, monkeypatch):
    reg, log = _seed(tmp_path, monkeypatch)
    sr.retire_version(
        "toyfam", "v1.0", reason="r", evidence_refs=[], control_event_path=log,
    )
    lines = [json.loads(ln) for ln in log.read_text().splitlines()]
    assert len(lines) == 1
    assert lines[0]["from_state"] == "REGISTERED"
    assert lines[0]["to_state"] == "RETIRED"
    assert lines[0]["family"] == "toyfam" and lines[0]["version"] == "v1.0"


def test_retire_version_rejects_illegal_source_state(tmp_path, monkeypatch):
    reg, log = _seed(tmp_path, monkeypatch, status="候选")
    with pytest.raises(IllegalTransition):
        sr.retire_version(
            "toyfam", "v1.0", reason="r", evidence_refs=[], control_event_path=log,
        )
    # 非法转换不得改动注册表
    data = json.loads(reg.read_text())
    v = next(x for x in data["families"][0]["versions"] if x["version"] == "v1.0")
    assert v["status"] == "候选"


def test_retire_version_unknown_identity_raises(tmp_path, monkeypatch):
    reg, log = _seed(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        sr.retire_version(
            "toyfam", "v9.9", reason="r", evidence_refs=[], control_event_path=log,
        )


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
