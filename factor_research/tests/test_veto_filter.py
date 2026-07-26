"""VetoFilter mechanics tests.

Run:
    cd factor_research && python3 tests/test_veto_filter.py
"""
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from factors.illiquidity_components import salience_covariance_score
from factors.veto import loser_veto_reversal, salience_covariance_veto
from factory.lines.line2_validation.veto_triage import should_route_to_veto_review
from factory.ontology import Decision, Experiment, ExperimentProtocol, ExperimentResult
from policy.candidate_filters import loser_reversal_filter
from scripts.research.veto_filter_marginal import (
    register_loser_veto_observation,
    run_marginal_veto_protocol,
    summarize_marginal,
)
from strategies.small_cap import build_rebalance_weights


def _toy_panels():
    dates = pd.date_range("2024-01-01", periods=130, freq="B")
    cols = list("ABCDE")
    close = pd.DataFrame(
        {
            "A": [10 + i * 0.01 for i in range(len(dates))],
            "B": [10 + i * 0.02 for i in range(len(dates))],
            "C": [10 + i * 0.03 for i in range(len(dates))],
            "D": [10 + i * 0.04 for i in range(len(dates))],
            "E": [10 + i * 0.05 for i in range(len(dates))],
        },
        index=dates,
        dtype=float,
    )
    host_factor = pd.DataFrame(
        {
            "A": [5.0] * len(dates),
            "B": [4.0] * len(dates),
            "C": [3.0] * len(dates),
            "D": [2.0] * len(dates),
            "E": [1.0] * len(dates),
        },
        index=dates,
    )
    veto_factor = pd.DataFrame(
        {
            "A": [0.0] * len(dates),  # death decile candidate
            "B": [0.4] * len(dates),
            "C": [0.5] * len(dates),
            "D": [0.6] * len(dates),
            "E": [0.7] * len(dates),
        },
        index=dates,
    )
    return close, host_factor[cols], veto_factor[cols]


def test_veto_filter_excludes_death_bucket_before_top_n_and_refills():
    close, host_factor, veto_factor = _toy_panels()
    plain = build_rebalance_weights(host_factor, close, top_n=3, rebalance_days=20)
    vetoed = build_rebalance_weights(
        host_factor,
        close,
        top_n=3,
        rebalance_days=20,
        veto_factor=veto_factor,
        veto_q=0.20,
    )
    first_decision = close.index[0]
    assert list(plain[first_decision].index) == ["A", "B", "C"]
    assert list(vetoed[first_decision].index) == ["B", "C", "D"]
    assert abs(vetoed[first_decision].sum() - 1.0) < 1e-12
    assert len(vetoed[first_decision]) == 3
    print("✅ veto excludes death bucket before top_n and refills")


def test_loser_veto_reversal_returns_safe_high_death_low_panel():
    dates = pd.date_range("2024-01-01", periods=80, freq="B")
    close = pd.DataFrame(
        {
            "DEATH": [10.0 - i * 0.01 for i in range(80)],  # persistent loser
            "SAFE": [10.0 + i * 0.04 for i in range(80)],
            "MID": [10.0 + i * 0.01 for i in range(80)],
        },
        index=dates,
    )
    score = loser_veto_reversal(close, lookback=20, vol_window=20)
    last = score.iloc[-1].dropna()
    assert last["DEATH"] < last["MID"] < last["SAFE"]
    print("✅ loser_veto_reversal scores death low and safe high")


def test_marginal_protocol_reports_delta_only_and_yearly_breakdown():
    close, host_factor, veto_factor = _toy_panels()
    report = run_marginal_veto_protocol(
        close=close,
        host_factor=host_factor,
        veto_factor=veto_factor,
        start="2024-01-01",
        windows={"toy": ("2024-01-01", "2024-06-30")},
        top_n=3,
        rebalance_days=20,
        veto_q=0.20,
    )
    summary = summarize_marginal(report["windows"]["toy"])
    assert set(summary) >= {"delta_annual", "delta_maxdd", "delta_turnover_annual", "delta_cost_annual"}
    assert "veto_returns" not in report["windows"]["toy"]
    assert "yearly" in report["windows"]["toy"]
    print("✅ marginal protocol reports deltas only")


def test_register_loser_veto_observation_uses_observation_status_and_host_config():
    with tempfile.TemporaryDirectory() as td:
        import strategy_registry

        old_registry = strategy_registry.REGISTRY
        strategy_registry.REGISTRY = Path(td) / "strategy_versions.json"
        try:
            registered = register_loser_veto_observation(
                host_family="small-cap-size",
                host_version="v3.0",
                metrics={"delta_annual": 0.01, "positive_years": 3, "total_years": 7, "hit": False},
                notes="unit test",
            )
            data = strategy_registry._load()
        finally:
            strategy_registry.REGISTRY = old_registry

    assert registered == "loser_veto_reversal/v0.1-observe"
    fam = data["families"][0]
    assert fam["id"] == "loser_veto_reversal"
    assert fam["status"] == "paused"
    version = fam["versions"][0]
    assert version["status"] == "条件假设/观察"
    assert version["config"]["artifact_type"] == "VetoFilter"
    assert version["config"]["host"] == {"family": "small-cap-size", "version": "v3.0"}
    print("✅ registry records VetoFilter as host-scoped observation")


def test_l1_discard_with_strong_l0_routes_to_veto_review():
    l0 = Experiment(
        experiment_id="l0",
        hypothesis_id="hyp",
        protocol=ExperimentProtocol.L0_IC_SCAN,
        vintage_id="vintage",
        result=ExperimentResult(metrics={"ICIR": -0.7}, details={"ic_ir": -0.7}),
        decision=Decision.PROMOTE,
    )
    l1_dead = Experiment(
        experiment_id="l1",
        hypothesis_id="hyp",
        protocol=ExperimentProtocol.L1_QUICK_BT,
        vintage_id="vintage",
        result=ExperimentResult(metrics={"annual": -0.2}, details={"decision_reason": "annual failed"}),
        decision=Decision.DISCARD,
    )
    l1_alive = Experiment(
        experiment_id="l1b",
        hypothesis_id="hyp",
        protocol=ExperimentProtocol.L1_QUICK_BT,
        vintage_id="vintage",
        result=ExperimentResult(metrics={"annual": 0.2}, details={}),
        decision=Decision.PROMOTE,
    )
    assert should_route_to_veto_review(l0, l1_dead, icir_threshold=0.5) is True
    assert should_route_to_veto_review(l0, l1_alive, icir_threshold=0.5) is False

    weak_l0 = Experiment(
        experiment_id="l0w",
        hypothesis_id="hyp",
        protocol=ExperimentProtocol.L0_IC_SCAN,
        vintage_id="vintage",
        result=ExperimentResult(metrics={"ICIR": -0.2}, details={"ic_ir": -0.2}),
        decision=Decision.PROMOTE,
    )
    assert should_route_to_veto_review(weak_l0, l1_dead, icir_threshold=0.5) is False
    print("✅ strong L0 plus dead L1 routes to veto review")


def test_pipeline_marks_veto_review_candidate_on_l1_death():
    from factory.autoresearch import CandidateRepository, ExperimentLog, ReviewQueue
    from factory.autoresearch.generator import generate_seed_candidates
    from factory.autoresearch.pipeline import run_validation_pipeline

    def fake(protocol, decision, *, icir=None):
        def _run(hyp, *args, **kwargs):
            details = {"direction": "long"}
            if icir is not None:
                details["ic_ir"] = icir
            return Experiment(
                experiment_id=f"fake-{protocol.value}",
                hypothesis_id=hyp.id,
                protocol=protocol,
                vintage_id=kwargs.get("vintage_id", "test-vintage"),
                result=ExperimentResult(metrics={}, details=details),
                decision=decision,
                notes="annual failed" if decision == Decision.DISCARD else "pass",
            )
        return _run

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        candidate = next(iter(generate_seed_candidates(limit=1)))
        result = run_validation_pipeline(
            candidate,
            close=pd.DataFrame(),
            volume=pd.DataFrame(),
            amount=pd.DataFrame(),
            forward_ret=pd.DataFrame(),
            vintage_id="test-vintage",
            repository=CandidateRepository(root / "candidates.jsonl"),
            experiment_log=ExperimentLog(root / "experiment_log.jsonl"),
            review_queue=ReviewQueue(root / "review_queue.jsonl"),
            runners={
                "l0": fake(ExperimentProtocol.L0_IC_SCAN, Decision.PROMOTE, icir=-0.7),
                "l1": fake(ExperimentProtocol.L1_QUICK_BT, Decision.DISCARD),
            },
            max_stage="l3",
        )
    assert result.metrics["veto_review_candidate"] is True
    assert "VetoFilter" in result.reason
    print("✅ pipeline marks dead-but-informative candidates for veto review")


def test_loser_reversal_filter_matches_legacy_veto_score():
    dates = pd.date_range("2024-01-01", periods=80, freq="B")
    close = pd.DataFrame(
        {
            "DEATH": [10.0 - i * 0.01 for i in range(80)],
            "SAFE": [10.0 + i * 0.04 for i in range(80)],
            "MID": [10.0 + i * 0.01 for i in range(80)],
        },
        index=dates,
    )
    pd.testing.assert_frame_equal(
        loser_reversal_filter(close, lookback=20, vol_window=20),
        loser_veto_reversal(close, lookback=20, vol_window=20),
    )


def test_salience_covariance_score_matches_legacy_veto_component():
    dates = pd.date_range("2024-01-01", periods=60, freq="B")
    close = pd.DataFrame(
        {
            "A": [10.0 + i * 0.02 for i in range(60)],
            "B": [12.0 - i * 0.01 for i in range(60)],
            "C": [8.0 + i * 0.03 for i in range(60)],
        },
        index=dates,
    )
    pd.testing.assert_frame_equal(
        salience_covariance_score(close),
        salience_covariance_veto(close),
    )


if __name__ == "__main__":
    test_veto_filter_excludes_death_bucket_before_top_n_and_refills()
    test_loser_veto_reversal_returns_safe_high_death_low_panel()
    test_marginal_protocol_reports_delta_only_and_yearly_breakdown()
    test_register_loser_veto_observation_uses_observation_status_and_host_config()
    test_l1_discard_with_strong_l0_routes_to_veto_review()
    test_pipeline_marks_veto_review_candidate_on_l1_death()
    test_loser_reversal_filter_matches_legacy_veto_score()
    test_salience_covariance_score_matches_legacy_veto_component()
    print("\n🎉 VetoFilter tests passed!")
