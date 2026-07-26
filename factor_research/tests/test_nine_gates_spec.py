"""Unit test for spec-driven 9-Gate evaluation in run_nine_gates_all.py."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workflow.nine_gate_runner import _load_spec_from_registry, run_evaluation


def test_load_spec_from_registry():
    mock_registry_data = {
        "families": [
            {
                "id": "mock-family",
                "name": "Mock Family",
                "hypothesis": "Mock economic hypothesis",
                "versions": [
                    {
                        "version": "v1.0",
                        "status": "在册",
                        "executable_spec": {
                            "spec": {
                                "family": "mock-family",
                                "version": "v1.0",
                                "universe": {"market": "A_SHARE", "exclude_star": False},
                                "data": {"price_units": "shares_yuan", "warmup_start": "2024-01-01"},
                                "factor": {"type": "small_cap_amount", "window": 20, "shift": 1},
                                "selection": {"top_n": 5, "rebalance_days": 10},
                                "timing": {"type": "ma_trend", "ma": 10},
                                "policy": {"veto": "none"},
                                "execution": {"fill": "T_PLUS_1_CLOSE", "cost_model": "A_SHARE_STANDARD_V1"}
                            },
                            "spec_hash": "dummy_hash"
                        }
                    }
                ]
            }
        ]
    }
    
    with patch("strategy_registry._load", return_value=mock_registry_data):
        spec = _load_spec_from_registry("mock-family", "v1.0")
        assert spec is not None
        assert spec["family"] == "mock-family"
        assert spec["version"] == "v1.0"
        
        # Test default version fallback
        spec_default = _load_spec_from_registry("mock-family", None)
        assert spec_default is not None
        assert spec_default["version"] == "v1.0"


def test_run_evaluation_with_spec():
    mock_registry_data = {
        "families": [
            {
                "id": "mock-family",
                "name": "Mock Family",
                "hypothesis": "Mock economic hypothesis",
                "versions": [
                    {
                        "version": "v1.0",
                        "status": "在册",
                        "executable_spec": {
                            "spec": {
                                "family": "mock-family",
                                "version": "v1.0",
                                "universe": {"market": "A_SHARE", "exclude_star": False},
                                "data": {"price_units": "shares_yuan", "warmup_start": "2024-01-01"},
                                "factor": {"type": "small_cap_amount", "window": 20, "shift": 1},
                                "selection": {"top_n": 5, "rebalance_days": 10},
                                "timing": {"type": "ma_trend", "ma": 10},
                                "policy": {"veto": "none"},
                                "execution": {"fill": "T_PLUS_1_CLOSE", "cost_model": "A_SHARE_STANDARD_V1"}
                            },
                            "spec_hash": "dummy_hash"
                        }
                    }
                ]
            }
        ]
    }
    
    # Create mock price panels to avoid actual data lake loading
    dates = pd.bdate_range("2024-01-01", periods=100)
    codes = ["000001", "000002", "000003", "000004", "000005"]
    rng = np.random.default_rng(42)
    close = pd.DataFrame(10.0 * np.exp(np.cumsum(rng.normal(0, 0.01, (100, 5)), axis=0)), index=dates, columns=codes)
    volume = pd.DataFrame(1000, index=dates, columns=codes)
    amount = volume * close
    
    mock_prices = (close, volume, amount)
    
    with patch("strategy_registry._load", return_value=mock_registry_data), \
         patch("strategies.small_cap.load_price_panels", return_value=mock_prices), \
         patch("strategy_registry.attach_nine_gate") as mock_attach, \
         patch("workflow.nine_gate_runner._family_n_trials", return_value=5):
         
        summary = run_evaluation("mock-family", version="v1.0", persist=True)
        assert summary is not None
        assert summary["strategy"] == "mock-family"
        assert summary["version"] == "v1.0"
        
        # Verify that registry attach_nine_gate was called since persist=True
        mock_attach.assert_called_once()
        args, kwargs = mock_attach.call_args
        assert args[0] == "mock-family"
        assert args[1] == "v1.0"
        assert "dsr_p" in args[2]
