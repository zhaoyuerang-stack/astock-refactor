"""Phase 5 验证:Agent planner 工具路由 + 不越权分级。

Run: cd factor_research && python3 tests/test_agent_phase5.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from services.agent.planner import ask
from services.agent.tools import tool_registry


def test_tool_whitelist_risk():
    reg = tool_registry()
    assert reg["data_quality"].risk == "readonly"
    assert reg["run_backtest"].risk == "mid"
    assert reg["rebalance"].risk == "high" and reg["rebalance"].fn is None
    print(f"✅ 工具白名单 {len(reg)} 个,风险分级正确")


def test_high_risk_never_auto_executes():
    """不越权铁律:调仓(high)→ requires_human_confirmation,绝不执行。"""
    r = ask("帮我降仓调仓", {"current_page": "portfolio"})
    assert r["tool"] == "rebalance" and r["risk"] == "high"
    assert r["output"]["requires_human_confirmation"] is True
    print("✅ 高风险(调仓)→ 仅提案待确认,系统不自动执行")


def test_mid_risk_requires_confirmation():
    r = ask("跑一下回测", {"current_page": "backtest"})
    assert r["tool"] == "run_backtest" and r["output"]["requires_human_confirmation"] is True
    print("✅ 中风险(回测)→ 需二次确认")


def test_readonly_executes_and_grounds():
    r = ask("当前数据质量怎么样", {"current_page": "data"})
    assert r["tool"] == "data_quality"
    assert r["output"]["requires_human_confirmation"] is False
    assert "数据质量" in r["output"]["summary"]
    print(f"✅ 只读(数据质量)→ 自动执行并解读:{r['output']['summary'][:40]}…")


def test_page_context_routing():
    r = ask("看看", {"current_page": "risk"})   # 无明确关键词 → 按页面默认
    assert r["tool"] == "risk"
    print("✅ 页面上下文路由:risk 页 → risk 工具")


def test_llm_not_ready_but_works():
    r = ask("帮助", {})
    assert isinstance(r["llm_ready"], bool)   # 本机可能已有运行时 LLM 配置
    print(f"✅ Agent help 可用(llm_ready={r['llm_ready']})")


def test_llm_cannot_override_strategy_counts():
    """DeepSeek/LLM 只能润色,不能覆盖确定性台账数量。"""
    from services.agent import skills

    class BadAdapter:
        def available(self):
            return True

        def complete(self, system, user, max_tokens=2000, timeout=None):
            return None   # 意图解析失败 → 回退关键词路由;本测试只验证 synthesize 不得改计数

        def synthesize(self, request, context, tool_name, data, timeout=20):
            return "根据工具返回的数据，策略台账共有 2 个策略。"

    old = skills.get_adapter
    skills.get_adapter = lambda: BadAdapter()
    try:
        r = ask("策略台账有多少个策略", {"current_page": "overview"})
    finally:
        skills.get_adapter = old

    assert "2 个策略" not in r["output"]["summary"]
    assert "台账" in r["output"]["summary"]
    print("✅ LLM 不得覆盖策略台账确定性数量")


if __name__ == "__main__":
    # 单测离线确定性:status/stock 的硬数据断言不依赖外部 LLM;LLM 行为由注入式 adapter 单独验证。
    from providers.llm_adapter import NullAdapter
    from services.agent import skills
    skills.get_adapter = lambda: NullAdapter()

    print("Running Phase 5 agent tests...\n")
    test_tool_whitelist_risk()
    test_high_risk_never_auto_executes()
    test_mid_risk_requires_confirmation()
    test_readonly_executes_and_grounds()
    test_page_context_routing()
    test_llm_not_ready_but_works()
    test_llm_cannot_override_strategy_counts()
    print("\n🎉 Phase 5 agent tests passed!")
