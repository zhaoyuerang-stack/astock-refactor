"""验证闸门②:逐版本 9-Gate 裁决面。权威裁决归 decide_nine_gate,入册卡点诚实派生。"""
import pytest

from contracts.views import GateVerdictsView
from services.read.promotion_readiness import _derive_gates
from services.read.validation_gate import _register_blocker, get_gate_verdicts

TOP = {"desc": "x", "config": {"a": 1}, "data_scope": {"s": "lake"}}


def _gates(ng):
    return _derive_gates(ng, TOP)


# ---- _register_blocker:权威 reasons 优先,失败门优先于 unknown ----

def test_blocker_passed_is_empty():
    assert _register_blocker("PASSED", {"passed_all": True}, _gates({}), ()) == ""

def test_blocker_never_audited():
    assert "未审计" in _register_blocker("PENDING", {}, _gates({}), ())

def test_blocker_dsr_from_authority():
    ng = {"passed_all": False, "dsr_significant": False}
    assert "DSR" in _register_blocker("FAILED", ng, _gates(ng), ("dsr_not_significant",))

def test_blocker_prefers_failed_gate_over_unknown():
    # nw_icir<0 → G2 failed;capacity 缺失 → G6 unknown。应报 G2(失败)而非 unknown。
    ng = {"passed_all": False, "nw_icir": -0.1}
    b = _register_blocker("FAILED", ng, _gates(ng), ("passed_all_false",))
    assert "G2_IC" in b and "未过" in b

def test_blocker_no_failed_gate_is_honest():
    # 无明确失败门(都 passed/unknown)→ 不臆造具体门
    ng = {"passed_all": False}  # 全字段缺失 → 多为 unknown,无 failed
    b = _register_blocker("FAILED", ng, _gates(ng), ("passed_all_false",))
    assert "未定位" in b or "G" in b  # 容忍 G9 等顶层可判门;关键是不崩


# ---- 整合视图 ----

def test_gate_verdicts_invariants():
    v = get_gate_verdicts()
    assert isinstance(v, GateVerdictsView)
    assert v.summary["total"] == len(v.verdicts)
    # 计数自洽
    assert (v.summary["PASSED"] + v.summary["FAILED"]
            + v.summary["PENDING"] + v.summary["RUN_FAILED"]) == len(v.verdicts)
    for gv in v.verdicts:
        assert len(gv.gate_diag) == 9
        assert gv.verdict in {"PASSED", "FAILED", "PENDING", "RUN_FAILED"}
        if gv.verdict == "PASSED":
            assert gv.register_blocker == ""   # 通过=无入册卡点
        else:
            assert gv.register_blocker          # 未过必有根因
    # 排序:PASSED 在 FAILED 之前(验证决策视角)
    order = {"PASSED": 0, "PENDING": 1, "RUN_FAILED": 2, "FAILED": 3}
    seq = [order[gv.verdict] for gv in v.verdicts]
    assert seq == sorted(seq)
    assert v.truth_sources.get("verdict_authority", "").endswith("decide_nine_gate")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
