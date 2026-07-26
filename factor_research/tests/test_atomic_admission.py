"""Task 8: 原子准入 —— 任一证据缺失/失败都不得授予「在册」。"""
import pytest

from core.strategy_spec import ExecutableStrategySpec
from workflow.admission import AdmissionEvidence, evaluate_admission


def _spec():
    return ExecutableStrategySpec(
        family="illiquidity", version="v3.1",
        universe={"market": "A_SHARE"}, data={"price_units": "shares_yuan"},
        factor={"type": "amihud_illiquidity", "window": 20, "shift": 1},
        selection={"top_n": 25, "rebalance_days": 20},
        timing={"type": "pure_trend_band", "ma": 16, "cap": 1.5},
        policy={"veto": "none"},
        execution={"fill": "T_PLUS_1_CLOSE", "cost_model": "A_SHARE_STANDARD_V1"},
    )


def _full_evidence(spec, **overrides):
    base = dict(
        phase1={"status": "PASS"},
        phase2={"status": "PASS"},
        phase3={"verdict": "PASS"},
        nine_gate={"passed_all": True, "dsr_significant": True},
        holdout={"peek_count": 1},
        marginal={"verdict": "PASS"},
        experiment_ids=("exp-1",),
        data_fingerprint="fp",
        spec_hash=spec.spec_hash,
    )
    base.update(overrides)
    return AdmissionEvidence(**base)


def _decide(spec, evidence, track="standalone", require_marginal=False):
    return evaluate_admission(spec, evidence, admission_track=track, require_marginal=require_marginal)


def test_full_evidence_is_admitted():
    spec = _spec()
    d = _decide(spec, _full_evidence(spec))
    assert d.approved is True and d.target_status == "在册" and d.blocking_reasons == ()


@pytest.mark.parametrize("override,reason", [
    ({"phase1": {"status": "FAIL"}}, "phase1_failed"),
    ({"phase3": {"verdict": "FAIL"}}, "phase3_failed"),
    ({"nine_gate": {"passed_all": False}}, "nine_gate_not_passed"),
    ({"nine_gate": {}}, "nine_gate_not_passed"),                 # PENDING 也不算通过
    ({"holdout": {"peek_count": 0}}, "holdout_not_single_use"),
    ({"holdout": {"peek_count": 2}}, "holdout_not_single_use"),  # 重复窥视
    ({"experiment_ids": ()}, "evidence_missing"),
])
def test_any_missing_evidence_blocks_active(override, reason):
    spec = _spec()
    d = _decide(spec, _full_evidence(spec, **override))
    assert d.approved is False and d.target_status == "候选"
    assert reason in d.blocking_reasons


def test_spec_hash_mismatch_blocks():
    spec = _spec()
    d = _decide(spec, _full_evidence(spec, spec_hash="deadbeef"))
    assert d.approved is False and "spec_hash_mismatch" in d.blocking_reasons


def test_marginal_required_but_failed_blocks():
    spec = _spec()
    d = _decide(spec, _full_evidence(spec, marginal={"verdict": "FAIL"}), track="diversifier",
                require_marginal=True)
    assert d.approved is False and "marginal_failed" in d.blocking_reasons


def test_marginal_not_required_passes_without_it():
    spec = _spec()
    d = _decide(spec, _full_evidence(spec, marginal={}), require_marginal=False)
    assert d.approved is True


def test_invalid_admission_track_blocks():
    spec = _spec()
    d = _decide(spec, _full_evidence(spec), track="freestyle")
    assert d.approved is False and "invalid_admission_track" in d.blocking_reasons


def test_multiple_failures_all_reported():
    spec = _spec()
    d = _decide(spec, _full_evidence(spec, phase1={"status": "FAIL"}, experiment_ids=()))
    assert {"phase1_failed", "evidence_missing"} <= set(d.blocking_reasons)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
