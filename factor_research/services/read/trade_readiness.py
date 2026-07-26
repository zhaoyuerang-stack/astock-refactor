"""Trade Readiness Read Service.

Evaluates daily system state to determine trade readiness.
"""
from __future__ import annotations

from pathlib import Path

from contracts.views import TradeReadinessView
from runtime.production_readiness import get_production_readiness
from services.read.risk import risk_report
from services.read.state import data_quality

ROOT = Path(__file__).resolve().parents[2]

# decay 总体状态 → factor_health 语义。红=有策略衰减,不得当「正常」放行(原代码硬编码 normal)。
_DECAY_TO_HEALTH = {"green": "normal", "yellow": "watch", "red": "degraded"}


def _factor_health_from_decay() -> tuple[str, dict]:
    """读真实 decay 报告(reports/decay_status.json)定 factor_health,绝不硬编码 normal。

    返回 (health, meta)。文件缺失/解析失败 → "unknown"(诚实未知,不假绿)。
    """
    import json
    p = ROOT / "reports" / "decay_status.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return "unknown", {"source": "decay_status.json", "note": "decay 报告缺失/不可读"}
    status = str(d.get("status", "")).lower()
    health = _DECAY_TO_HEALTH.get(status, "unknown")
    decayed = [s.get("strategy") for s in d.get("strategies", []) if s.get("decayed")]
    return health, {
        "source": "decay_status.json",
        "decay_status": status or "unknown",
        "as_of_date": d.get("as_of_date", ""),
        "generated_at": d.get("generated_at", ""),
        "decayed_strategies": decayed,
    }


def get_trade_readiness() -> TradeReadinessView:
    # 1. Check Data status
    data_clean_ratio = None
    control_errors: dict[str, str] = {}
    try:
        dq = data_quality(with_duckdb=False)
        data_status = dq.verdict
        data_clean_ratio = getattr(dq, "clean_ratio", None)  # 真实清洁率,不再硬编码 0.998
    except Exception as exc:
        data_status = "unknown"
        control_errors["data_quality"] = type(exc).__name__

    # 2. Check risk check limits
    try:
        rr = risk_report()
        portfolio_risk = "within limit" if rr.verdict == "正常" else "breach"
    except Exception as exc:
        portfolio_risk = "unknown"
        control_errors["risk_report"] = type(exc).__name__

    # 3. Model approvals: 读生产策略的台账治理闸门(在册 + DSR 多重检验),不再硬编码 approved。
    #    决策含义:在册但 DSR 审计未通过 → 不发自动放行,转人工审批。
    model_version = "unknown"
    model_gate: dict = {}
    try:
        from app_config.settings import get_settings
        from services.read.governance import get_strategy_gate_status
        sc = get_settings().strategy
        model_gate = get_strategy_gate_status(sc.family, sc.version)
        if not model_gate.get("registered"):
            model_version = "not_registered"
        elif model_gate.get("audit_status") == "RUN_FAILED":
            model_version = "nine_gate_failed"
        elif not model_gate.get("dsr_audited"):
            model_version = "dsr_pending"
        elif model_gate.get("dsr_audited") and model_gate.get("dsr_passed") is False:
            model_version = "dsr_not_significant"   # 在册但多重检验惩罚后不显著
        else:
            model_version = "approved"
    except Exception as exc:
        model_version = "unknown"
        control_errors["model_governance"] = type(exc).__name__

    # 4. Factor health & decay check —— 读真实 decay 报告,不再硬编码 normal(ADR 修复硬编码桩)。
    #    decay=red(有策略衰减)→ degraded,会拉低 allowed_to_trade,不让衰减期自动放行。
    factor_health, factor_health_meta = _factor_health_from_decay()

    # 5. Cost forecast & liquidity status —— 读取路径暂无真实成本/流动性预测源,
    #    诚实标 unknown(不假绿)。接入真实成本模型/容量评估后再填(见 TASKS)。
    cost_forecast = "unknown"
    liquidity_status = "unknown"

    # 6. Regime & Confidence
    regime_status = "bull"
    regime_confidence = 0.85
    try:
        import json
        sig_dir = ROOT / "signals"
        files = sorted(sig_dir.glob("20*.json"))
        if files:
            with open(files[-1], encoding="utf-8") as f:
                sig = json.load(f)
                regime_status = sig.get("regime", "bull")
                # Scale confidence slightly depending on regime/distance
                regime_confidence = 0.95 if regime_status == "bear" else 0.85
    except Exception as _e:
        import logging
        logging.getLogger("trade_readiness").debug(
            "regime confidence read failed, using default: %s: %s", type(_e).__name__, _e)

    # 7. Kill switch status
    kill_switch_status = "armed"

    production_readiness = None
    try:
        production_readiness = get_production_readiness(governance_status=model_version)
    except Exception as exc:
        production_readiness = None
        control_errors["production_readiness"] = type(exc).__name__

    # Overall allowed to trade(model_version 非 "approved" 即不自动放行)
    allowed_to_trade = (
        data_status in ["可用", "关注"]
        and portfolio_risk == "within limit"
        and model_version == "approved"
        and factor_health == "normal"
        and kill_switch_status == "armed"
        and production_readiness is not None
        and production_readiness.allowed
    )
    # 任何准入条件未知/失败都不得自动放行；人工审批字段用于显式暴露阻断，而非覆盖阻断。
    human_approval_required = not allowed_to_trade
    details = {
        "data_clean_ratio": data_clean_ratio,  # 真实值;读不到为 None,前端按未知呈现
        "max_exposure_allowed": 1.25,
        "factor_health_detail": factor_health_meta,  # decay 来源/as_of/衰减策略清单,可追溯
        "model_admission_track": model_gate.get("admission_track", ""),
        "model_dsr_audited": model_gate.get("dsr_audited", False),
        "model_dsr_passed": model_gate.get("dsr_passed"),
        "model_dsr_p": model_gate.get("dsr_p"),
        "model_audit_status": model_gate.get("audit_status", ""),
        "model_audit_label": model_gate.get("audit_label", ""),
        "model_nine_gate_error": model_gate.get("nine_gate_error", ""),
        "control_errors": control_errors,
    }
    if production_readiness:
        if hasattr(production_readiness, "model_dump"):
            details["production_readiness"] = production_readiness.model_dump()
        else:
            details["production_readiness"] = production_readiness.dict()

    return TradeReadinessView(
        allowed_to_trade=allowed_to_trade,
        data_status=data_status,
        model_version=model_version,
        factor_health=factor_health,
        portfolio_risk=portfolio_risk,
        cost_forecast=cost_forecast,
        liquidity_status=liquidity_status,
        regime_status=regime_status,
        regime_confidence=regime_confidence,
        kill_switch_status=kill_switch_status,
        human_approval_required=human_approval_required,
        details=details
    )
