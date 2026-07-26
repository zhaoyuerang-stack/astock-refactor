"""对抗性测试:研究结论保质期复核环(services.read.knowledge_expiry + 收件箱源)。

Run:  cd factor_research && python3 tests/test_knowledge_expiry.py

护栏 C:过期 finding 必须被点名(含父过期级联——只查自身过期的实现必挂);
过期方向条目必须带复活条件透出;全新鲜 → fresh 不造事项;
收件箱 info 级不计入待裁决数;源爆炸 → source_error 显式入箱。
全程 fixture 文件,不读真实 knowledge/。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contracts.views import DataQualityView, PromotionReadinessView, SystemTruthView
from services.read.decision_inbox import get_decision_inbox
from services.read.knowledge_expiry import get_knowledge_expiry


def _finding(fid, expires, depends_on=None):
    return {"id": fid, "statement": f"stmt-{fid}", "domain": "factor", "confidence": 0.8,
            "evidence": ["e"], "created": "2026-01-01", "expires": expires,
            "depends_on": depends_on or [], "metrics": {}, "gates": []}


def _write_store(td, findings):
    p = Path(td) / "findings.json"
    p.write_text(json.dumps({f["id"]: f for f in findings}, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _write_registry(td, entries):
    p = Path(td) / "registry.json"
    p.write_text(json.dumps({"version": 1, "entries": entries}, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _dir_entry(eid, expires, revival=""):
    return {"id": eid, "direction": f"方向-{eid}", "status": "weak", "action": "DEPRIORITIZE",
            "scope_factors": ["momentum"], "evidence": ["LESSONS.md#x"],
            "revival_condition": revival, "created": "2026-01-01", "expires": expires}


def test_expired_findings_and_parent_cascade_detected():
    with tempfile.TemporaryDirectory() as td:
        store = _write_store(td, [
            _finding("dead", "2020-01-01"),
            _finding("child_of_dead", "2099-01-01", depends_on=["dead"]),  # 父过期 → 级联重测
            _finding("fresh", "2099-01-01"),
        ])
        reg = _write_registry(td, [])
        out = get_knowledge_expiry(store_path=store, registry_path=reg)
        ids = {f["id"] for f in out["expired_findings"]}
        assert "dead" in ids, "自身过期必须点名"
        assert "child_of_dead" in ids, "父结论过期必须级联点名——只查自身过期的实现必挂这里"
        assert "fresh" not in ids
        assert out["state"] == "retest_due" and out["n_expired"] == 2


def test_expired_direction_entry_surfaces_with_revival_condition():
    with tempfile.TemporaryDirectory() as td:
        store = _write_store(td, [_finding("fresh", "2099-01-01")])
        reg = _write_registry(td, [
            _dir_entry("old-dir", "2020-01-01", revival="宽持仓基因组落地后重测"),
            _dir_entry("live-dir", "2099-01-01"),
        ])
        out = get_knowledge_expiry(store_path=store, registry_path=reg)
        assert out["state"] == "retest_due" and out["n_expired"] == 1
        d = out["expired_directions"][0]
        assert d["id"] == "old-dir" and "宽持仓" in d["revival_condition"]


def test_all_fresh_is_fresh():
    with tempfile.TemporaryDirectory() as td:
        store = _write_store(td, [_finding("fresh", "2099-01-01"), _finding("forever", "")])
        reg = _write_registry(td, [_dir_entry("live-dir", "2099-01-01")])
        out = get_knowledge_expiry(store_path=store, registry_path=reg)
        assert out["state"] == "fresh" and out["n_expired"] == 0


# ── 收件箱源 ────────────────────────────────────────────────────────────────

def _inbox_kwargs(**overrides):
    base = dict(
        gate_verdicts=[], system_truth=SystemTruthView(declared_present=False),
        review_pending=[], decay=None,
        data_quality_view=DataQualityView(total=100, clean=100, clean_ratio=1.0,
                                          verdict="可用", severe_count=0),
        promotion=PromotionReadinessView(), exhaustion={"state": "healthy"},
        recompose=None, knowledge_expiry={"state": "fresh"},
    )
    base.update(overrides)
    return base


def test_inbox_retest_due_is_info_not_pending():
    ke = {"state": "retest_due", "n_expired": 2,
          "expired_findings": [{"id": "dead", "statement": "s", "expires": "2020-01-01"}],
          "expired_directions": [{"id": "old-dir", "revival_condition": "条件X"}]}
    v = get_decision_inbox(**_inbox_kwargs(knowledge_expiry=ke))
    item = next(i for i in v.items if i.kind == "knowledge_expiry")
    assert item.severity == "info" and v.pending_count == 0
    assert any("old-dir" in e for e in item.evidence)
    assert any("新证据" in a.reason for a in item.actions), "续期动作必须警示『裸续期=永久墓碑』"


def test_inbox_fresh_creates_no_item():
    v = get_decision_inbox(**_inbox_kwargs(knowledge_expiry={"state": "fresh"}))
    assert not [i for i in v.items if i.kind == "knowledge_expiry"]
    assert v.pending_count == 0 and "无需你介入" in v.headline


def test_inbox_source_explosion_surfaces():
    class _Boom(dict):
        def get(self, *a, **k):  # noqa: D102
            raise RuntimeError("expiry source exploded")

    v = get_decision_inbox(**_inbox_kwargs(knowledge_expiry=_Boom()))
    assert [i for i in v.items if i.kind == "source_error" and "knowledge_expiry" in i.key]
    assert not v.all_sources_readable


def _run_all():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
        except AssertionError as e:
            failed += 1
            print(f"  ❌ {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
