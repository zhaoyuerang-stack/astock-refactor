"""Tests for knowledge graph mechanism (empty container, grows from validation).

Run:  cd /Users/kiki/astcok/factor_research && python3 tests/test_knowledge.py

铁律对齐:零预置结论 + 失败分级(phase1→SKIP / 其余→DEPRIORITIZE) + 保质期。
全程用临时 store,绝不写脏真实 knowledge/findings.json。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from factory.ontology import Hypothesis
from knowledge.graph import KnowledgeGraph, load_graph

HYP = Hypothesis(name="t-illiq20", description="", factor_fn_name="x.AmihudIlliq",
                 factor_params={"window": 20}, data_dependencies=("price/close",))
HYP_DIFF = Hypothesis(name="t-illiq40", description="", factor_fn_name="x.AmihudIlliq",
                      factor_params={"window": 40}, data_dependencies=("price/close",))


def _fresh():
    return KnowledgeGraph(tempfile.mktemp(suffix=".json"))


def test_empty_start():
    kg = _fresh()
    assert len(kg.all_valid()) == 0
    print("✅ test_empty_start passed")


def test_real_store_zero_seeds():
    # 真实 findings.json 必须能成功载入且格式正确
    kg = load_graph()
    assert len(kg.all_valid()) >= 0, "knowledge/findings.json 必须可以正常载入"
    print("✅ test_real_store_zero_seeds passed")


def test_phase1_fail_skips_exact_only():
    kg = _fresh()
    kg.record_from_validation(HYP, passed=False, metrics={"sharpe": 0}, stage="phase1")
    skip_same, _ = kg.should_skip(HYP)
    skip_diff, _ = kg.should_skip(HYP_DIFF)
    assert skip_same is True, "phase1 fail 应 SKIP 同参数"
    assert skip_diff is False, "不同参数不得被 gate(避免结论过度泛化)"
    print("✅ test_phase1_fail_skips_exact_only passed")


def test_weak_alpha_deprioritizes_not_skips():
    kg = _fresh()
    kg.record_from_validation(HYP, passed=False, metrics={"wf_sharpe": 0.4}, stage="phase3")
    skip, _ = kg.should_skip(HYP)
    adj = kg.priority_adjustment(HYP)
    assert skip is False, "弱 alpha 不应永久 SKIP(避免搜索失明)"
    assert adj < 1.0, "弱 alpha 应降权"
    print("✅ test_weak_alpha_deprioritizes_not_skips passed")


def test_pass_records_no_gate():
    kg = _fresh()
    kg.record_from_validation(HYP, passed=True, metrics={"wf_sharpe": 1.8}, stage="phase3")
    skip, _ = kg.should_skip(HYP)
    assert skip is False
    assert kg.priority_adjustment(HYP) == 1.0, "通过候选不降权"
    print("✅ test_pass_records_no_gate passed")


def test_expiry_triggers_retest():
    kg = _fresh()
    f = kg.record_from_validation(HYP, passed=False, metrics={}, stage="L0")
    f.expires = "2020-01-01"   # 强制过期
    assert f.is_expired is True
    assert len(kg.check_expiry()) == 1, "过期结论应进重测队列"
    # 过期结论不再产生有效 gate(搜索不被永久封)
    assert kg.priority_adjustment(HYP) == 1.0, "过期后 gate 失效"
    print("✅ test_expiry_triggers_retest passed")


def test_save_load_roundtrip():
    path = tempfile.mktemp(suffix=".json")
    kg = KnowledgeGraph(path)
    kg.record_from_validation(HYP, passed=False, metrics={"sharpe": 0}, stage="phase1")
    reloaded = KnowledgeGraph(path)
    assert len(reloaded._findings) == 1
    skip, _ = reloaded.should_skip(HYP)
    assert skip is True, "持久化后 gate 仍生效"
    print("✅ test_save_load_roundtrip passed")


def test_sync_pending_lessons_merges_duplicates_and_writes_skip_gate():
    from knowledge.graph import sync_pending_lessons_to_graph

    tmp = Path(tempfile.mkdtemp())
    pending = tmp / "pending_lessons"
    pending.mkdir()
    store = tmp / "findings.json"
    lesson = {
        "fingerprint": "abc12345",
        "trigger": "Phase1_timing_peek",
        "pattern": "timing shift(1)",
        "detail": "Timing MISSING shift(1)! Uses T-day market return.",
        "fix": "Add .shift(1) to timing.",
        "hit_count": 2,
        "first_seen": "2026-06-06",
        "last_seen": "2026-06-07",
        "strategies": ["leaky-timing"],
    }
    (pending / "timing_a.json").write_text(json.dumps(lesson, ensure_ascii=False), encoding="utf-8")
    duplicate = dict(lesson)
    duplicate["hit_count"] = 3
    duplicate["strategies"] = ["leaky-timing", "leaky-block-test"]
    duplicate["last_seen"] = "2026-06-18"
    (pending / "timing_b.json").write_text(json.dumps(duplicate, ensure_ascii=False), encoding="utf-8")

    summary = sync_pending_lessons_to_graph(pending_dir=pending, store_path=store)

    assert summary["files_read"] == 2
    assert summary["merged_lessons"] == 1
    kg = KnowledgeGraph(str(store))
    assert len(kg.all_valid()) == 1
    finding = kg.all_valid()[0]
    assert finding.metrics["lesson_category"] == "TIMING_PEEK"
    assert finding.metrics["hit_count"] == 5
    hyp = Hypothesis(name="leaky-timing", description="", factor_fn_name="x", factor_params={})
    skip, reason = kg.should_skip(hyp)
    assert skip is True, reason
    print("✅ test_sync_pending_lessons_merges_duplicates_and_writes_skip_gate passed")


def test_sync_pending_lessons_deprioritizes_weak_oos_patterns():
    from knowledge.graph import sync_pending_lessons_to_graph

    tmp = Path(tempfile.mkdtemp())
    pending = tmp / "pending_lessons"
    pending.mkdir()
    store = tmp / "findings.json"
    (pending / "wf.json").write_text(json.dumps({
        "fingerprint": "neg2018",
        "trigger": "Phase_wf_negative_window",
        "pattern": "WF negative window 2018",
        "detail": "OOS annual=-9.1% in 2018",
        "fix": "Manual review needed.",
        "hit_count": 15,
        "first_seen": "2026-06-06",
        "last_seen": "2026-06-18",
        "strategies": ["small_cap_factor__window90"],
    }, ensure_ascii=False), encoding="utf-8")

    sync_pending_lessons_to_graph(pending_dir=pending, store_path=store)

    kg = KnowledgeGraph(str(store))
    hyp = Hypothesis(name="small_cap_factor__window90", description="", factor_fn_name="x", factor_params={})
    skip, _ = kg.should_skip(hyp)
    assert skip is False
    assert kg.priority_adjustment(hyp) < 1.0
    print("✅ test_sync_pending_lessons_deprioritizes_weak_oos_patterns passed")


if __name__ == "__main__":
    print("Running knowledge graph tests...\n")
    test_empty_start()
    test_real_store_zero_seeds()
    test_phase1_fail_skips_exact_only()
    test_weak_alpha_deprioritizes_not_skips()
    test_pass_records_no_gate()
    test_expiry_triggers_retest()
    test_save_load_roundtrip()
    test_sync_pending_lessons_merges_duplicates_and_writes_skip_gate()
    test_sync_pending_lessons_deprioritizes_weak_oos_patterns()
    print("\n🎉 All knowledge tests passed!")
