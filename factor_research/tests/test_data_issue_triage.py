import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_validation_results_triage_classifies_severity_and_manual_review():
    from lake.data_issue_triage import build_validation_triage

    out = Path(tempfile.mkdtemp()) / "reports/data/data_issue_triage.json"
    results = [
        {
            "code": "000001",
            "rows": 20,
            "issues": ["OHLC逻辑错误2行"],
            "info": ["孤立缺失6天(疑数据源漏数据)"],
            "ok": False,
        },
        {
            "code": "000002",
            "rows": 20,
            "issues": ["负价格1行(后复权错误)"],
            "info": [],
            "ok": False,
        },
    ]

    report = build_validation_triage(results, save_path=out)

    assert out.exists()
    assert report["summary"]["production_blocked"] is True
    assert report["summary"]["counts_by_category"]["OHLC_INVALID"] == 1
    assert report["summary"]["counts_by_category"]["NEGATIVE_PRICE"] == 1
    assert report["summary"]["counts_by_category"]["MISSING_BAR"] == 1
    assert report["summary"]["counts_by_severity"]["block_production"] == 2
    assert report["summary"]["counts_by_severity"]["block_backtest"] == 1
    severe = [i for i in report["issues"] if i["category"] == "NEGATIVE_PRICE"][0]
    assert severe["auto_repair_allowed"] is False
    assert severe["manual_review"]
    assert severe.get("suggested_command", "") == ""
    assert _read_json(out)["summary"]["production_blocked"] is True


def test_scheduled_report_triage_records_stale_and_etf_failures():
    from lake.data_issue_triage import build_scheduled_update_triage

    out = Path(tempfile.mkdtemp()) / "reports/data/data_issue_triage.json"
    daily = {
        "run_date": "2026-06-18",
        "data_fresh": False,
        "latest_after_update": "2026-06-17",
        "expected_trade_date": "2026-06-18",
        "etf_update": {
            "ok": False,
            "detail": {
                "511010": {"ok": True},
                "518880": {"ok": False, "error": "timeout"},
            },
        },
        "sample_quality": {
            "bad": [{"code": "000001", "issues": ["OHLC逻辑错误1行"]}],
        },
    }

    report = build_scheduled_update_triage(daily, save_path=out)

    categories = report["summary"]["counts_by_category"]
    assert categories["STALE"] == 1
    assert categories["ETF_SOURCE_STALE"] == 1
    assert categories["OHLC_INVALID"] == 1
    assert report["summary"]["production_blocked"] is True
    assert any("scheduled_daily_update.py" in cmd for cmd in report["suggested_commands"])
    assert any("fetch_cross_asset_etf.py" in cmd for cmd in report["suggested_commands"])
    assert out.exists()


def test_production_readiness_blocks_on_triage_report():
    from runtime.production_readiness import get_production_readiness

    root = Path(tempfile.mkdtemp())
    triage_path = root / "reports/data/data_issue_triage.json"
    triage_path.parent.mkdir(parents=True)
    triage_path.write_text(json.dumps({
        "summary": {
            "production_blocked": True,
            "counts_by_category": {"OHLC_INVALID": 1},
        }
    }, ensure_ascii=False), encoding="utf-8")

    readiness = get_production_readiness(
        root=root,
        data_date="2026-06-18",
        expected_trade_date="2026-06-18",
        governance_status="approved",
        decay_status="normal",
        paper_status="ok",
        trading_day_status="trading_day",
    )

    assert readiness.allowed is False
    assert "data_issue:block_production" in readiness.blocking_reasons


if __name__ == "__main__":
    test_validation_results_triage_classifies_severity_and_manual_review()
    test_scheduled_report_triage_records_stale_and_etf_failures()
    test_production_readiness_blocks_on_triage_report()
    print("✅ test_data_issue_triage")
