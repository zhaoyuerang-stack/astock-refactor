"""Phase 6 验证:系统设置(成本铁律只读)+ 审计。

Run: cd factor_research && python3 tests/test_settings_phase6.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from services.read.audit import recent_audit
from services.read.settings import system_config


def test_cost_locked_and_iron_law():
    c = system_config()
    assert c.cost.get("locked") is True, "成本必须标记 locked(UI 不可调)"
    # 成本铁律值
    assert abs(c.cost["buy_cost"] - 0.00225) < 1e-9
    assert abs(c.cost["sell_cost"] - 0.00275) < 1e-9
    assert abs(c.cost["financing_rate"] - 0.065) < 1e-9
    print("✅ 成本铁律 locked=True 且值正确(买0.225%/卖0.275%/融资6.5%)")


def test_services_and_ai_status():
    c = system_config()
    names = {s["name"] for s in c.services}
    assert any("API" in n for n in names) and any("引擎" in n for n in names)
    assert "llm_ready" in c.ai_model
    print(f"✅ 服务状态 {len(c.services)} 项;AI 模式 {c.ai_model['mode']};隔离区间 {c.quarantine_ranges}")


def test_audit_readable():
    a = recent_audit(10)
    assert a.total >= 0 and isinstance(a.entries, list)
    print(f"✅ 审计可读:{a.total} 条")


if __name__ == "__main__":
    print("Running Phase 6 settings tests...\n")
    test_cost_locked_and_iron_law()
    test_services_and_ai_status()
    test_audit_readable()
    print("\n🎉 Phase 6 settings tests passed!")
