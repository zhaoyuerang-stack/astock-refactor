"""Adversarial isolation tests: Lab/scratch paths never formal evidence (ADR-037).

If any assertion fails against production code, that is a real wash-through
vulnerability — do NOT weaken policy modules to make this suite green.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts.evidence import EvidenceTier, make_envelope
from services.agent.evidence import (
    assert_formal_evidence_path,
    is_formal_evidence_path,
    wrap_tool_result,
)


def test_scratch_lab_path_assert_raises_permission_error():
    with pytest.raises(PermissionError):
        assert_formal_evidence_path("scratch/lab/x.json")


def test_scratch_root_path_assert_raises_permission_error():
    with pytest.raises(PermissionError):
        assert_formal_evidence_path("scratch/x.json")


def test_unknown_path_assert_raises_fail_closed():
    """Positive whitelist: unknown dirs are not formal evidence."""
    with pytest.raises(PermissionError):
        assert_formal_evidence_path("random_dir/out.json")


def test_is_formal_evidence_path_rejects_lab_scratch():
    assert is_formal_evidence_path("scratch/lab/x.json") is False
    assert is_formal_evidence_path("scratch/x.json") is False
    assert is_formal_evidence_path("random_dir/out.json") is False


def test_make_envelope_can_claim_valid_true_only_on_gated():
    with pytest.raises((ValidationError, ValueError)):
        make_envelope(
            evidence_tier=EvidenceTier.ENGINE,
            sources=["tool:run_backtest"],
            can_claim_valid=True,
            payload={"annual": 0.2},
        )
    ok = make_envelope(
        evidence_tier=EvidenceTier.GATED,
        sources=["registry:strategy_versions.json"],
        can_claim_valid=True,
        payload={"dsr_p": 0.01},
    )
    assert ok.can_claim_valid is True
    assert ok.evidence_tier == EvidenceTier.GATED


def test_wrap_tool_result_defaults_can_claim_valid_false():
    env = wrap_tool_result(
        tool_name="strategy_idea_check",
        result={"trust": {"headline": "probe only"}},
        evidence_tier=EvidenceTier.PRECHECK,
        protocol_id="idea_precheck",
    )
    assert env.can_claim_valid is False
    assert env.fake_curve_allowed is False
