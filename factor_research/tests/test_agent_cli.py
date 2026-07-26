from __future__ import annotations

import json

import pytest

from apps.agent_cli import AgentCliError, call_capability, capability_catalog, main
from services.agent.tools import RISK_HIGH, RISK_READONLY, Tool


def _registry() -> dict[str, Tool]:
    return {
        "profile": Tool("profile", RISK_READONLY, "profile", lambda code: {"code": code}, ("code",)),
        "rebalance": Tool("rebalance", RISK_HIGH, "rebalance", None),
    }


def test_catalog_exposes_executable_capabilities():
    cats = capability_catalog(_registry())
    assert cats[0]["name"] == "profile"
    assert cats[0]["risk"] == "readonly"
    assert cats[0]["arguments"] == ["code"]
    # Non-callable high-risk tools stay out of catalog
    assert all(c["name"] != "rebalance" for c in cats)


def test_call_capability_checks_risk_and_exact_arguments():
    assert call_capability("profile", {"code": "600519"}, _registry()) == {"code": "600519"}

    with pytest.raises(AgentCliError, match="proposal-only|not executable|readonly"):
        call_capability("rebalance", {}, _registry())
    with pytest.raises(AgentCliError, match="unexpected arguments"):
        call_capability("profile", {"code": "600519", "command": "rm"}, _registry())


def test_cli_rejects_non_executable_high_risk(monkeypatch, capsys):
    monkeypatch.setattr("services.agent.capability.tool_registry", _registry)

    assert main(["call", "--tool", "rebalance", "--args-json", "{}"]) == 2
    error = json.loads(capsys.readouterr().err)
    assert "not executable" in error["error"] or "proposal-only" in error["error"]
