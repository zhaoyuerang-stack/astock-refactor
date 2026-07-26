"""trade_readiness 的 factor_health 必须读真实 decay 报告,不再硬编码 normal(机制修复)。

原 trade_readiness.py 把 factor_health 写死 "normal",交易就绪闸门永远当因子健康放行,
无论实际衰减。改为读 reports/decay_status.json,red→degraded 会拉低 allowed_to_trade。
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import services.read.trade_readiness as TR


class _Ready:
    allowed = True

    def dict(self):
        return {"allowed": True}


def _write_decay(tmp_path, status, strategies=()):
    rep = tmp_path / "reports"
    rep.mkdir()
    (rep / "decay_status.json").write_text(json.dumps({
        "status": status, "as_of_date": "2026-06-18", "generated_at": "2026-06-22T10:00:00+08:00",
        "strategies": list(strategies),
    }), encoding="utf-8")


def test_red_decay_maps_to_degraded(tmp_path, monkeypatch):
    monkeypatch.setattr(TR, "ROOT", tmp_path)
    _write_decay(tmp_path, "red", [{"strategy": "x.v1", "decayed": True}])
    health, meta = TR._factor_health_from_decay()
    assert health == "degraded"
    assert meta["decay_status"] == "red"
    assert meta["decayed_strategies"] == ["x.v1"]


def test_green_decay_maps_to_normal(tmp_path, monkeypatch):
    monkeypatch.setattr(TR, "ROOT", tmp_path)
    _write_decay(tmp_path, "green")
    assert TR._factor_health_from_decay()[0] == "normal"


def test_missing_report_is_unknown_not_normal(tmp_path, monkeypatch):
    # 报告缺失 → unknown(诚实),绝不退回假 normal
    monkeypatch.setattr(TR, "ROOT", tmp_path)
    health, meta = TR._factor_health_from_decay()
    assert health == "unknown"
    assert "缺失" in meta["note"] or "不可读" in meta["note"]


def test_degraded_health_blocks_auto_trade(tmp_path, monkeypatch):
    # factor_health=degraded 必须使 allowed_to_trade=False(衰减期不自动放行)
    monkeypatch.setattr(TR, "_factor_health_from_decay", lambda: ("degraded", {}))
    v = TR.get_trade_readiness()
    assert v.factor_health == "degraded"
    assert v.allowed_to_trade is False


@pytest.mark.parametrize("failed_dependency", ["data", "risk", "model", "production"])
def test_dependency_failure_blocks_auto_trade_and_requires_human(
    tmp_path, monkeypatch, failed_dependency
):
    """任何准入依赖异常都必须 fail closed，不能由默认绿值掩盖。"""
    from app_config import settings as settings_module
    from services.read import governance as governance_module

    monkeypatch.setattr(TR, "ROOT", tmp_path)
    monkeypatch.setattr(
        TR,
        "data_quality",
        lambda **_: SimpleNamespace(verdict="可用", clean_ratio=1.0),
    )
    monkeypatch.setattr(TR, "risk_report", lambda: SimpleNamespace(verdict="正常"))
    monkeypatch.setattr(TR, "_factor_health_from_decay", lambda: ("normal", {}))
    monkeypatch.setattr(TR, "get_production_readiness", lambda **_: _Ready())
    monkeypatch.setattr(
        settings_module,
        "get_settings",
        lambda: SimpleNamespace(strategy=SimpleNamespace(family="toy", version="v1")),
    )
    monkeypatch.setattr(
        governance_module,
        "get_strategy_gate_status",
        lambda *_: {
            "registered": True,
            "audit_status": "PASS",
            "dsr_audited": True,
            "dsr_passed": True,
        },
    )

    def boom(*_args, **_kwargs):
        raise RuntimeError(f"{failed_dependency} unavailable")

    if failed_dependency == "data":
        monkeypatch.setattr(TR, "data_quality", boom)
    elif failed_dependency == "risk":
        monkeypatch.setattr(TR, "risk_report", boom)
    elif failed_dependency == "model":
        monkeypatch.setattr(governance_module, "get_strategy_gate_status", boom)
    else:
        monkeypatch.setattr(TR, "get_production_readiness", boom)

    view = TR.get_trade_readiness()

    assert view.allowed_to_trade is False
    assert view.human_approval_required is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
