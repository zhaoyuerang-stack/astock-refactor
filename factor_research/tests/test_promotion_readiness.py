"""Alpha 工厂「晋级就绪」读层:门距/卡点由扁平字段派生,权威裁决归 decide_nine_gate,
诚实护栏(未审计不伪造门距、拥挤度无数据记 None、排序按距入册非收益)。"""
import pytest

from contracts.views import PromotionReadinessView
from services.read.promotion_readiness import (
    _assess,
    _crowding,
    _derive_gates,
    _marginal_action,
    get_promotion_readiness,
)

GOOD_NG = {  # 一个"全过"的扁平 nine_gate
    "passed_all": True, "dsr_significant": True, "dsr_p": 0.01, "pbo": 0.1,
    "nw_icir": 1.2, "icir_retention": 0.9, "wf_sharpe": 1.1, "bear_sharpe": 0.3,
    "capacity_limit_aum": 5e7, "n_trials": 12,
}
TOP = {"version": "v1.0", "status": "候选", "desc": "x", "config": {"a": 1}, "data_scope": {"s": "lake"}}


# ---- _derive_gates:逐门诊断派生 ----

def test_derive_gates_count_and_status():
    gates = _derive_gates(GOOD_NG, TOP)
    assert len(gates) == 9
    by = {g.gate: g for g in gates}
    assert by["G8_DSR"].status == "passed"      # dsr_significant True
    assert by["G2_IC"].status == "passed"       # nw_icir>0
    assert by["G9_MATERIAL"].status == "passed"  # desc+config 齐全
    # source_field 可追溯
    assert by["G8_DSR"].source_field


def test_derive_gates_failed_and_unknown():
    ng = {"dsr_significant": False, "nw_icir": -0.1}  # 其余字段缺失
    gates = {g.gate: g for g in _derive_gates(ng, {"desc": "", "config": None})}
    assert gates["G8_DSR"].status == "failed"
    assert gates["G2_IC"].status == "failed"     # nw_icir<0
    assert gates["G6_CAPACITY"].status == "unknown"  # 字段缺失 → 不臆测通过
    assert gates["G9_MATERIAL"].status == "failed"   # 缺 desc/config


def test_derive_gates_dsr_falls_back_to_p_when_significant_absent():
    g = {x.gate: x for x in _derive_gates({"dsr_p": 0.2}, TOP)}
    assert g["G8_DSR"].status == "failed"        # 无 dsr_significant,按 p≥0.05 判 failed
    g2 = {x.gate: x for x in _derive_gates({"dsr_p": 0.01}, TOP)}
    assert g2["G8_DSR"].status == "passed"


# ---- _crowding:家族拥挤度 ----

def test_crowding_from_corr_matrix():
    corr = {"illiquidity": {"illiquidity": 1.0, "small_cap_size": 0.82, "gov_bond": -0.1}}
    crowd, cluster = _crowding("illiquidity", corr)
    assert crowd == pytest.approx(0.82)
    assert "small_cap_size" in cluster          # >0.7 同簇

def test_crowding_unknown_when_family_absent():
    crowd, cluster = _crowding("ghost-family", {"illiquidity": {"illiquidity": 1.0}})
    assert crowd is None and "未知" in cluster


# ---- _marginal_action:启发式 advisory ----

def test_marginal_action_crowded_dsr_recommends_new_source():
    a = _marginal_action("FAILED", "G8 DSR 多重检验不显著", crowd=0.9)
    assert "换信息源" in a
    a2 = _marginal_action("FAILED", "G8 DSR 多重检验不显著", crowd=0.2)
    assert "样本外显著性" in a2 and "换信息源" not in a2

def test_marginal_action_passed_is_register_ready():
    assert "入册" in _marginal_action("PASSED", "", crowd=None)


# ---- _assess:权威主导 headline,诚实护栏 ----

def test_assess_never_audited_max_distance():
    c = _assess("some-fam", {"version": "v1.0", "status": "参考", "nine_gate": {}}, {})
    assert c.authoritative_verdict == "PENDING"   # 空 nine_gate → 未跑完整
    assert c.audited is False
    assert c.distance_to_register == 9            # 不伪造门距
    assert "未审计" in c.single_blocker

def test_assess_passed_all_true_is_register_ready():
    c = _assess("f", {"version": "v2.0", "status": "候选", "nine_gate": GOOD_NG, "desc": "x", "config": {"a": 1}, "data_scope": {}}, {})
    assert c.authoritative_verdict == "PASSED"
    assert c.distance_to_register == 0
    assert c.single_blocker == ""                 # 已就绪

def test_assess_failed_dsr_blocker_from_authority():
    ng = {"passed_all": False, "dsr_significant": False, "dsr_p": 0.37}
    c = _assess("f", {"version": "v1.0", "status": "候选", "nine_gate": ng, "desc": "x", "config": {}}, {})
    assert c.authoritative_verdict == "FAILED"
    assert "DSR" in c.single_blocker              # 取权威 blocking_reasons


# ---- 整合视图:结构不变式 ----

def test_get_promotion_readiness_invariants():
    v = get_promotion_readiness()
    assert isinstance(v, PromotionReadinessView)
    # 排序:距入册升序(决策导向,非按收益)
    dists = [c.distance_to_register for c in v.candidates]
    assert dists == sorted(dists)
    # 池只含 候选/参考
    assert all(c.stage in {"候选", "参考"} for c in v.candidates)
    # 每个候选逐门诊断 9 门;权威=PASSED ⇒ 门距=0(不矛盾)
    for c in v.candidates:
        assert len(c.gate_diag) == 9
        if c.authoritative_verdict == "PASSED":
            assert c.distance_to_register == 0
    # lead 与权威源齐全
    if v.candidates:
        assert v.lead_candidate
    assert v.truth_sources.get("verdict_authority", "").endswith("decide_nine_gate")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
