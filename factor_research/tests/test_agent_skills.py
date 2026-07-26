"""Agent skill 路由 + 数据源边界 + DeepSeek 讲解防编造护栏测试。

单测默认离线(NullAdapter):路由/边界/确定性摘要不依赖外部 LLM。
DeepSeek 讲解的"替换/否决/智能体路由"行为用注入式假 adapter 单独验证。
Run: cd factor_research && python3 tests/test_agent_skills.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from providers.llm_adapter import NullAdapter
from services.agent import skills
from services.agent.skills import (
    StockDataSkill,
    SystemStatusSkill,
    _number_guard,
    route_skill,
    tool_registry,
)


def _offline() -> None:
    skills.get_adapter = lambda: NullAdapter()


def _set_adapter(adapter) -> None:
    skills.get_adapter = lambda: adapter


# ── 离线:路由 + 数据源边界 ─────────────────────────────────────────────────────
def test_route_system_status_questions_to_status_skill():
    _offline()
    skill = route_skill("系统有几个策略", {"current_page": "overview"})
    assert skill.name == "system_status"
    result = skill.answer("系统有几个策略", {"current_page": "overview"})
    assert result["tool"] == "strategies"
    assert result["output"].source_types == ["runtime", "ui_context"]
    assert result["output"].citations == []


def test_route_stock_questions_to_stock_data_skill():
    _offline()
    skill = route_skill("600519 最近情况怎么样", {"current_page": "overview"})
    assert skill.name == "stock_data"
    result = skill.answer("600519 最近情况怎么样", {"current_page": "overview"})
    assert result["tool"] == "stock_profile"
    assert result["output"].source_types == ["runtime", "ui_context"]
    assert result["output"].citations == []


def test_route_usage_questions_to_system_guide_skill():
    _offline()
    skill = route_skill("这个系统怎么用", {"current_page": "overview"})
    assert skill.name == "system_guide"
    result = skill.answer("这个系统怎么用", {"current_page": "overview"})
    assert result["tool"] is None
    assert "system_manual" in result["output"].source_types


def test_capability_questions_route_to_guide():
    _offline()
    assert route_skill("现在系统能干什么", {}).name == "system_guide"
    assert route_skill("系统能帮我做什么", {}).name == "system_guide"


# ── 离线:策略细节(每个 / 具名)──────────────────────────────────────────────────
def test_each_strategy_lists_per_version_evidence():
    _offline()
    r = SystemStatusSkill().answer("每个策略情况如何", {})
    assert r["tool"] == "strategies"
    assert r["output"].evidence, "每策略问应给出逐版本证据"


def test_named_strategy_returns_that_family_detail():
    _offline()
    data = tool_registry()["strategies"].fn()
    fam = data[0]["family"]
    r = SystemStatusSkill().answer(f"{fam} 这个策略情况如何", {})
    assert r["tool"] == "strategies"
    assert fam in r["output"].summary


# ── 数字护栏:防编造 + 防篡改计数 ────────────────────────────────────────────────
def test_number_guard_logic():
    det = "台账 39 个版本,在册 22。"
    allowed = det + " 39 22 2"
    assert _number_guard("台账共 39 个版本，22 个在册。", det, allowed) is True
    assert _number_guard("策略台账共有 2 个策略。", det, allowed) is False      # 缺确定性 39/22
    assert _number_guard("台账 39 在册 22，编造 123456.789。", det, det) is False  # 引入新数字


def test_grounded_narration_replaces_summary():
    _offline()
    det = SystemStatusSkill(forced_tool="data_quality").answer("数据质量怎么样", {})["output"].summary
    grounded = "【讲解】" + det   # 含全部确定性数字、无新数字 → 应被采纳

    class Good:
        def available(self): return True
        def route(self, *a, **k): return None
        def synthesize(self, *a, **k): return grounded

    _set_adapter(Good())
    r = SystemStatusSkill(forced_tool="data_quality").answer("数据质量怎么样", {})
    assert r["output"].summary == grounded


def test_fabricated_number_is_rejected():
    _offline()
    det = SystemStatusSkill(forced_tool="data_quality").answer("数据质量怎么样", {})["output"].summary

    class Fab:
        def available(self): return True
        def route(self, *a, **k): return None
        def synthesize(self, *a, **k): return det + " 另有编造指标 123456.789。"

    _set_adapter(Fab())
    r = SystemStatusSkill(forced_tool="data_quality").answer("数据质量怎么样", {})
    assert "123456.789" not in r["output"].summary
    assert r["output"].summary == det   # 回退确定性摘要


def test_stock_fabrication_is_rejected():
    _offline()

    class FabStock:
        def available(self): return True
        def route(self, *a, **k): return None
        def synthesize(self, *a, **k): return "贵州茅台 PE 高达 99999.99 倍。"

    _set_adapter(FabStock())
    r = StockDataSkill().answer("600519 最近怎么样", {})
    assert "99999.99" not in r["output"].summary   # data_lake 没有该值 → 否决


# ── LLM 结构化意图解析:换问法不靠关键词;确定性安全前置不可被覆盖 ────────────────
class _IntentAgent:
    """假 agent:complete() 返回结构化意图 JSON;synthesize 不参与(返回 None)。"""
    def __init__(self, payload: str):
        self._payload = payload
    def available(self): return True
    def complete(self, system, user, max_tokens=2000, timeout=None): return self._payload
    def synthesize(self, *a, **k): return None


def test_llm_intent_selects_status_tool():
    _set_adapter(_IntentAgent('{"skill":"system_status","tool":"risk"}'))
    sk = route_skill("随便看看大盘怎么样", {"current_page": "overview"})
    assert isinstance(sk, SystemStatusSkill) and sk.forced_tool == "risk"


def test_llm_intent_ranking_without_keyword():
    # "顶尖"不在任何关键词清单里 → 全靠 LLM intent=ranking + rank_by=calmar
    _set_adapter(_IntentAgent('{"skill":"system_status","tool":"strategies","intent":"ranking","rank_by":"calmar"}'))
    sk = route_skill("把表现最顶尖的那个策略讲给我听", {})
    r = sk.answer("把表现最顶尖的那个策略讲给我听", {})
    assert r["tool"] == "strategies"
    assert "卡玛" in r["output"].summary and "最佳" in r["output"].summary


def test_llm_intent_named_strategy():
    data = tool_registry()["strategies"].fn()
    fam = data[0]["family"]
    _set_adapter(_IntentAgent(f'{{"skill":"system_status","tool":"strategies","intent":"named","entity":"{fam}"}}'))
    sk = route_skill("帮我说说那个策略", {})
    r = sk.answer("帮我说说那个策略", {})
    assert r["tool"] == "strategies" and fam in r["output"].summary


def test_stock_code_safety_overrides_llm():
    _set_adapter(_IntentAgent('{"skill":"system_guide"}'))   # LLM 试图改判
    sk = route_skill("600519 怎么样", {})
    assert sk.name == "stock_data"      # 安全前置:抽到代码必走数据源


def test_stock_resolves_by_name():
    from services.read.stocks import resolve_stock_code
    assert resolve_stock_code("汇川技术股票怎么样") == "300124"
    assert resolve_stock_code("600519") == "600519"
    assert resolve_stock_code("") is None
    _offline()
    r = StockDataSkill(entity="汇川技术").answer("汇川技术怎么样", {})
    assert r["tool"] == "stock_profile"
    assert "300124" in r["output"].summary or "汇川" in r["output"].summary


def test_stock_unknown_name_asks_for_clarification():
    _offline()
    r = StockDataSkill(entity=None).answer("那只票怎么样", {})
    assert r["tool"] == "stock_profile"
    assert "没认出" in r["output"].summary or "代码" in r["output"].summary


def test_llm_intent_stock_fundamental():
    # 基本面/议价权问句 → stock_data + intent=fundamental → fundamental_profile 工具
    _set_adapter(_IntentAgent('{"skill":"stock_data","intent":"fundamental","entity":"300124"}'))
    sk = route_skill("汇川技术议价权强不强,估值贵不贵", {})
    r = sk.answer("汇川技术议价权强不强,估值贵不贵", {})
    assert r["tool"] == "fundamental_profile"
    assert "300124" in r["output"].summary and "预期差" in r["output"].summary


def test_offline_falls_back_to_keyword_routing():
    _offline()   # NullAdapter.complete → None → _parse_intent 返回 None → 关键词降级
    assert route_skill("系统有几个策略", {}).name == "system_status"
    assert route_skill("这个系统怎么用", {}).name == "system_guide"


if __name__ == "__main__":
    test_route_system_status_questions_to_status_skill()
    test_route_stock_questions_to_stock_data_skill()
    test_route_usage_questions_to_system_guide_skill()
    test_capability_questions_route_to_guide()
    test_each_strategy_lists_per_version_evidence()
    test_named_strategy_returns_that_family_detail()
    test_number_guard_logic()
    test_grounded_narration_replaces_summary()
    test_fabricated_number_is_rejected()
    test_stock_fabrication_is_rejected()
    test_llm_intent_selects_status_tool()
    test_llm_intent_ranking_without_keyword()
    test_llm_intent_named_strategy()
    test_stock_code_safety_overrides_llm()
    test_stock_resolves_by_name()
    test_stock_unknown_name_asks_for_clarification()
    test_llm_intent_stock_fundamental()
    test_offline_falls_back_to_keyword_routing()
    print("Agent skill tests passed.")
