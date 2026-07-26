import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from runtime.production_readiness import (
    build_production_readiness,
    current_paper_status,
    validate_feedback_envelope,
)

IDENTITY = {
    "deployment_id": "prod-a-share-v1",
    "family": "illiquidity",
    "version": "v3.1",
    "spec_hash": "abc123",
}


def _report(now, **overrides):
    report = {
        "report_type": "decay",
        "generated_at": now.isoformat(),
        **IDENTITY,
        "data_fingerprint": "data123",
        "as_of_date": "2026-06-18",
        "status": "green",
    }
    report.update(overrides)
    return report


def test_feedback_identity_mismatch_is_blocked():
    now = datetime(2026, 6, 20, tzinfo=ZoneInfo("Asia/Shanghai"))
    result = validate_feedback_envelope(
        _report(now, spec_hash="wrong"),
        expected=IDENTITY,
        ttl_days=8,
        now=now,
    )
    assert result["valid"] is False
    assert "spec_hash_mismatch" in result["blocking_reasons"]


def test_feedback_staleness_and_missing_data_fingerprint_are_blocked():
    now = datetime(2026, 6, 20, tzinfo=ZoneInfo("Asia/Shanghai"))
    stale = validate_feedback_envelope(
        _report(now - timedelta(days=9)),
        expected=IDENTITY,
        ttl_days=8,
        now=now,
    )
    missing = validate_feedback_envelope(
        _report(now, data_fingerprint=""),
        expected=IDENTITY,
        ttl_days=8,
        now=now,
    )
    assert "feedback_stale" in stale["blocking_reasons"]
    assert "data_fingerprint_missing" in missing["blocking_reasons"]


def test_paper_blocked_fill_and_identity_mismatch_are_blocked(tmp_path):
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "account.json").write_text(json.dumps({
        "last_exec": {
            **IDENTITY,
            "from_signal": "2026-06-18",
            "blocked": [["买入", "000001", "平安银行", "涨停"]],
        }
    }))

    assert current_paper_status(tmp_path, expected=IDENTITY) == "blocked"

    account = json.loads((paper / "account.json").read_text())
    account["last_exec"]["blocked"] = []
    account["last_exec"]["spec_hash"] = "wrong"
    (paper / "account.json").write_text(json.dumps(account))
    assert current_paper_status(tmp_path, expected=IDENTITY) == "identity_mismatch"


def test_unknown_feedback_is_fail_closed():
    result = build_production_readiness(
        data_date="2026-06-18",
        expected_trade_date="2026-06-18",
        governance_status="approved",
        decay_status="unknown",
        paper_status="unknown",
        trading_day_status="trading_day",
        data_issue_status={"status": "unknown", "production_blocked": False},
    )
    assert result.allowed is False
    assert "decay:unknown" in result.blocking_reasons
    assert "paper:unknown" in result.blocking_reasons
    assert "data_issue:unknown" in result.blocking_reasons
