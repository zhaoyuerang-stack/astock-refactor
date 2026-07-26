"""对抗测试:strategy_idea_check 永不宣称有效,且固定披露成本。"""
from __future__ import annotations

from core.engine import CostModel
from services.agent.tools import RISK_READONLY, tool_registry
from services.read.strategy_idea import check_strategy_idea


def test_empty_idea_is_honest_neutral():
    out = check_strategy_idea("")
    assert out["can_claim_valid"] is False
    assert out["fake_curve_allowed"] is False
    assert out["validation_status"] == "empty_idea"
    assert out["trust"]["banner_status"] == "neutral"


def test_idea_precheck_parses_hints_and_never_claims_valid():
    out = check_strategy_idea("小盘 + 低换手 + 反转,每周调仓,持仓30只")
    assert out["can_claim_valid"] is False
    assert out["fake_curve_allowed"] is False
    assert out["validation_status"] == "idea_precheck"
    assert out["trust"]["banner_status"] in {"attention", "blocked"}
    terms = out["parsed_hints"]["candidate_terms"]
    assert any("小盘" in t or "反转" in t or "换手" in t for t in terms)
    assert out["parsed_hints"]["rebalance_hint"] == "weekly"
    assert out["parsed_hints"]["top_n_hint"] == 30
    cost = out["system_facts"]["cost_model"]
    assert cost["buy_cost"] == CostModel().buy_cost
    assert cost["sell_cost"] == CostModel().sell_cost
    assert "不可调低" in cost["note"]
    assert any("不得" in c for c in out["forbidden_claims"])
    blob = str(out)
    assert "annual_return" not in blob
    assert "equity_curve" not in blob


def test_tool_registry_exposes_strategy_idea_check_as_readonly():
    tool = tool_registry()["strategy_idea_check"]
    assert tool.risk == RISK_READONLY
    assert tool.args == ("idea",)
    result = tool.fn(idea="动量 月频")
    assert result["can_claim_valid"] is False
    assert "动量" in result["parsed_hints"]["candidate_terms"]


def test_unregistered_token_is_detected_via_live_registry_not_hardcoded_branch():
    out = check_strategy_idea("帮我用WACC作为因子试下策略")
    assert out["can_claim_valid"] is False
    assert out["fake_curve_allowed"] is False
    assert "WACC" in out["parsed_hints"]["candidate_terms"]
    assert out["parsed_hints"]["registry_factor_hits"] == []
    notes = " ".join(out["system_facts"].get("implementation_notes") or [])
    assert "WACC" in notes
    assert "未命中已注册实现" in notes
    assert out["system_facts"].get("factor_ready") is False
    assert out["trust"]["banner_status"] == "blocked"
