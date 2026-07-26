import json

import numpy as np
import pandas as pd

import strategy_registry
from scripts.research.price_unit_blast_radius import (
    compare_strategy_paths,
    invalidation_reasons,
)


def _factor(values):
    return pd.DataFrame(
        values,
        index=pd.to_datetime(["2026-06-17", "2026-06-18"]),
        columns=["A", "B", "C", "D"],
        dtype=float,
    )


def test_comparison_outputs_overlap_spearman_and_metric_deltas():
    old_factor = _factor([[4, 3, 2, 1], [4, 3, 2, 1]])
    new_factor = _factor([[4, 3, 2, 1], [1, 2, 3, 4]])
    dates = pd.bdate_range("2026-01-01", periods=120)
    old_returns = pd.Series(0.001, index=dates)
    new_returns = pd.Series(np.r_[np.repeat(0.001, 60), np.repeat(-0.001, 60)], index=dates)

    comparison = compare_strategy_paths(
        old_factor,
        new_factor,
        old_returns,
        new_returns,
        top_n=2,
    )

    assert comparison["top_n_overlap"] == 0.5
    assert comparison["top_n_jaccard"] == 0.5
    assert comparison["factor_spearman"] == 0.0
    assert "annual_delta" in comparison
    assert "sharpe_delta" in comparison
    assert "maxdd_delta" in comparison
    assert "daily_return_abs_mean" in comparison
    assert "cumulative_return_delta" in comparison


def test_mechanical_invalidation_thresholds():
    reasons = invalidation_reasons({
        "top_n_overlap": 0.79,
        "factor_spearman": 0.94,
        "annual_delta": 0.021,
        "sharpe_delta": 0.11,
        "maxdd_delta": 0.021,
    })
    assert set(reasons) == {
        "top25_overlap_below_0.80",
        "factor_spearman_below_0.95",
        "annual_delta_above_2pct",
        "sharpe_delta_above_0.10",
        "maxdd_delta_above_2pct",
    }


def test_registry_incident_is_appended_without_retiring(tmp_path, monkeypatch):
    registry = tmp_path / "strategy_versions.json"
    registry.write_text(json.dumps({
        "families": [{
            "id": "illiquidity",
            "versions": [{
                "version": "v3.1",
                "status": "在册",
                "evidence": {},
            }],
        }],
    }))
    monkeypatch.setattr(strategy_registry, "REGISTRY", registry)

    strategy_registry.attach_data_incident(
        "illiquidity",
        "v3.1",
        {
            "code": "INVALIDATED_BY_DATA_UNIT_INCIDENT",
            "incident_id": "price-units-20260620",
            "resolved": False,
        },
    )

    version = json.loads(registry.read_text())["families"][0]["versions"][0]
    assert version["status"] == "在册"
    assert version["evidence"]["data_incidents"][0]["resolved"] is False
    assert version["evidence"]["production_blocked"] is True
