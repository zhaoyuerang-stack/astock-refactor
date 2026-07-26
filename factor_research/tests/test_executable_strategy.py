"""Task 6: spec 驱动 builder 必须逐位复现 canonical 公式,无公式漂移。"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.engine import PricePanel
from core.strategy_spec import ExecutableStrategySpec
from strategies.catalog import UnsupportedStrategyComponent
from strategies.executable import build_executable_strategy
from strategies.small_cap import build_rebalance_weights


def _panel(days=240, n=40, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=days)
    cols = [f"{600000+i:06d}" for i in range(n)]
    rets = rng.normal(0.0005, 0.02, size=(days, n))
    close = pd.DataFrame(10.0 * np.cumprod(1 + rets, axis=0), index=idx, columns=cols)
    volume = pd.DataFrame(rng.uniform(1e5, 1e6, size=(days, n)), index=idx, columns=cols)
    amount = close * volume
    return PricePanel(close=close, volume=volume, amount=amount)


def _spec():
    return ExecutableStrategySpec(
        family="illiquidity", version="v3.1",
        universe={"market": "A_SHARE", "exclude_star": False},
        data={"price_units": "shares_yuan"},
        factor={"type": "amihud_illiquidity", "window": 20, "shift": 1, "mad_clip": 5},
        selection={"top_n": 10, "rebalance_days": 20},
        timing={"type": "pure_trend_band", "ma": 16, "cap": 1.5},
        policy={"veto": "salience_covariance", "veto_q": 0.30},
        execution={"fill": "T_PLUS_1_CLOSE", "cost_model": "A_SHARE_STANDARD_V1"},
    )


def test_factor_matches_canonical_expression():
    prices = _panel()
    built = build_executable_strategy(_spec(), prices)

    from factors.alpha.base import FactorData
    from factors.alpha.builtins.illiq import AmihudIlliq
    data = FactorData(close=prices.close, volume=prices.volume, amount=prices.amount)
    direct = AmihudIlliq(window=20).mad_clip(5).zscore().shift(1).compute(data)

    pd.testing.assert_frame_equal(built.factor, direct)


def test_weights_match_direct_rebalance_with_veto():
    prices = _panel()
    spec = _spec()
    built = build_executable_strategy(spec, prices)

    from factors.alpha.base import FactorData
    from factors.alpha.builtins.illiq import AmihudIlliq
    from factors.veto import salience_covariance_veto
    data = FactorData(close=prices.close, volume=prices.volume, amount=prices.amount)
    factor = AmihudIlliq(window=20).mad_clip(5).zscore().shift(1).compute(data)
    veto = salience_covariance_veto(prices.close).shift(1)
    direct = build_rebalance_weights(factor, prices.close, top_n=10, rebalance_days=20,
                                     veto_factor=veto, veto_q=0.30)

    assert built.scheduled_weights.keys() == direct.keys()
    for k in direct:
        pd.testing.assert_series_equal(built.scheduled_weights[k], direct[k])


def test_spec_hash_propagates():
    spec = _spec()
    built = build_executable_strategy(spec, _panel())
    assert built.spec_hash == spec.spec_hash


def test_unknown_component_raises_not_silent():
    bad = _spec().replace(factor={"type": "nonexistent", "shift": 1})
    with pytest.raises(UnsupportedStrategyComponent):
        build_executable_strategy(bad, _panel())


def test_run_daily_uses_canonical_builder_not_formula_copies():
    source = (Path(__file__).resolve().parents[1] / "run_daily.py").read_text()
    assert "build_executable_strategy" in source
    assert "from factors.alpha.builtins.illiq import" not in source
    assert "from factors.veto import" not in source
    assert "from factors.small_cap import small_cap_timing" not in source


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
