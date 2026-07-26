import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _model_dict(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj.dict()


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_blocked_signal_writes_draft_and_preserves_formal_state():
    import run_daily as RD
    from contracts.views import ProductionReadinessView

    signals = Path(tempfile.mkdtemp()) / "signals"
    signals.mkdir(parents=True)
    old_paths = (RD.SIGNALS, RD.STATE_FILE, RD.LAST_REBAL)
    RD.SIGNALS = signals
    RD.STATE_FILE = signals / "state.json"
    RD.LAST_REBAL = signals / "_last_rebalance.txt"
    old_state = {
        "current_position": "cash",
        "last_rebalance_date": None,
        "last_signal_date": "2026-06-17",
        "last_holdings": [],
    }
    try:
        _write_json(RD.STATE_FILE, old_state)
        signal = {"date": "2026-06-18", "action": "建仓买入", "holdings": ["000001"]}
        new_state = {**old_state, "last_signal_date": "2026-06-18", "last_holdings": ["000001"]}
        readiness = ProductionReadinessView(
            allowed=False,
            blocking_reasons=["data_stale", "governance:dsr_pending"],
            warnings=[],
            data_date="2026-06-17",
            expected_trade_date="2026-06-18",
            governance_status="dsr_pending",
            decay_status="normal",
        )

        result = RD.persist_signal_with_readiness(signal, new_state, readiness)

        assert result["published"] is False
        assert not (signals / "2026-06-18.json").exists()
        draft = _read_json(signals / "drafts" / "2026-06-18.json")
        assert draft["production_readiness"]["allowed"] is False
        assert draft["production_readiness"]["blocking_reasons"] == [
            "data_stale",
            "governance:dsr_pending",
        ]
        assert _read_json(RD.STATE_FILE) == old_state
    finally:
        RD.SIGNALS, RD.STATE_FILE, RD.LAST_REBAL = old_paths


def test_allowed_signal_writes_formal_signal_and_state():
    import run_daily as RD
    from contracts.views import ProductionReadinessView

    signals = Path(tempfile.mkdtemp()) / "signals"
    signals.mkdir(parents=True)
    old_paths = (RD.SIGNALS, RD.STATE_FILE, RD.LAST_REBAL)
    RD.SIGNALS = signals
    RD.STATE_FILE = signals / "state.json"
    RD.LAST_REBAL = signals / "_last_rebalance.txt"
    try:
        signal = {"date": "2026-06-18", "action": "空仓观望", "holdings": []}
        new_state = {
            "current_position": "cash",
            "last_rebalance_date": None,
            "last_signal_date": "2026-06-18",
            "last_holdings": [],
        }
        readiness = ProductionReadinessView(
            allowed=True,
            blocking_reasons=[],
            warnings=[],
            data_date="2026-06-18",
            expected_trade_date="2026-06-18",
            governance_status="approved",
            decay_status="normal",
        )

        result = RD.persist_signal_with_readiness(signal, new_state, readiness)

        assert result["published"] is True
        formal = _read_json(signals / "2026-06-18.json")
        assert formal["production_readiness"]["allowed"] is True
        assert _read_json(RD.STATE_FILE)["last_signal_date"] == "2026-06-18"
    finally:
        RD.SIGNALS, RD.STATE_FILE, RD.LAST_REBAL = old_paths


def test_build_production_readiness_blocks_stale_and_governance_failures():
    from runtime.production_readiness import build_production_readiness

    readiness = build_production_readiness(
        data_date="2026-06-17",
        expected_trade_date="2026-06-18",
        governance_status="dsr_pending",
        decay_status="normal",
        paper_status="ok",
        trading_day_status="trading_day",
    )

    assert readiness.allowed is False
    assert "data_stale" in readiness.blocking_reasons
    assert "governance:dsr_pending" in readiness.blocking_reasons

    dsr_failed = build_production_readiness(
        data_date="2026-06-18",
        expected_trade_date="2026-06-18",
        governance_status="dsr_not_significant",
        decay_status="normal",
        paper_status="ok",
        trading_day_status="trading_day",
    )
    assert dsr_failed.allowed is False
    assert "governance:dsr_not_significant" in dsr_failed.blocking_reasons


def test_build_production_readiness_surfaces_deployment_identity_error():
    from runtime.production_readiness import build_production_readiness

    readiness = build_production_readiness(
        data_date="2026-06-18",
        expected_trade_date="2026-06-18",
        governance_status="approved",
        decay_status="normal",
        paper_status="ok",
        trading_day_status="trading_day",
        deployment_identity_error="illiquidity/v3.1 spec_hash 不匹配:清单=4f667d162dc5 注册=c9b026914617",
    )
    assert readiness.allowed is False
    assert any(r.startswith("deployment_identity:") for r in readiness.blocking_reasons)

    clean = build_production_readiness(
        data_date="2026-06-18",
        expected_trade_date="2026-06-18",
        governance_status="approved",
        decay_status="normal",
        paper_status="ok",
        trading_day_status="trading_day",
    )
    assert not any(r.startswith("deployment_identity:") for r in clean.blocking_reasons)


def test_paper_latest_signal_ignores_drafts():
    from services.read import paper as P

    signals = Path(tempfile.mkdtemp()) / "signals"
    signals.mkdir(parents=True)
    old_signals = P.SIGNALS
    P.SIGNALS = signals
    try:
        _write_json(signals / "2026-06-17.json", {"date": "2026-06-17", "action": "formal"})
        _write_json(signals / "drafts" / "2026-06-18.json", {"date": "2026-06-18", "action": "draft"})

        assert P._latest_signal()["date"] == "2026-06-17"
    finally:
        P.SIGNALS = old_signals


def test_scheduled_report_records_production_readiness():
    import runtime.production_readiness as PR
    from contracts.views import ProductionReadinessView
    from scripts.ops import scheduled_daily_update as S

    old_get = PR.get_production_readiness
    PR.get_production_readiness = lambda data_date=None, expected_trade_date=None: ProductionReadinessView(
        allowed=False,
        blocking_reasons=["data_stale"],
        warnings=[],
        data_date=data_date or "",
        expected_trade_date=expected_trade_date or "",
        governance_status="approved",
        decay_status="normal",
    )
    try:
        report = {
            "latest_after_update": "2026-06-17",
            "expected_trade_date": "2026-06-18",
        }
        readiness = S.attach_production_readiness(report)

        assert readiness.allowed is False
        assert report["production_readiness"]["blocking_reasons"] == ["data_stale"]
        assert report["production_readiness"]["data_date"] == "2026-06-17"
    finally:
        PR.get_production_readiness = old_get


if __name__ == "__main__":
    test_blocked_signal_writes_draft_and_preserves_formal_state()
    test_allowed_signal_writes_formal_signal_and_state()
    test_build_production_readiness_blocks_stale_and_governance_failures()
    test_build_production_readiness_surfaces_deployment_identity_error()
    test_paper_latest_signal_ignores_drafts()
    test_scheduled_report_records_production_readiness()
    print("✅ test_production_readiness")
