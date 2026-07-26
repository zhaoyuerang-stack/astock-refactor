"""Classify data issues into production/backtest blocking triage records."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

DATA_ISSUE_CATEGORIES = (
    "STALE",
    "MISSING_BAR",
    "OHLC_INVALID",
    "NEGATIVE_PRICE",
    "AMOUNT_UNIT_SUSPECT",
    "FUNDAMENTAL_ALIGNMENT",
    "ETF_SOURCE_STALE",
)

ISSUE_SEVERITY = {
    "STALE": "block_production",
    "MISSING_BAR": "block_backtest",
    "OHLC_INVALID": "block_production",
    "NEGATIVE_PRICE": "block_production",
    "AMOUNT_UNIT_SUSPECT": "block_backtest",
    "FUNDAMENTAL_ALIGNMENT": "block_backtest",
    "ETF_SOURCE_STALE": "warn_only",
}

AUTO_COMMANDS = {
    "STALE": "/opt/homebrew/bin/python3 scripts/ops/scheduled_daily_update.py --force",
    "MISSING_BAR": "/opt/homebrew/bin/python3 scripts/repair/revalidate.py",
    "ETF_SOURCE_STALE": "/opt/homebrew/bin/python3 scripts/data/fetch_cross_asset_etf.py",
}

AUTO_REPAIR_ALLOWED = {"STALE", "ETF_SOURCE_STALE"}

MANUAL_REVIEW = {
    "OHLC_INVALID": "Review source rows before writing repairs or quarantine ranges.",
    "NEGATIVE_PRICE": "Review adjusted-price source and quarantine/repair plan before mutation.",
    "AMOUNT_UNIT_SUSPECT": "Review amount/moneyflow units against source documentation.",
    "FUNDAMENTAL_ALIGNMENT": "Review report_date/avail_date alignment before backtests.",
}


def classify_issue(text: str) -> str | None:
    t = str(text or "")
    tl = t.lower()
    if "oh" in tl and "lc" in tl:
        return "OHLC_INVALID"
    if "负价格" in t or "negative" in tl or "nonpositive" in tl:
        return "NEGATIVE_PRICE"
    if "孤立缺失" in t or "空数据" in t or "missing" in tl or "缺失" in t:
        return "MISSING_BAR"
    if "stale" in tl or "过期" in t or "落后" in t:
        return "STALE"
    if "etf" in tl or "ETF" in t:
        return "ETF_SOURCE_STALE"
    if "amount" in tl or "moneyflow" in tl or "成交额" in t or "资金流" in t:
        return "AMOUNT_UNIT_SUSPECT"
    if ("fundamental" in tl or "daily_basic" in tl or "财务" in t
            or "avail_date" in tl or "report_date" in tl or "对齐" in t):
        return "FUNDAMENTAL_ALIGNMENT"
    return None


def _issue(category: str, detail: str, *, code: str = "", source: str = "", context: dict | None = None) -> dict:
    severity = ISSUE_SEVERITY[category]
    item = {
        "category": category,
        "severity": severity,
        "code": code,
        "source": source,
        "detail": str(detail),
        "auto_repair_allowed": category in AUTO_REPAIR_ALLOWED,
        "suggested_command": AUTO_COMMANDS.get(category, ""),
        "manual_review": MANUAL_REVIEW.get(category, ""),
    }
    if context:
        item["context"] = context
    return item


def _summarize(issues: list[dict], *, source: str, latest_data_date: str = "", expected_trade_date: str = "") -> dict:
    counts_by_category = {c: 0 for c in DATA_ISSUE_CATEGORIES}
    counts_by_severity = {"block_production": 0, "block_backtest": 0, "warn_only": 0}
    for item in issues:
        counts_by_category[item["category"]] = counts_by_category.get(item["category"], 0) + 1
        counts_by_severity[item["severity"]] = counts_by_severity.get(item["severity"], 0) + 1
    counts_by_category = {k: v for k, v in counts_by_category.items() if v}
    suggested_commands = sorted({
        item["suggested_command"]
        for item in issues
        if item.get("suggested_command") and item.get("auto_repair_allowed")
    })
    manual_review = [
        {
            "category": item["category"],
            "code": item.get("code", ""),
            "detail": item["detail"],
            "instruction": item.get("manual_review", ""),
        }
        for item in issues
        if item.get("manual_review")
    ]
    summary = {
        "total_issues": len(issues),
        "counts_by_category": counts_by_category,
        "counts_by_severity": counts_by_severity,
        "production_blocked": counts_by_severity.get("block_production", 0) > 0,
        "backtest_blocked": counts_by_severity.get("block_backtest", 0) > 0,
        "latest_data_date": latest_data_date,
        "expected_trade_date": expected_trade_date,
    }
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "summary": summary,
        "issues": issues,
        "suggested_commands": suggested_commands,
        "manual_review": manual_review,
    }


def _write(path: str | Path | None, report: dict) -> None:
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def build_validation_triage(results: list[dict], save_path: str | Path | None = None) -> dict:
    issues: list[dict] = []
    for result in results:
        code = str(result.get("code", ""))
        for field in ("issues", "info"):
            for detail in result.get(field, []) or []:
                category = classify_issue(detail)
                if category:
                    issues.append(_issue(category, detail, code=code, source=f"validator.{field}"))
    report = _summarize(issues, source="validation_results")
    _write(save_path, report)
    return report


def build_scheduled_update_triage(report: dict, save_path: str | Path | None = None) -> dict:
    issues: list[dict] = []
    latest = str(report.get("latest_after_update") or "")
    expected = str(report.get("expected_trade_date") or "")

    if report.get("data_fresh") is False:
        issues.append(_issue(
            "STALE",
            f"latest_after_update={latest or 'unknown'} expected_trade_date={expected or 'unknown'}",
            source="scheduled_daily_update.freshness",
            context={"latest_after_update": latest, "expected_trade_date": expected},
        ))

    for row in report.get("sample_quality", {}).get("bad", []) or []:
        code = str(row.get("code", ""))
        for detail in row.get("issues", []) or []:
            category = classify_issue(detail)
            if category:
                issues.append(_issue(category, detail, code=code, source="scheduled_daily_update.sample_quality"))

    etf = report.get("etf_update", {}) or {}
    if etf.get("ok") is False:
        detail = etf.get("detail") or {}
        if isinstance(detail, dict):
            failed = [code for code, stat in detail.items() if not (stat or {}).get("ok")]
            for code in failed or ["ETF"]:
                err = (detail.get(code) or {}).get("error", "update_failed") if failed else "update_failed"
                issues.append(_issue("ETF_SOURCE_STALE", err, code=str(code), source="scheduled_daily_update.etf"))
        else:
            issues.append(_issue("ETF_SOURCE_STALE", etf.get("error", "update_failed"), source="scheduled_daily_update.etf"))

    if (report.get("fundamental_update", {}) or {}).get("ok") is False:
        detail = report.get("fundamental_update", {}).get("error", "fundamental update failed")
        issues.append(_issue("FUNDAMENTAL_ALIGNMENT", detail, source="scheduled_daily_update.fundamental"))

    ts = report.get("tushare_incremental", {}) or {}
    if ts.get("ok") is False:
        detail = ts.get("detail") or {}
        if isinstance(detail, dict):
            for dim, stat in detail.items():
                if (stat or {}).get("ok"):
                    continue
                text = f"{dim}: {(stat or {}).get('error', 'update_failed')}"
                category = classify_issue(text) or "FUNDAMENTAL_ALIGNMENT"
                issues.append(_issue(category, text, source="scheduled_daily_update.tushare"))
        else:
            issues.append(_issue("FUNDAMENTAL_ALIGNMENT", ts.get("error", "tushare update failed"),
                                 source="scheduled_daily_update.tushare"))

    triage = _summarize(
        issues,
        source="scheduled_daily_update",
        latest_data_date=latest,
        expected_trade_date=expected,
    )
    _write(save_path, triage)
    return triage
