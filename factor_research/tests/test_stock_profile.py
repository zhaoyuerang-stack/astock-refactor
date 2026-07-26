"""Stock profile read service and Agent routing tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from services.agent.planner import ask
from services.read.stocks import stock_profile


def test_stock_profile_reads_price_and_basic_data():
    profile = stock_profile("600519")

    assert profile["code"] == "600519"
    assert profile["name"]
    assert profile["latest_price"]["date"]
    assert profile["latest_price"]["close"] > 0
    assert "price/daily/600519.parquet" in profile["data_sources"]


def test_stock_profile_reports_unadjusted_price_not_backadjusted():
    # 铁律3:展示的应是不复权真实股价(总市值/总股本),不是 price/daily 的后复权价
    profile = stock_profile("600519")
    raw = profile["price_cny"]
    adj = profile["latest_price"]["close"]
    assert raw is not None and raw > 0
    assert profile["latest_price"]["close_is_adjusted"] is True
    assert raw != adj                     # 后复权价 ≠ 真实股价
    assert any("后复权" in w for w in profile["warnings"])


def test_agent_routes_stock_question_to_stock_profile():
    r = ask("600519 最近情况怎么样", {"current_page": "overview"})

    assert r["tool"] == "stock_profile"
    assert "600519" in r["output"]["summary"]
    assert "runtime" in r["output"]["source_types"]
    assert not any(c["source_type"] in {"research", "system_manual", "rules"} for c in r["output"]["citations"])


if __name__ == "__main__":
    from providers.llm_adapter import NullAdapter
    from services.agent import skills
    skills.get_adapter = lambda: NullAdapter()   # 路由/摘要确定性,不依赖外部 LLM

    test_stock_profile_reads_price_and_basic_data()
    test_stock_profile_reports_unadjusted_price_not_backadjusted()
    test_agent_routes_stock_question_to_stock_profile()
    print("Stock profile tests passed.")
