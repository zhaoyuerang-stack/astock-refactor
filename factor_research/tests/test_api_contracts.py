"""API contract smoke tests for frontend-facing shapes.

This intentionally checks a small set of high-risk contracts instead of trying
to replace generated OpenAPI clients. If these drift, the handwritten frontend
mirror must be updated in the same change.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from api.main import app  # noqa: E402
from services.read.backtest import production_defaults  # noqa: E402


def _schema_ref(operation: dict) -> str:
    return operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]


def test_backtest_contract_exposes_dynamic_band_not_fixed_leverage():
    spec = app.openapi()
    params = {
        p["name"]
        for p in spec["paths"]["/backtest/run"]["get"].get("parameters", [])
    }
    assert params == {"start", "top_n", "rebalance_days", "factor_window", "timing_ma"}

    defaults = production_defaults()
    assert "leverage" not in defaults
    assert defaults["exposure_mode"] == "PureTrend MA16 Band dynamic 0-1.5x"


def test_action_endpoints_return_job_contracts():
    spec = app.openapi()
    job_ref = "#/components/schemas/ActionJobView"
    assert _schema_ref(spec["paths"]["/experiments/autoresearch/run-seeds"]["post"]) == job_ref
    assert _schema_ref(spec["paths"]["/experiments/autoresearch/run-llm"]["post"]) == job_ref
    assert _schema_ref(spec["paths"]["/experiments/autoresearch/island-search"]["post"]) == job_ref
    assert _schema_ref(spec["paths"]["/experiments/autoresearch/promote/{fingerprint}"]["post"]) == job_ref
    assert _schema_ref(spec["paths"]["/experiments/jobs/{job_id}"]["get"]) == job_ref


def test_cors_allows_fallback_next_dev_port_3001():
    cors = next(m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware")
    pattern = cors.kwargs["allow_origin_regex"]
    assert pattern == r"http://(localhost|127\.0\.0\.1):\d+"
    assert re.fullmatch(pattern, "http://127.0.0.1:3001").group(0) == "http://127.0.0.1:3001"
    assert re.fullmatch(pattern, "http://localhost:3001").group(0) == "http://localhost:3001"


def test_paper_contract_exposes_settlement_dates():
    spec = app.openapi()
    trade_props = spec["components"]["schemas"]["TradePlanView"]["properties"]
    nav_props = spec["components"]["schemas"]["NavCurveView"]["properties"]

    assert "account_date" in trade_props
    assert "last_exec_signal_date" in trade_props
    assert "latest_nav_date" in nav_props


def test_research_run_index_contract_exists():
    spec = app.openapi()
    assert _schema_ref(spec["paths"]["/experiments/research-runs"]["get"]) == "#/components/schemas/ResearchRunIndexView"


def test_research_workspace_and_strategy_detail_contracts_exist():
    spec = app.openapi()
    assert _schema_ref(spec["paths"]["/experiments/work-items"]["get"]) == "#/components/schemas/ResearchWorkItemListView"
    assert _schema_ref(spec["paths"]["/experiments/work-items/{kind}/{item_id}"]["get"]) == "#/components/schemas/ResearchWorkItemDetailView"
    assert _schema_ref(spec["paths"]["/strategies/{family}/{version}"]["get"]) == "#/components/schemas/StrategyDetailView"
    assert _schema_ref(spec["paths"]["/experiments/drafts"]["post"]) == "#/components/schemas/ResearchDraftView"
    assert _schema_ref(spec["paths"]["/experiments/drafts/{draft_id}"]["patch"]) == "#/components/schemas/ResearchDraftView"
    assert _schema_ref(spec["paths"]["/experiments/work-items/{kind}/{item_id}/reviews"]["post"]) == "#/components/schemas/ResearchReviewView"
    assert _schema_ref(spec["paths"]["/experiments/work-items/{kind}/{item_id}/actions/{action}"]["post"]) == "#/components/schemas/ActionJobView"


if __name__ == "__main__":
    print("Running API contract tests...\n")
    test_backtest_contract_exposes_dynamic_band_not_fixed_leverage()
    print("✅ backtest contract hides fixed leverage and documents dynamic band exposure")
    test_action_endpoints_return_job_contracts()
    print("✅ AutoResearch action endpoints return ActionJobView")
    test_cors_allows_fallback_next_dev_port_3001()
    print("✅ CORS allows fallback Next.js dev port 3001")
    test_paper_contract_exposes_settlement_dates()
    print("✅ Paper contracts expose settlement dates")
    test_research_run_index_contract_exists()
    print("✅ Research run index contract exists")
    test_research_workspace_and_strategy_detail_contracts_exist()
    print("✅ Research workspace and strategy detail contracts exist")
    print("\n🎉 API contract tests passed!")
