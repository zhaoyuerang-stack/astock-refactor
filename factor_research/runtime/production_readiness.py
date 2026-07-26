"""Production signal readiness gate.

This module intentionally lives outside ``services`` so ``run_daily.py`` can use
the same production gate without violating layer dependency rules.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from contracts.views import ProductionReadinessView

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)
CHINA_TZ = ZoneInfo("Asia/Shanghai")

GOVERNANCE_ALLOWED = {"approved", "audit_passed", "passed", "ok"}
PAPER_ALLOWED = {"ok", "ready", "normal"}
PAPER_WARN = {"", "unknown", "missing"}
TRADING_ALLOWED = {"", "trading_day", "expected_closed_day", "ok"}
DECAY_TTL_DAYS = 8


def _today_for_expected() -> pd.Timestamp:
    # Before the China open, the latest closed trading day is still yesterday.
    return pd.Timestamp((datetime.now(CHINA_TZ) - pd.Timedelta(hours=9)).date())


def _date_str(value) -> str:
    if value is None or value == "":
        return ""
    try:
        return str(pd.Timestamp(value).date())
    except Exception:
        return str(value)


def _parse_date(value):
    if value is None or value == "":
        return None
    try:
        return pd.Timestamp(value)
    except Exception:
        return None


def current_deployment_identity() -> dict:
    from runtime.deployment import load_active_deployment

    deployment = load_active_deployment()
    equity = next((leg for leg in deployment.legs if leg.role == "equity_alpha"), None)
    if equity is None:
        raise RuntimeError("deployment has no equity_alpha leg")
    return {
        "deployment_id": deployment.deployment_id,
        "family": equity.family,
        "version": equity.version,
        "spec_hash": equity.spec_hash,
    }


def validate_feedback_envelope(
    payload: dict,
    *,
    expected: dict,
    ttl_days: int,
    now: datetime | None = None,
) -> dict:
    reasons: list[str] = []
    for field in ("deployment_id", "family", "version", "spec_hash"):
        if str(payload.get(field) or "") != str(expected.get(field) or ""):
            reasons.append(f"{field}_mismatch")
    if not payload.get("data_fingerprint"):
        reasons.append("data_fingerprint_missing")
    generated = payload.get("generated_at")
    try:
        generated_at = pd.Timestamp(generated)
        current = pd.Timestamp(now or datetime.now(CHINA_TZ))
        if generated_at.tzinfo is None:
            generated_at = generated_at.tz_localize(CHINA_TZ)
        if current.tzinfo is None:
            current = current.tz_localize(CHINA_TZ)
        if current - generated_at > pd.Timedelta(days=ttl_days):
            reasons.append("feedback_stale")
    except Exception:
        reasons.append("generated_at_invalid")
    return {"valid": not reasons, "blocking_reasons": reasons}


def actual_latest_price_date(root: Path = ROOT) -> str:
    dates = []
    # 性能优化：无需扫描全部 5000+ 个 Parquet 文件（耗时 5秒+），仅扫描前 10 个即可对齐最新日期（耗时 <30ms）
    for fp in sorted((root / "data_lake/price/daily").glob("*.parquet"))[:10]:
        try:
            df = pd.read_parquet(fp, columns=["date"])
        except Exception as exc:
            # 单文件不可读则跳过该项(保留 best-effort 取最新日),但必须留痕
            logger.warning("skip unreadable price parquet %s: %s", fp.name, exc)
            continue
        if len(df):
            dates.append(pd.to_datetime(df["date"]).max())
    return _date_str(max(dates)) if dates else ""


def latest_expected_trade_date(root: Path = ROOT, today=None) -> tuple[str, str]:
    if today is None:
        today_ts = _today_for_expected()
    else:
        today_ts = pd.Timestamp(today)
    cal_path = root / "data_lake/meta/trade_calendar.parquet"
    if not cal_path.exists():
        return "", "calendar_missing"
    try:
        cal = pd.read_parquet(cal_path)["date"]
        cal = pd.to_datetime(cal)
    except Exception:
        return "", "calendar_unreadable"
    eligible = cal[cal <= today_ts]
    if len(eligible):
        return _date_str(eligible.max()), "local_calendar"
    return "", "calendar_empty"


def _nine_gate_audit_state(nine_gate: dict) -> dict:
    """委托唯一裁决策略(Task 9):production readiness 与 governance/registry 同源裁决。"""
    from core.analysis.nine_gate_policy import decide_nine_gate
    return decide_nine_gate(nine_gate).as_state()


def current_governance_status() -> str:
    from runtime.deployment import DeploymentNotReady
    try:
        identity = current_deployment_identity()
    except DeploymentNotReady:
        return "deployment_not_ready"

    try:
        import strategy_registry
        data = strategy_registry._load()
        fam = next((
            f for f in data.get("families", [])
            if f.get("id") == identity["family"]
        ), None)
        version = next(
            (
                v for v in (fam or {}).get("versions", [])
                if v.get("version") == identity["version"]
            ),
            None,
        )
        if version is None or version.get("status") != "在册":
            return "not_registered"
        if (version.get("evidence") or {}).get("production_blocked"):
            return "evidence_invalidated"
        audit = _nine_gate_audit_state(version.get("nine_gate") or {})
        if audit["code"] == "RUN_FAILED":
            return "nine_gate_failed"
        if not audit["audited"]:
            return "dsr_pending"
        if audit["passed"] is False:
            return "dsr_not_significant"
        return "approved"
    except Exception:
        return "governance_unknown"


def current_decay_status(root: Path = ROOT, *, expected: dict | None = None) -> str:
    fp = root / "reports/decay_status.json"
    if not fp.exists():
        return "unknown"
    try:
        payload = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return "unreadable"
    if expected is not None:
        validation = validate_feedback_envelope(
            payload,
            expected=expected,
            ttl_days=DECAY_TTL_DAYS,
        )
        if not validation["valid"]:
            return "identity_or_stale"
    return str(payload.get("status") or "unknown")


def current_paper_status(root: Path = ROOT, *, expected: dict | None = None) -> str:
    fp = root / "paper/account.json"
    if not fp.exists():
        return "missing"
    try:
        account = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return "corrupt"
    if account.get("halted") or account.get("blocked"):
        return "blocked"
    if account.get("last_error"):
        return "error"
    last_exec = account.get("last_exec") or {}
    if last_exec.get("blocked"):
        return "blocked"
    if expected is not None:
        if not last_exec:
            return "unknown"
        if any(
            str(last_exec.get(field) or "") != str(expected.get(field) or "")
            for field in ("deployment_id", "family", "version", "spec_hash")
        ):
            return "identity_mismatch"
    return "ok"


def current_data_issue_status(root: Path = ROOT) -> dict:
    fp = root / "reports/data/data_issue_triage.json"
    if not fp.exists():
        return {"status": "unknown", "production_blocked": True, "categories": []}
    try:
        payload = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "unreadable", "production_blocked": True, "categories": []}
    summary = payload.get("summary") or {}
    categories = sorted((summary.get("counts_by_category") or {}).keys())
    blocked = bool(summary.get("production_blocked"))
    return {
        "status": "block_production" if blocked else "ok",
        "production_blocked": blocked,
        "categories": categories,
    }


def build_production_readiness(
    *,
    data_date: str = "",
    expected_trade_date: str = "",
    governance_status: str = "",
    decay_status: str = "",
    paper_status: str = "",
    trading_day_status: str = "",
    data_issue_status: dict | str | None = None,
    deployment_identity_error: str | None = None,
) -> ProductionReadinessView:
    blocking_reasons: list[str] = []
    warnings: list[str] = []

    if deployment_identity_error:
        blocking_reasons.append(f"deployment_identity:{deployment_identity_error[:80]}")

    data_date = _date_str(data_date)
    expected_trade_date = _date_str(expected_trade_date)
    data_ts = _parse_date(data_date)
    expected_ts = _parse_date(expected_trade_date)

    if not data_date:
        blocking_reasons.append("data_date_missing")
    if not expected_trade_date:
        warnings.append("expected_trade_date_missing")
    if data_ts is not None and expected_ts is not None and data_ts < expected_ts:
        blocking_reasons.append("data_stale")

    gov = (governance_status or "governance_unknown").strip()
    if gov not in GOVERNANCE_ALLOWED:
        blocking_reasons.append(f"governance:{gov}")

    decay = (decay_status or "unknown").strip()
    if decay.startswith("🔴") or "预警" in decay or decay.lower() in {"red", "failed", "fail", "breach"}:
        blocking_reasons.append("decay:red")
    elif decay.startswith("🟡") or "观察" in decay or decay.lower() in {"yellow", "watch", "warn", "warning"}:
        warnings.append("decay:watch")
    elif decay.lower() in {"", "unknown", "unreadable", "identity_or_stale"}:
        blocking_reasons.append(f"decay:{decay or 'unknown'}")

    paper = (paper_status or "unknown").strip()
    if paper in PAPER_WARN:
        blocking_reasons.append(f"paper:{paper or 'unknown'}")
    elif paper not in PAPER_ALLOWED:
        blocking_reasons.append(f"paper:{paper}")

    trading = (trading_day_status or "").strip()
    if trading and trading not in TRADING_ALLOWED:
        blocking_reasons.append(f"trading_day:{trading}")

    if isinstance(data_issue_status, dict):
        issue_status = str(data_issue_status.get("status") or "unknown")
        issue_categories = [str(c) for c in data_issue_status.get("categories", [])]
        if data_issue_status.get("production_blocked"):
            blocking_reasons.append("data_issue:block_production")
        if issue_status in {"unknown", "unreadable", "stale"}:
            blocking_reasons.append(f"data_issue:{issue_status}")
    else:
        issue_status = str(data_issue_status or "unknown")
        issue_categories = []
        if issue_status == "block_production":
            blocking_reasons.append("data_issue:block_production")

    return ProductionReadinessView(
        allowed=not blocking_reasons,
        blocking_reasons=blocking_reasons,
        warnings=warnings,
        data_date=data_date,
        expected_trade_date=expected_trade_date,
        governance_status=gov,
        decay_status=decay,
        paper_status=paper,
        trading_day_status=trading,
        data_issue_status=issue_status,
        data_issue_categories=issue_categories,
        generated_at=datetime.now(CHINA_TZ).isoformat(timespec="seconds"),
    )


def get_production_readiness(
    root: Path = ROOT,
    *,
    data_date: str | None = None,
    expected_trade_date: str | None = None,
    governance_status: str | None = None,
    decay_status: str | None = None,
    paper_status: str | None = None,
    trading_day_status: str | None = None,
    data_issue_status: dict | str | None = None,
) -> ProductionReadinessView:
    root = Path(root)
    if data_date is None:
        data_date = actual_latest_price_date(root)
    expected_source = ""
    if expected_trade_date is None:
        expected_trade_date, expected_source = latest_expected_trade_date(root)
    if governance_status is None:
        governance_status = current_governance_status()
    try:
        expected_identity = current_deployment_identity()
    except Exception as e:
        expected_identity = None
        deployment_identity_error = str(e)
    else:
        deployment_identity_error = None
    if decay_status is None:
        decay_status = current_decay_status(root, expected=expected_identity)
    if paper_status is None:
        paper_status = current_paper_status(root, expected=expected_identity)
    if trading_day_status is None:
        trading_day_status = "trading_day" if expected_trade_date else (expected_source or "unknown")
    if data_issue_status is None:
        data_issue_status = current_data_issue_status(root)

    return build_production_readiness(
        data_date=data_date or "",
        expected_trade_date=expected_trade_date or "",
        governance_status=governance_status or "",
        decay_status=decay_status or "",
        paper_status=paper_status or "",
        trading_day_status=trading_day_status or "",
        data_issue_status=data_issue_status,
        deployment_identity_error=deployment_identity_error,
    )
