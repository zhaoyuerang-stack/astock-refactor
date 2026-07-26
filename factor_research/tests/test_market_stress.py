import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factors.market_stress import HMMStressConfig, identify_stress_state, latest_hmm_stress


class DummyModel:
    def __init__(self):
        self.means = np.array(
            [
                [1.0, -0.5, 0.3, 0.7],
                [-1.5, 1.2, -0.8, -1.0],
                [0.0, 0.1, 0.0, 0.0],
            ]
        )


def test_identify_stress_state_uses_lowest_original_risk_appetite():
    train = pd.DataFrame(
        {
            "risk_appetite": [0.2, 0.4, 0.6, 0.8],
            "volatility": [0.01, 0.02, 0.03, 0.04],
            "liquidity": [0.8, 1.0, 1.2, 1.4],
            "ma_diffusion": [0.25, 0.45, 0.65, 0.85],
        }
    )
    assert identify_stress_state(DummyModel(), train) == 1


def test_latest_hmm_stress_returns_probability_without_future_rows():
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2022-01-03", periods=90)
    features = pd.DataFrame(
        {
            "risk_appetite": np.r_[rng.normal(0.55, 0.03, 70), rng.normal(0.2, 0.02, 20)],
            "volatility": np.r_[rng.normal(0.01, 0.002, 70), rng.normal(0.04, 0.003, 20)],
            "liquidity": np.r_[rng.normal(1.1, 0.08, 70), rng.normal(0.65, 0.05, 20)],
            "ma_diffusion": np.r_[rng.normal(0.6, 0.04, 70), rng.normal(0.25, 0.03, 20)],
        },
        index=dates,
    )
    out = latest_hmm_stress(
        features,
        target_date=dates[-1],
        cfg=HMMStressConfig(lookback=60, retrain_days=20, max_iter=5, filter_days=20),
    )
    assert 0.0 <= out["prob_stress"] <= 1.0
    assert out["cache_key"] == 80
