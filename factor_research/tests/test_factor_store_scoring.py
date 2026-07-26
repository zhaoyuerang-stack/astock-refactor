"""Factor Store scoring tests.

Run:
    cd factor_research && python3 -m pytest tests/test_factor_store_scoring.py -q
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factor_store import (  # noqa: E402
    evaluate_factor_panel,
    factor_panel_correlation,
    load_factor_score,
    save_factor_panel,
    save_factor_score,
)


def _zrow(df: pd.DataFrame) -> pd.DataFrame:
    return df.sub(df.mean(axis=1), axis=0).div(df.std(axis=1) + 1e-12, axis=0)


def _synthetic(seed: int = 7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-01-02", periods=90)
    codes = [f"{600000 + i:06d}" for i in range(48)]
    factor = pd.DataFrame(rng.normal(0, 1, (len(dates), len(codes))), index=dates, columns=codes)
    neutralizer = _zrow(factor * 0.55 + pd.DataFrame(
        rng.normal(0, 1, factor.shape), index=dates, columns=codes
    ))
    alpha = 0.07 * _zrow(factor) + pd.DataFrame(
        rng.normal(0, 0.01, factor.shape), index=dates, columns=codes
    )
    close = 100 * (1 + alpha.shift(1).fillna(0)).cumprod()
    return factor, close, {"style": neutralizer}


def test_evaluate_factor_panel_outputs_rank_ic_decay_turnover_and_neutralization():
    factor, close, neutralizers = _synthetic()

    score = evaluate_factor_panel(
        factor,
        close,
        factor_id="unit_factor",
        horizons=(1, 5, 10),
        primary_horizon=1,
        neutralizers=neutralizers,
        n_quantile=5,
        top_quantile=0.2,
    )

    assert score.factor_id == "unit_factor"
    assert score.primary_horizon == 1
    assert score.ic_count >= 70
    assert score.ic_mean > 0.70
    assert score.nw_icir > 2.0
    assert set(score.ic_decay) == {1, 5, 10}
    assert score.monotonicity_corr > 0.9
    assert 0.0 <= score.turnover_mean <= 1.0
    assert score.neut_nw_icir is not None
    assert score.icir_retention is not None
    assert score.to_dict()["factor_id"] == "unit_factor"


def test_factor_panel_correlation_detects_clones_and_unrelated_panels():
    factor, _, _ = _synthetic()
    clone = factor * 2.0
    unrelated = pd.DataFrame(
        np.random.default_rng(99).normal(0, 1, factor.shape),
        index=factor.index,
        columns=factor.columns,
    )

    corr = factor_panel_correlation({
        "base": factor,
        "clone": clone,
        "unrelated": unrelated,
    })

    assert corr.loc["base", "base"] == 1.0
    assert corr.loc["base", "clone"] > 0.99
    assert abs(corr.loc["base", "unrelated"]) < 0.20


def test_save_and_load_factor_score_next_to_factor_panel(tmp_path):
    factor, close, _ = _synthetic()
    manifest = save_factor_panel(
        factor,
        factor_name="unit_factor",
        params={"window": 1},
        data_vintage="unit-vintage#score",
        store_root=tmp_path,
    )
    score = evaluate_factor_panel(
        factor,
        close,
        factor_id=manifest.factor_id,
        horizons=(1, 5),
        primary_horizon=1,
    )

    save_factor_score(score, store_root=tmp_path)
    loaded = load_factor_score(manifest.factor_id, store_root=tmp_path)

    assert loaded.factor_id == manifest.factor_id
    assert loaded.ic_decay == score.ic_decay
    assert loaded.ic_mean == score.ic_mean
