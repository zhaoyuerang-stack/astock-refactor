"""Append-only 控制事件链(Task 15)。

每次状态变更写一条不可变事件,用 previous_event_hash/event_hash 串成链 —— 任何篡改/删除
都会断链。事件是 model approval / registry status / deployment status 的唯一史实来源。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from governance.state_machine import assert_transition

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = ROOT / "data_lake" / "governance" / "control_events.jsonl"

GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class ControlEvent:
    event_id: str
    timestamp: str
    actor: str
    family: str
    version: str
    spec_hash: str
    from_state: str
    to_state: str
    reason_code: str
    evidence_refs: tuple
    previous_event_hash: str
    event_hash: str = ""

    def _payload(self) -> dict:
        d = asdict(self)
        d.pop("event_hash")
        d["evidence_refs"] = list(self.evidence_refs)
        return d

    def compute_hash(self) -> str:
        body = json.dumps(self._payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _read_all(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def append_event(
    *, event_id: str, timestamp: str, actor: str, family: str, version: str,
    spec_hash: str, from_state: str, to_state: str, reason_code: str,
    evidence_refs=(), path: Path | str = DEFAULT_LOG,
) -> ControlEvent:
    """校验转换合法后追加一条链式事件。非法转换抛 IllegalTransition,不落盘。"""
    assert_transition(from_state, to_state)
    path = Path(path)
    records = _read_all(path)
    prev_hash = records[-1]["event_hash"] if records else GENESIS_HASH

    ev = ControlEvent(
        event_id=event_id, timestamp=timestamp, actor=actor, family=family,
        version=version, spec_hash=spec_hash, from_state=from_state, to_state=to_state,
        reason_code=reason_code, evidence_refs=tuple(evidence_refs),
        previous_event_hash=prev_hash,
    )
    ev = ControlEvent(**{**asdict(ev), "evidence_refs": tuple(evidence_refs),
                         "event_hash": ev.compute_hash()})

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({**ev._payload(), "event_hash": ev.event_hash},
                           ensure_ascii=False) + "\n")
    return ev


def verify_chain(path: Path | str = DEFAULT_LOG) -> bool:
    """逐条校验 hash 链完整性:previous 串接 + 每条 event_hash 自洽。"""
    records = _read_all(Path(path))
    prev = GENESIS_HASH
    for rec in records:
        if rec.get("previous_event_hash") != prev:
            return False
        stored = rec.get("event_hash", "")
        body = {k: rec[k] for k in rec if k != "event_hash"}
        body["evidence_refs"] = list(body.get("evidence_refs", []))
        recomputed = hashlib.sha256(
            json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if recomputed != stored:
            return False
        prev = stored
    return True
