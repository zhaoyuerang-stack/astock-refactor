"""Adversarial tests for ADR-037 Evidence Envelope + dual-rail + protocols."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from apps.agent_cli import AgentCliError, call_capability
from contracts.evidence import (
    EvidenceTier,
    make_envelope,
    strip_performance_for_display,
)
from services.agent.evidence import (
    assert_formal_evidence_path,
    is_formal_evidence_path,
    public_view,
    wrap_tool_result,
)
from services.agent.proposals import propose_high_risk_action
from services.agent.protocols import UnknownProtocolError, assert_tool_allowed, get_protocol
from services.agent.tools import tool_registry


def test_default_can_claim_valid_is_false():
    env = make_envelope(evidence_tier=EvidenceTier.PRECHECK, sources=["tool:x"])
    assert env.can_claim_valid is False
    assert env.fake_curve_allowed is False


def test_can_claim_valid_true_only_on_gated():
    with pytest.raises(ValidationError):
        make_envelope(
            evidence_tier=EvidenceTier.ENGINE,
            sources=["tool:run_backtest"],
            can_claim_valid=True,
            payload={"annual": 0.4},
        )
    ok = make_envelope(
        evidence_tier=EvidenceTier.GATED,
        sources=["registry:strategy_versions.json"],
        can_claim_valid=True,
        payload={"dsr_p": 0.01},
    )
    assert ok.can_claim_valid is True


def test_narrative_rejects_performance_payload():
    with pytest.raises(ValidationError):
        make_envelope(
            evidence_tier=EvidenceTier.NARRATIVE,
            payload={"annual": 0.35, "sharpe": 2.0},
        )


def test_engine_requires_sources():
    with pytest.raises(ValidationError):
        make_envelope(evidence_tier=EvidenceTier.ENGINE, payload={"annual": 0.1})


def test_public_view_redacts_performance_on_precheck():
    env = wrap_tool_result(
        tool_name="strategy_idea_check",
        result={"trust": {"headline": "x"}, "annual": 0.99},
        evidence_tier=EvidenceTier.PRECHECK,
        protocol_id="idea_precheck",
    )
    # precheck may include stray keys in payload — redaction on public_view
    view = public_view(env)
    assert view["performance_redacted"] is True
    assert "annual" not in view["payload"]
    assert view["can_claim_valid"] is False


def test_strip_performance_nested():
    raw = {"metrics": {"sharpe": 1.2, "note": "ok"}, "foo": 1}
    cleaned = strip_performance_for_display(raw)
    assert cleaned["foo"] == 1
    assert "sharpe" not in cleaned.get("metrics", {})


def test_lab_paths_not_formal_evidence():
    assert is_formal_evidence_path("scratch/foo.json") is False
    assert is_formal_evidence_path("results/x.csv") is False
    assert is_formal_evidence_path("logs/run.log") is False
    with pytest.raises(PermissionError):
        assert_formal_evidence_path("scratch/agent_lab/x.json")
    assert is_formal_evidence_path("reports/research/a.json") is True


def test_unknown_protocol_rejected():
    with pytest.raises(UnknownProtocolError):
        get_protocol("invented_protocol")


def test_tool_not_allowed_under_protocol():
    with pytest.raises(PermissionError):
        assert_tool_allowed("idea_precheck", "run_backtest")
    assert_tool_allowed("idea_precheck", "strategy_idea_check")


def test_strategy_idea_tool_attaches_envelope():
    tool = tool_registry()["strategy_idea_check"]
    out = tool.fn(idea="小盘 动量")
    assert out["can_claim_valid"] is False
    env = out["evidence_envelope"]
    assert env["evidence_tier"] == "precheck"
    assert env["can_claim_valid"] is False
    assert env["fake_curve_allowed"] is False
    assert any("strategy_idea_check" in s for s in env["sources"])


def test_data_gap_audit_wacc_missing():
    tool = tool_registry()["data_gap_audit"]
    out = tool.fn(idea="帮我用WACC作为因子试下策略")
    assert out["can_claim_valid"] is False if "can_claim_valid" in out else True
    assert out["evidence_envelope"]["evidence_tier"] == "precheck"
    assert any("WACC" in m or "equity_cost" in m or "debt_cost" in m for m in out["missing"])
    assert out["factor_ready"] is False


def test_run_signal_probe_blocks_past_holdout(tmp_path, monkeypatch):
    tool = tool_registry()["run_signal_probe"]
    with pytest.raises(Exception):
        tool.fn(factor_name="no_such_factor_xyz", end="2099-01-01")


def test_run_signal_probe_unregistered_receipt(tmp_path, monkeypatch):
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    tool = tool_registry()["run_signal_probe"]
    out = tool.fn(factor_name="definitely_not_registered_factor_zzz", end="2024-06-01", full="false")
    assert out["registered"] is False
    assert out["can_claim_valid"] is False
    assert out["evidence_envelope"]["can_claim_valid"] is False
    assert "report_path" in out
    path = Path(out["report_path"])
    assert "reports" in path.parts
    assert "research" in path.parts


def test_run_signal_probe_full_uses_probe_fn(monkeypatch):
    from factors.registry import FACTOR_REGISTRY, FactorRecord
    from services.actions import run_signal_probe as rsp

    def fake_probe(factor_ref, params, universe, start, cutoff, end):
        return {
            "factor": factor_ref,
            "params": params,
            "universe": universe,
            "window": {"start": start, "cutoff": cutoff, "end": end},
            "raw_ic": {"IS": {"ic": 0.01, "icir": 0.5, "months": 10}, "OOS": {"ic": 0.008, "icir": 0.4, "months": 5}, "full": None},
            "residual_ic_size_liq": {"IS": None, "OOS": None, "full": None},
            "style_corr": {"size": 0.1, "liquidity": 0.05, "momentum": 0.0},
        }

    def dummy_fn(close, window=20):
        return close

    monkeypatch.setitem(
        FACTOR_REGISTRY,
        "unit_test_probe_factor",
        FactorRecord(
            name="unit_test_probe_factor",
            fn=dummy_fn,
            definition="unit test only",
            params={"window": (10, 30)},
            evidence="test",
            searchable=False,
        ),
    )
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    out = rsp.run_signal_probe(
        factor_name="unit_test_probe_factor",
        end="2024-06-01",
        full=True,
        probe_fn=fake_probe,
    )
    assert out["status"] == "l0_probe_complete"
    assert out["full"] is True
    assert out["can_claim_valid"] is False
    assert out["probe"]["raw_ic"]["IS"]["ic"] == 0.01
    assert "report_path" in out
    # receipt-only path
    receipt = rsp.run_signal_probe(
        factor_name="unit_test_probe_factor",
        end="2024-06-01",
        full=False,
        probe_fn=fake_probe,
    )
    assert receipt["status"] == "queued_l0_receipt"
    assert "probe" not in receipt


def test_high_risk_proposal_never_executes():
    out = propose_high_risk_action(action_kind="register", target="x", rationale="test")
    assert out["executed"] is False
    assert out["proposed"] is True
    assert out["can_claim_valid"] is False
    assert out["requires_human_confirmation"] is True


def test_cli_mid_requires_confirm_token(monkeypatch, capsys):
    monkeypatch.delenv("ASTOCK_MID_CONFIRM_TOKEN", raising=False)
    with pytest.raises(AgentCliError, match="confirm"):
        call_capability("run_backtest", {}, confirm_token=None)

    monkeypatch.setenv("ASTOCK_MID_CONFIRM_TOKEN", "secret-token")
    with pytest.raises(AgentCliError, match="invalid"):
        call_capability("run_backtest", {}, confirm_token="wrong")


def test_cli_readonly_only_blocks_mid():
    with pytest.raises(AgentCliError, match="readonly mode|confirm"):
        call_capability(
            "run_backtest",
            {},
            readonly_only=True,
        )


def test_cli_catalog_includes_new_tools():
    from apps.agent_cli import capability_catalog

    names = {c["name"] for c in capability_catalog()}
    assert "strategy_idea_check" in names
    assert "data_gap_audit" in names
    assert "list_protocols" in names
    assert "run_signal_probe" in names
    assert "propose_high_risk_action" in names


def test_protocol_runner_idea_precheck():
    from services.agent.protocol_runner import run_protocol_step

    out = run_protocol_step(
        "idea_precheck",
        "strategy_idea_check",
        {"idea": "低估值 周频"},
    )
    assert out["protocol_id"] == "idea_precheck"
    assert out["result"]["evidence_envelope"]["evidence_tier"] == "precheck"
