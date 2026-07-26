"""系统设置只读视图:成本铁律(锁定)/ 策略 / 风控规则 / AI 模型 / 服务状态。

铁律护栏:成本(买0.225%/卖0.275%/融资6.5%)以 locked=True 只读展示,UI 不可调低。
"""
from __future__ import annotations

from pathlib import Path

from contracts.views import SystemConfigView

ROOT = Path(__file__).resolve().parents[2]


def _settings() -> dict:
    try:
        import yaml
    except ImportError:
        return {}
    p = ROOT / "app_config" / "settings.yaml"
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}


def _services_status() -> list[dict]:
    out = [{"name": "API (FastAPI)", "status": "正常"}, {"name": "回测引擎 (core.engine)", "status": "正常"}]
    try:
        import duckdb  # noqa: F401
        out.append({"name": "DuckDB (即席QA)", "status": "正常"})
    except ImportError:
        out.append({"name": "DuckDB (即席QA)", "status": "未安装"})
    from providers.llm_adapter import llm_ready
    out.append({"name": "Agent LLM", "status": "已接入" if llm_ready() else "规则式(无 key)"})
    out.append({"name": "数据湖 data_lake", "status": "正常" if (ROOT / "data_lake").exists() else "缺失"})
    return out


def system_config() -> SystemConfigView:
    s = _settings()
    from lake.cleaning import load_quarantine
    from providers.llm_adapter import ai_model_info
    return SystemConfigView(
        cost={**(s.get("cost") or {}), "locked": True},   # 成本铁律,只读
        strategy=s.get("strategy") or {},
        risk_policy=s.get("risk_policy") or {},
        data=s.get("data") or {},
        ai_model=ai_model_info(),   # {provider, model, base_url, llm_ready, mode}
        services=_services_status(),
        quarantine_ranges=len(load_quarantine()),
    )
