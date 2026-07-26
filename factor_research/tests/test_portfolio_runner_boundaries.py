"""生产部署事实与研究目录必须保持硬边界。"""
from types import SimpleNamespace

import pandas as pd


def test_paused_deployment_does_not_fall_back_to_research_catalog(monkeypatch):
    from portfolio import strategy_runners
    from runtime import deployment

    def not_ready():
        raise deployment.DeploymentNotReady("paused")

    monkeypatch.setattr(deployment, "load_active_deployment", not_ready)
    monkeypatch.setattr(
        strategy_runners,
        "RESEARCH_STRATEGY_CATALOG",
        {
            "research-active.v1": {
                "status": "ACTIVE",
                "role": "defensive",
                "fn": lambda _start: pd.Series([0.01]),
            }
        },
    )

    assert strategy_runners.run_active() == {}
    assert strategy_runners.active_strategies() == []
    assert strategy_runners.defensive_strategies() == set()
    assert list(strategy_runners.run_research_active()) == ["research-active.v1"]
    assert strategy_runners.research_active_strategies() == ["research-active.v1"]
    assert strategy_runners.research_defensive_strategies() == {"research-active.v1"}


def test_active_deployment_still_returns_manifest_legs(monkeypatch):
    from portfolio import strategy_runners
    from runtime import deployment

    manifest = SimpleNamespace(
        legs=[SimpleNamespace(family="toy", version="v1", role="defensive")]
    )
    monkeypatch.setattr(deployment, "load_active_deployment", lambda: manifest)

    assert strategy_runners.active_strategies() == ["toy.v1"]
    assert strategy_runners.defensive_strategies() == {"toy.v1"}
