"""Task 9: 单一 Nine-Gate 裁决 —— 杜绝 DSR-only 批准,缺失/失败 fail-closed。"""
import pytest

from core.analysis.nine_gate_policy import decide_nine_gate


def test_passed_all_true_is_approved():
    d = decide_nine_gate({"passed_all": True, "dsr_significant": True})
    assert d.code == "PASSED" and d.approved is True and d.audited is True


def test_passed_all_false_is_blocked_even_if_dsr_significant():
    # DSR 显著也救不回 passed_all=False —— 这正是要消除的降维
    d = decide_nine_gate({"passed_all": False, "dsr_significant": True, "dsr_p": 0.001})
    assert d.code == "FAILED" and d.approved is False and d.audited is True
    assert "passed_all_false" in d.blocking_reasons


def test_pbo_high_cannot_be_overridden_by_dsr():
    d = decide_nine_gate({"passed_all": False, "dsr_significant": True, "pbo": 0.8})
    assert d.approved is False
    assert "pbo_high" in d.blocking_reasons


def test_missing_passed_all_is_pending_not_inferred_pass():
    # 只有 DSR 显著、没有 passed_all → 绝不能推断为通过
    d = decide_nine_gate({"dsr_p": 0.001, "dsr_significant": True})
    assert d.code == "PENDING" and d.approved is False and d.audited is False


def test_run_failed_is_blocked():
    d = decide_nine_gate({"status": "FAILED_TO_RUN"})
    assert d.code == "RUN_FAILED" and d.approved is False and d.audited is False


def test_none_summary_is_pending():
    d = decide_nine_gate(None)
    assert d.code == "PENDING" and d.approved is False


def test_as_state_preserves_legacy_contract():
    st = decide_nine_gate({"passed_all": True}).as_state()
    assert set(st) >= {"code", "label", "audited", "passed"}
    assert st["passed"] is True and st["label"] == "审计通过"
    pend = decide_nine_gate(None).as_state()
    assert pend["passed"] is None  # PENDING 的 passed 必须是 None,不得伪造 True/False


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
