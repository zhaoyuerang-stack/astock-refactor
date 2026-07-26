"""Agent knowledge retrieval and source citation tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from services.agent.knowledge import retrieve_knowledge
from services.agent.planner import ask


def test_retrieve_knowledge_returns_layered_sources_for_system_rules():
    hits = retrieve_knowledge("为什么 AI 不能直接下单", limit=4)

    assert hits
    assert any(hit.source_type == "rules" for hit in hits)
    assert any("CLAUDE.md" in hit.source_path or "SPEC.md" in hit.source_path for hit in hits)
    assert any("下单" in hit.text or "不越权" in hit.text for hit in hits)


def test_agent_answer_includes_citations_and_source_types():
    r = ask("为什么 AI 不能直接下单", {"current_page": "portfolio"})
    out = r["output"]

    assert out["citations"]
    assert "rules" in out["source_types"]
    assert any("CLAUDE.md" in c["source_path"] or "SPEC.md" in c["source_path"] for c in out["citations"])
    assert out["requires_human_confirmation"] is False


def test_high_risk_answer_keeps_confirmation_and_adds_citations():
    r = ask("帮我调仓买入", {"current_page": "portfolio"})
    out = r["output"]

    assert r["tool"] == "rebalance"
    assert out["requires_human_confirmation"] is True
    assert out["citations"]
    assert any(c["source_type"] == "rules" for c in out["citations"])


def test_retrieve_knowledge_returns_runtime_state():
    hits = retrieve_knowledge("当前系统有什么风险", limit=4)
    assert hits
    assert any(hit.source_type == "runtime" for hit in hits)
    assert any("runtime_status" == hit.source_path for hit in hits)
    assert any("风控" in hit.text or "评估" in hit.text or "数据质量" in hit.text for hit in hits)


def test_agent_fallback_rag_utilizes_llm():
    from unittest.mock import MagicMock, patch
    
    mock_adapter = MagicMock()
    mock_adapter.available.return_value = True
    mock_adapter.complete.return_value = "这是模拟 RAG 答案: 系统目前有 5 个因子家族，并且风控报告正常。"
    
    with patch("services.agent.skills.get_adapter", return_value=mock_adapter):
        r = ask("帮我查询没有注册的工具的信息", {"current_page": "overview"})
        out = r["output"]
        assert "这是模拟 RAG 答案" in out["summary"]
        assert r["tool"] is None


def test_system_usage_question_uses_manual_sources_not_stock_tool():
    r = ask("这个系统怎么用", {"current_page": "overview"})

    assert r["tool"] is None
    assert any(c["source_type"] == "system_manual" for c in r["output"]["citations"])


def test_strategy_count_question_uses_runtime_registry_not_manual_docs():
    r = ask("我们系统有几个策略", {"current_page": "overview"})

    assert r["tool"] == "strategies"
    assert "台账" in r["output"]["summary"]
    assert "在册" in r["output"]["summary"]
    assert "runtime" in r["output"]["source_types"]
    assert r["output"]["citations"] == []


def test_best_strategy_question_returns_ranked_runtime_strategy_not_count_only():
    r = ask("哪个策略最好", {"current_page": "overview"})

    assert r["tool"] == "strategies"
    assert "按夏普" in r["output"]["summary"]
    assert "最佳" in r["output"]["summary"]
    assert "台账 39 个版本" not in r["output"]["summary"]
    assert r["output"]["citations"] == []


if __name__ == "__main__":
    # 确定性断言离线化:路由/摘要不依赖外部 LLM(LLM 行为由 test_agent_fallback 的内部 mock 覆盖)。
    from providers.llm_adapter import NullAdapter
    from services.agent import skills
    skills.get_adapter = lambda: NullAdapter()

    print("Running agent knowledge tests...\n")
    test_retrieve_knowledge_returns_layered_sources_for_system_rules()
    test_agent_answer_includes_citations_and_source_types()
    test_high_risk_answer_keeps_confirmation_and_adds_citations()
    test_retrieve_knowledge_returns_runtime_state()
    test_agent_fallback_rag_utilizes_llm()
    test_system_usage_question_uses_manual_sources_not_stock_tool()
    test_strategy_count_question_uses_runtime_registry_not_manual_docs()
    test_best_strategy_question_returns_ranked_runtime_strategy_not_count_only()
    print("\nAgent knowledge tests passed.")
