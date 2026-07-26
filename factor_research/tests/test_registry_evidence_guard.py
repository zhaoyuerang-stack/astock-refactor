"""check_registry_evidence 守卫的 fixture 测试(检测器吃 dict,不依赖实时台账状态)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci.check_registry_evidence import (
    check,
    extract_versions,
    find_active_without_track,
    find_cross_family_ic_copies,
    find_standalone_evidence_gaps,
    find_understated_trials,
)

IC_A = {"ic_mean": 0.078, "nw_icir": 0.126, "ic_decay": {"1": 0.02}}
IC_B = {"ic_mean": 0.041, "nw_icir": 0.21, "ic_decay": {"1": 0.01}}


def _ledger(families):
    return {"families": families}


def test_cross_family_ic_copy_flagged():
    # 两个不同 family 共享逐位相同 IC 块 → G1 违规
    led = _ledger([
        {"id": "famA", "status": "在册", "versions": [
            {"version": "v1.0", "admission": {"track": "standalone"}, "nine_gate": dict(IC_A, passed_all=True, dsr_p=0.01, pbo=0.2)}]},
        {"id": "famB", "status": "在册", "versions": [
            {"version": "v1.0", "admission": {"track": "standalone"}, "nine_gate": dict(IC_A, passed_all=True, dsr_p=0.01, pbo=0.3)}]},
    ])
    rows = extract_versions(led)
    v = find_cross_family_ic_copies(rows)
    assert len(v) == 1 and "famA" in v[0][1] and "famB" in v[0][1]


def test_within_family_shared_ic_allowed():
    # 同 family 多版本共享 IC 块 → 合法,不报
    led = _ledger([
        {"id": "famA", "status": "在册", "versions": [
            {"version": "v1.0", "admission": {"track": "standalone"}, "nine_gate": dict(IC_A, dsr_p=0.01, pbo=0.2)},
            {"version": "v1.1", "admission": {"track": "standalone"}, "nine_gate": dict(IC_A, dsr_p=0.02, pbo=0.2)}]},
    ])
    assert find_cross_family_ic_copies(extract_versions(led)) == []


def test_empty_evidence_standalone_flagged():
    # active+standalone 但 nine_gate/evidence 皆空 → G2 违规(industry-neglect v1.3 型)
    led = _ledger([
        {"id": "famC", "status": "在册", "versions": [
            {"version": "v1.3", "admission": {"track": "standalone"}, "nine_gate": {}, "evidence": {}}]},
    ])
    v = find_standalone_evidence_gaps(extract_versions(led))
    assert len(v) == 1 and "证据全空" in v[0][1]


def test_passed_all_with_skipped_gate_flagged():
    # passed_all=true 但 pbo=None → G2 跳门违规(illiquidity-large-cap 型)
    led = _ledger([
        {"id": "famD", "status": "在册", "versions": [
            {"version": "v1.0", "admission": {"track": "standalone"},
             "nine_gate": dict(IC_B, passed_all=True, dsr_p=0.01, pbo=None)}]},
    ])
    v = find_standalone_evidence_gaps(extract_versions(led))
    assert len(v) == 1 and "pbo" in v[0][1]


def test_dsr_insignificant_standalone_flagged():
    # active+standalone 但 dsr_p>=0.05 → G3 违规(ADR-020:DSR 多重测试惩罚下不显著)
    led = _ledger([
        {"id": "famG", "status": "在册", "versions": [
            {"version": "v1.0", "admission": {"track": "standalone"},
             "nine_gate": dict(IC_B, dsr_p=0.34)}]},
    ])
    v = find_standalone_evidence_gaps(extract_versions(led))
    assert len(v) == 1 and "DSR不显著" in v[0][1] and v[0][0].startswith("G3-dsr-fail")


def test_dsr_none_standalone_flagged():
    # active+standalone,nine_gate 非空(非 G2-empty)但 dsr_p 缺算 → G3 违规(industry-neglect v1.3 型)
    led = _ledger([
        {"id": "famH", "status": "在册", "versions": [
            {"version": "v1.3", "admission": {"track": "standalone"},
             "nine_gate": dict(IC_B)}]},  # 无 dsr_p
    ])
    v = find_standalone_evidence_gaps(extract_versions(led))
    assert len(v) == 1 and "DSR未实算" in v[0][1] and v[0][0].startswith("G3-dsr-none")


def test_dsr_insignificant_diversifier_not_flagged():
    # diversifier 凭组合边际入册,不受 DSR 约束 → 即便 dsr_p=0.9 也不报
    led = _ledger([
        {"id": "famI", "status": "在册", "versions": [
            {"version": "v1.0", "admission": {"track": "diversifier", "rationale": "负相关"},
             "nine_gate": dict(IC_B, dsr_p=0.9)}]},
    ])
    assert find_standalone_evidence_gaps(extract_versions(led)) == []


def test_understated_trials_composite_flagged():
    # 组合含 3 分量却 n_trials=1 → EV4 低报违规(composite-portfolio v1.0 型),与 status 无关
    led = _ledger([
        {"id": "composite-portfolio", "status": "候选", "versions": [
            {"version": "v1.0", "admission": {"track": None},
             "config": {"allocation": {"a": 0.4, "b": 0.4, "c": 0.2}},
             "nine_gate": {"n_trials": 1, "dsr_p": 0.07}}]},
    ])
    v = find_understated_trials(extract_versions(led))
    assert len(v) == 1 and v[0][0].startswith("EV4-trials:") and "低报" in v[0][1]


def test_trials_at_floor_not_flagged():
    # n_trials 达到分量数下界 → 不报(下界过门;真实自由度仍须 register 如实计)
    led = _ledger([
        {"id": "composite-portfolio", "status": "候选", "versions": [
            {"version": "v1.0", "admission": {"track": None},
             "config": {"allocation": {"a": 0.5, "b": 0.5}},
             "nine_gate": {"n_trials": 2}}]},
    ])
    assert find_understated_trials(extract_versions(led)) == []


def test_no_allocation_not_trials_checked():
    # 非组合版本(无 allocation)→ 不进 EV4 检查,n_trials=1 合法
    led = _ledger([
        {"id": "famX", "status": "候选", "versions": [
            {"version": "v1.0", "admission": {"track": None},
             "config": {}, "nine_gate": {"n_trials": 1}}]},
    ])
    assert find_understated_trials(extract_versions(led)) == []


def test_clean_ledger_passes():
    led = _ledger([
        {"id": "famE", "status": "在册", "versions": [
            {"version": "v1.0", "admission": {"track": "standalone"},
             "nine_gate": dict(IC_B, passed_all=True, dsr_p=0.01, pbo=0.2)}]},
        {"id": "famF", "status": "候选", "versions": [  # 候选无证据 → 不要求,不报
            {"version": "v1.0", "admission": {"track": None}, "nine_gate": {}}]},
    ])
    assert check(led) == 0


def test_new_violation_returns_nonzero():
    # 不在 PENDING_REMEDIATION 基线里的新违规 → check 必须 exit 1(守卫的核心职责)
    led = _ledger([
        {"id": "newFamX", "status": "在册", "versions": [
            {"version": "v1.0", "admission": {"track": "standalone"}, "nine_gate": {}, "evidence": {}}]},
    ])
    assert check(led) == 1


def test_version_active_without_track_flagged():
    """审计 #3 对抗:fixture 台账「在册+无 admission」必被 G2-no-track 抓。"""
    led = _ledger([
        {"id": "famZ", "status": "active", "versions": [
            {"version": "v1.0", "status": "在册", "admission": {}, "nine_gate": {}, "evidence": {}}]},
    ])
    v = find_active_without_track(extract_versions(led))
    assert len(v) == 1 and v[0][0].startswith("G2-no-track:")
    assert check(led) == 1


def test_family_active_without_version_status_not_no_track():
    """家族 status=active 回退不算 version 准入声明 → 不触发 G2-no-track。"""
    led = _ledger([
        {"id": "famY", "status": "active", "versions": [
            # version 无 status 字段 → version_status=None
            {"version": "v1.0", "admission": {}, "nine_gate": {}, "evidence": {}}]},
    ])
    assert find_active_without_track(extract_versions(led)) == []


def test_active_english_status_without_track_flagged():
    """英文 active 同义词作 version status 且无 track → 亦抓(台账污染向量)。"""
    led = _ledger([
        {"id": "famE", "status": "候选", "versions": [
            {"version": "v1.0", "status": "active", "admission": {}, "nine_gate": {}}]},
    ])
    v = find_active_without_track(extract_versions(led))
    assert len(v) == 1 and "G2-no-track" in v[0][0]


def test_live_ledger_passes():
    """真实台账守卫必须绿(或存量全在 PENDING)。"""
    assert check() == 0


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))

