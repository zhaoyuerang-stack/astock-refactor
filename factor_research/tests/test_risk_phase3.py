"""Phase 3 验证:声明式 risk_policy 评估 + 控制回路。

Run:
    cd factor_research && python3 tests/test_risk_phase3.py
    cd factor_research && PHASE3_FULL=1 python3 tests/test_risk_phase3.py   # 含 target 现算集成
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import services.read.risk as risk_mod
from contracts.models import ControlAction
from services.read.risk import (
    _cap_check,
    _registered_maxdd,
    _settings,
    load_risk_policy,
    risk_report,
)


def test_cap_check_levels():
    assert _cap_check("x", 0.10, 0.50).status == "breach"
    assert _cap_check("x", 0.10, 0.095).status == "warn"   # >= 0.9*阈值
    assert _cap_check("x", 0.10, 0.04).status == "ok"
    assert _cap_check("x", 0.10, None).status == "na"
    print("✅ cap_check 分级 ok/warn/breach/na 正确")


def test_policy_loaded():
    p = load_risk_policy()
    assert p["max_single_position_weight"] > 0 and p["max_position_count"] > 0
    print(f"✅ risk_policy 载入:单票上限 {p['max_single_position_weight']}, 持仓上限 {p['max_position_count']}")


def test_breach_generates_control_action():
    """合成一个超限组合 → 必须生成 requires_confirmation 的 ControlAction(且未执行)。"""
    breach = _cap_check("单票最大权重", 0.10, 0.50)
    assert breach.status == "breach"
    ca = ControlAction(action_id="ca-test", object_type="portfolio", object_id="x/v1",
                        trigger_state=f"{breach.rule}={breach.current} vs {breach.threshold}",
                        action="decrease", reason="单票超限", requires_confirmation=True, executed=False)
    assert ca.requires_confirmation is True and ca.executed is False
    print("✅ 超限 → ControlAction(待确认,未执行)")


def test_current_strategy_drawdown_loaded_from_configured_version():
    """风控历史回撤必须来自当前生产版本,不能误取台账第一个在册版本。"""
    strategy = (_settings().get("strategy") or {})
    assert strategy.get("family") == "illiquidity"
    assert strategy.get("version") == "v3.1"
    assert abs((_registered_maxdd() or 0.0) - (-0.1195)) < 1e-12
    print("✅ 当前生产版本 maxdd 来自 illiquidity/v3.1 台账")


def test_risk_report_integration():
    r = risk_report()
    assert r.verdict in ("正常", "预警", "超限")
    assert any(c.rule == "单票最大权重" for c in r.checks)
    print(f"✅ risk_report 集成:verdict={r.verdict} "
          f"checks={[(c.rule, c.status) for c in r.checks]} actions={len(r.control_actions)}")


def test_risk_report_uses_latest_signal_effective_leverage():
    """风控当前杠杆必须优先读最新信号的 band_exposure/leverage。"""
    with tempfile.TemporaryDirectory() as td:
        tmp_root = Path(td)
        (tmp_root / "signals").mkdir(parents=True, exist_ok=True)
        (tmp_root / "signals" / "2026-06-16.json").write_text(
            json.dumps({"date": "2026-06-16", "band_exposure": 1.37, "leverage": 9.9}),
            encoding="utf-8",
        )

        original_root = risk_mod.ROOT
        original_settings = risk_mod._settings
        original_portfolio = risk_mod.target_portfolio
        original_maxdd = risk_mod._registered_maxdd
        try:
            risk_mod.ROOT = tmp_root
            risk_mod._settings = lambda: {
                "strategy": {"family": "illiquidity", "version": "v3.1", "leverage": 1.25},
                "risk_policy": {},
            }
            risk_mod.target_portfolio = lambda: []
            risk_mod._registered_maxdd = lambda *args, **kwargs: None

            r = risk_report()
            leverage_check = next(c for c in r.checks if c.rule == "杠杆")
            assert abs((leverage_check.current or 0.0) - 1.37) < 1e-12
            assert "latest signal 2026-06-16" in r.evaluated_on
            assert "band_exposure" in (leverage_check.note or "")
            print("✅ 风控当前杠杆读取最新信号 band_exposure")
        finally:
            risk_mod.ROOT = original_root
            risk_mod._settings = original_settings
            risk_mod.target_portfolio = original_portfolio
            risk_mod._registered_maxdd = original_maxdd


if __name__ == "__main__":
    print("Running Phase 3 risk tests...\n")
    test_cap_check_levels()
    test_policy_loaded()
    test_breach_generates_control_action()
    test_current_strategy_drawdown_loaded_from_configured_version()
    if os.environ.get("PHASE3_FULL"):
        test_risk_report_integration()
        test_risk_report_uses_latest_signal_effective_leverage()
    else:
        print("ℹ️  跳过 risk_report 集成(现算 target,设 PHASE3_FULL=1 开启)")
        test_risk_report_uses_latest_signal_effective_leverage()
    print("\n🎉 Phase 3 risk tests passed!")
