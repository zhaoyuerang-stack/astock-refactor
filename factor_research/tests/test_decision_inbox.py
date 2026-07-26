"""Decision-Inbox / Daily-Brief 对抗性测试。

Run:
    cd factor_research && python3 tests/test_decision_inbox.py

诚实不变量(护栏 C:不只 happy-path,守卫必须真拒):
- 在册 FAILED / 部署 fail-closed 必须以 blocked 事项入箱(想瞒瞒不住);
- 任一事实源抛异常 → source_error 显式入箱 + all_sources_readable=False,
  headline 禁称「无需介入」(盲区不得宣称无事);
- 空箱只在全源可读时宣称健康;info 级常设建议不得制造虚假紧迫感;
- review 队列超上限必须聚合计数,不静默截断;
- brief 的 trust banner 与权威 trust_calibration 完全一致(透传禁更绿)。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contracts.views import (
    DataQualityView,
    DeclaredLeg,
    GateVerdict,
    PromotionReadinessView,
    SystemTruthView,
)
from services.read.decision_inbox import _REVIEW_ITEM_CAP, get_decision_inbox

# ── 注入夹具 ────────────────────────────────────────────────────────────

def _dq(verdict="可用", severe=0):
    return DataQualityView(total=100, clean=100, clean_ratio=1.0,
                           verdict=verdict, severe_count=severe)


def _healthy_kwargs(**overrides):
    """全源可读且无待裁决的基线注入;单测逐项替换制造对抗场景。"""
    base = dict(
        gate_verdicts=[],
        system_truth=SystemTruthView(declared_present=False),
        review_pending=[],
        decay=None,  # 显式「无报告」(报告缺失属信任缺口,归 trust_calibration)
        data_quality_view=_dq(),
        promotion=PromotionReadinessView(),
    )
    base.update(overrides)
    return base


class _Boom(list):
    """切片即炸的假事实源:模拟源读取中途失败。"""
    def __getitem__(self, item):  # noqa: D105
        raise RuntimeError("source exploded")


# ── 对抗:该拦的必须拦 ──────────────────────────────────────────────────

def test_registered_failed_must_be_blocked_item():
    v = get_decision_inbox(**_healthy_kwargs(gate_verdicts=[
        GateVerdict(family="fam-x", version="v1.0", stage="在册",
                    verdict="FAILED", verdict_label="未通过",
                    register_blocker="G8 DSR 不显著", dsr_p=0.3),
    ]))
    item = next(i for i in v.items if i.kind == "registered_failed")
    assert item.severity == "blocked"
    assert "fam-x/v1.0" in item.title
    assert v.pending_count >= 1
    assert "裁决" in v.headline and "无需你介入" not in v.headline
    # 权威可追溯:authority 必须指向 decide_nine_gate,不是本视图自判
    assert "decide_nine_gate" in item.authority


def test_candidate_failed_is_not_registered_item():
    """非在册(候选/参考)FAILED 不属「在册矛盾」事项——不制造假紧迫。"""
    v = get_decision_inbox(**_healthy_kwargs(gate_verdicts=[
        GateVerdict(family="fam-y", version="v1.0", stage="候选", verdict="FAILED"),
    ]))
    assert not [i for i in v.items if i.kind == "registered_failed"]


def test_deployment_fail_closed_must_surface_with_root_cause():
    truth = SystemTruthView(
        declared_present=True, verified=False,
        declared_deployment_id="deploy-1",
        declared_legs=[DeclaredLeg(family="illiquidity", version="v3.1")],
        verify_error="registry status 参考 not deployable",
    )
    v = get_decision_inbox(**_healthy_kwargs(system_truth=truth))
    item = next(i for i in v.items if i.kind == "deployment")
    assert item.severity == "blocked"
    assert any("not deployable" in e for e in item.evidence)  # 根因原样透出


def test_deployment_verified_produces_no_item():
    truth = SystemTruthView(declared_present=True, verified=True)
    v = get_decision_inbox(**_healthy_kwargs(system_truth=truth))
    assert not [i for i in v.items if i.kind == "deployment"]


def test_review_overflow_is_counted_not_silently_truncated():
    pending = [{"fingerprint": f"fp{i:03d}", "candidate": "x", "decision": "promote"}
               for i in range(_REVIEW_ITEM_CAP + 5)]
    v = get_decision_inbox(**_healthy_kwargs(review_pending=pending))
    cards = [i for i in v.items if i.kind == "review" and i.key != "review:overflow"]
    overflow = next(i for i in v.items if i.key == "review:overflow")
    assert len(cards) == _REVIEW_ITEM_CAP
    assert str(5) in overflow.title  # 截断数量显式可见


def test_decay_red_requires_human_disposition():
    decay = {"status": "red", "as_of_date": "2026-07-01",
             "strategies": [{"strategy": "s1", "decayed": True}]}
    v = get_decision_inbox(**_healthy_kwargs(decay=decay))
    item = next(i for i in v.items if i.kind == "decay")
    assert item.severity == "attention"
    assert "s1" in " ".join(item.evidence)


def test_decay_green_and_missing_report_produce_no_item():
    green = {"status": "green", "strategies": []}
    assert not [i for i in get_decision_inbox(**_healthy_kwargs(decay=green)).items
                if i.kind == "decay"]
    assert not [i for i in get_decision_inbox(**_healthy_kwargs(decay=None)).items
                if i.kind == "decay"]


def test_data_verdict_escalates_severity():
    bad = get_decision_inbox(**_healthy_kwargs(data_quality_view=_dq("不建议回测", severe=99)))
    warn = get_decision_inbox(**_healthy_kwargs(data_quality_view=_dq("关注", severe=3)))
    assert next(i for i in bad.items if i.kind == "data").severity == "blocked"
    assert next(i for i in warn.items if i.kind == "data").severity == "attention"


# ── 对抗:盲区不得宣称无事(fail-closed 空箱三态) ───────────────────────

def test_source_error_surfaces_and_forbids_all_clear():
    v = get_decision_inbox(**_healthy_kwargs(review_pending=_Boom()))
    err = next(i for i in v.items if i.kind == "source_error")
    assert v.all_sources_readable is False
    assert err.severity == "attention"
    assert "source exploded" in " ".join(err.evidence)  # 异常不静默,原样入证据
    assert "无需你介入" not in v.headline  # 盲区禁全绿


def test_empty_inbox_is_healthy_only_when_all_sources_readable():
    v = get_decision_inbox(**_healthy_kwargs())
    assert v.pending_count == 0
    assert v.all_sources_readable is True
    assert "无需你介入" in v.headline


def test_info_steer_does_not_fake_urgency():
    """常设研究重心建议(info)不计入待裁决数,headline 仍可宣称无事。"""
    promo = PromotionReadinessView(lead_candidate="fam-z/v1.0",
                                   lead_blocker="G8 DSR 不显著",
                                   research_steer="换信息源")
    v = get_decision_inbox(**_healthy_kwargs(promotion=promo))
    steer = next(i for i in v.items if i.kind == "steer")
    assert steer.severity == "info"
    assert v.pending_count == 0
    assert "无需你介入" in v.headline


def test_ordering_blocked_before_attention_before_info():
    v = get_decision_inbox(**_healthy_kwargs(
        gate_verdicts=[GateVerdict(family="f", version="v", stage="在册", verdict="FAILED")],
        review_pending=[{"fingerprint": "fp1", "candidate": "x", "decision": "promote"}],
        promotion=PromotionReadinessView(lead_candidate="a/v1"),
    ))
    sev = [i.severity for i in v.items]
    assert sev == sorted(sev, key={"blocked": 0, "attention": 1, "info": 2}.get)
    assert sev[0] == "blocked"


def test_actions_are_advisory_with_canonical_entrypoints():
    """actions 只导航不执行:每个动作必须给 canonical 入口(R-LLM-001/ADR-030)。"""
    v = get_decision_inbox(**_healthy_kwargs(
        review_pending=[{"fingerprint": "fpA", "candidate": "x", "decision": "promote"}]))
    item = next(i for i in v.items if i.kind == "review")
    assert item.actions
    assert all(a.entrypoint for a in item.actions)


# ── Daily Brief:透传禁更绿 + 诚实降级 ──────────────────────────────────

def test_brief_trust_banner_is_verbatim_passthrough():
    from services.read.daily_brief import get_daily_brief
    from services.read.trust_calibration import get_trust_calibration

    brief = get_daily_brief()
    trust = get_trust_calibration()
    assert brief.trust_banner_status == trust.banner_status  # 原样透传,禁更绿/更红
    assert brief.trust_headline == trust.headline


def test_brief_decision_count_matches_live_inbox():
    from services.read.daily_brief import get_daily_brief

    brief = get_daily_brief()
    live = get_decision_inbox()
    assert brief.decision_count == live.pending_count
    assert len(brief.top_decisions) <= 3
    for i in brief.top_decisions:
        assert i.severity in ("blocked", "attention")  # 预览只放真待裁决,不放 info


def test_brief_world_state_sections_are_honest():
    from services.read.daily_brief import get_daily_brief

    ws = get_daily_brief().world_state
    for key in ("data", "decay", "paper"):
        assert key in ws and "status" in ws[key]
        # 不允许无 status 依据的伪 ok:非 ok 状态必须带 error/note 说明
        if ws[key]["status"] not in ("ok",):
            assert ws[key].get("error") or ws[key].get("note")
    if not (ROOT / "reports" / "decay_status.json").exists():
        assert ws["decay"]["status"] == "unmonitored"


def test_live_inbox_shape_and_domain():
    v = get_decision_inbox()
    assert v.headline
    assert v.pending_count == sum(1 for i in v.items if i.severity in ("blocked", "attention"))
    assert all(i.severity in ("blocked", "attention", "info") for i in v.items)
    assert v.honesty


if __name__ == "__main__":
    test_registered_failed_must_be_blocked_item()
    test_candidate_failed_is_not_registered_item()
    test_deployment_fail_closed_must_surface_with_root_cause()
    test_deployment_verified_produces_no_item()
    test_review_overflow_is_counted_not_silently_truncated()
    test_decay_red_requires_human_disposition()
    test_decay_green_and_missing_report_produce_no_item()
    test_data_verdict_escalates_severity()
    test_source_error_surfaces_and_forbids_all_clear()
    test_empty_inbox_is_healthy_only_when_all_sources_readable()
    test_info_steer_does_not_fake_urgency()
    test_ordering_blocked_before_attention_before_info()
    test_actions_are_advisory_with_canonical_entrypoints()
    test_brief_trust_banner_is_verbatim_passthrough()
    test_brief_decision_count_matches_live_inbox()
    test_brief_world_state_sections_are_honest()
    test_live_inbox_shape_and_domain()
    print("decision inbox / daily brief tests passed")
