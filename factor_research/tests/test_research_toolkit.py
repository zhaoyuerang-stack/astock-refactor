"""Standalone strategy research/control-rule toolkit tests.

Run:
    cd factor_research && python3 tests/test_research_toolkit.py
"""
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from research_toolkit import (
    ArtifactType,
    ControlArtifact,
    HostSpec,
    MarginalReport,
    TriageDecision,
    apply_veto_filter,
    compute_marginal_report,
    route_failed_candidate,
)


def _toy_returns():
    dates = pd.date_range("2024-01-01", periods=80, freq="B")
    base = pd.Series([0.001] * 80, index=dates)
    controlled = base.copy()
    controlled.iloc[20:40] += 0.002
    base_detail = pd.DataFrame({"turnover": [0.2] * 80, "cost": [0.001] * 80}, index=dates)
    controlled_detail = pd.DataFrame({"turnover": [0.25] * 80, "cost": [0.0012] * 80}, index=dates)
    return base, controlled, base_detail, controlled_detail


def test_control_artifact_models_veto_as_host_scoped_policy():
    artifact = ControlArtifact.veto_filter(
        artifact_id="loser_veto_reversal",
        name="输家端反转低波否决器",
        host=HostSpec(family="small-cap-size", version="v3.0"),
        veto_q=0.10,
        hypothesis="排除死亡分位,不独立做多。",
    )
    assert artifact.artifact_type == ArtifactType.VETO_FILTER
    assert artifact.host.family == "small-cap-size"
    assert artifact.config["veto_q"] == 0.10
    assert artifact.registry_status == "条件假设/观察"
    assert artifact.has_independent_nav is False
    print("✅ toolkit models VetoFilter as host-scoped policy")


def test_apply_veto_filter_filters_candidate_pool_and_refills():
    host_scores = pd.Series({"A": 5.0, "B": 4.0, "C": 3.0, "D": 2.0, "E": 1.0})
    veto_scores = pd.Series({"A": 0.0, "B": 0.4, "C": 0.5, "D": 0.6, "E": 0.7})
    selected = apply_veto_filter(host_scores, veto_scores, top_n=3, veto_q=0.20)
    assert list(selected.index) == ["B", "C", "D"]
    assert abs(selected.sum() - 1.0) < 1e-12
    print("✅ toolkit veto filter refills from survivors")


def test_compute_marginal_report_outputs_deltas_not_independent_nav():
    base, controlled, base_detail, controlled_detail = _toy_returns()
    report = compute_marginal_report(
        base_returns=base,
        controlled_returns=controlled,
        base_detail=base_detail,
        controlled_detail=controlled_detail,
        artifact_id="loser_veto_reversal",
        host=HostSpec(family="small-cap-size", version="v3.0"),
    )
    assert isinstance(report, MarginalReport)
    assert report.summary["delta_annual"] > 0
    assert report.summary["delta_cost_annual"] > 0
    payload = report.to_dict()
    assert payload["artifact_type"] == "VetoFilter"
    assert "controlled_returns" not in payload
    assert "independent_nav" not in payload
    assert "2024" in payload["yearly"]
    print("✅ toolkit marginal report exposes deltas only")


def test_route_failed_candidate_sends_strong_l1_death_to_veto_review():
    routed = route_failed_candidate(
        l0_icir=-0.72,
        l1_decision="discard",
        l1_reason="annual=-8.0% < gate",
        threshold=0.5,
    )
    ignored = route_failed_candidate(
        l0_icir=-0.2,
        l1_decision="discard",
        l1_reason="annual=-8.0% < gate",
        threshold=0.5,
    )
    assert routed == TriageDecision.VETO_REVIEW
    assert ignored == TriageDecision.IGNORE
    print("✅ toolkit routes strong dead candidates to veto review")


if __name__ == "__main__":
    test_control_artifact_models_veto_as_host_scoped_policy()
    test_apply_veto_filter_filters_candidate_pool_and_refills()
    test_compute_marginal_report_outputs_deltas_not_independent_nav()
    test_route_failed_candidate_sends_strong_l1_death_to_veto_review()
    print("\n🎉 Research toolkit tests passed!")
