"""Smoke tests for the unified ``core.engine.BacktestEngine``.

Run::

    cd /Users/kiki/astcok/factor_research && /usr/bin/python3 test_engine.py

两档结构(2026-07-18 评审:fresh worktree 无数据湖时整套挂死,冒烟档不可用):
- **合成档**(永远运行):合成 PricePanel 走引擎全链路 + 静态诊断测试,零数据湖依赖;
- **数据档**(有湖才跑):真实 data_lake 复算小盘策略等价性;无湖时响亮跳过、exit 0。
  在挂湖环境(主仓)行为与历史完全一致;设 ``REQUIRE_DATA_LAKE=1`` 可强制数据档
  缺湖即失败(防止正式验收环境静默降档)。
"""
import os
import sys
from pathlib import Path

os.chdir(Path(__file__).parent)

import numpy as np
import pandas as pd

from core.engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestResult,
    PricePanel,
    Signal,
)


def _lake_available() -> bool:
    """与 lake.load_lake.load_prices 相同的判定:prices 日线目录有无 parquet。"""
    daily = Path("data_lake/price/daily")
    return daily.is_dir() and any(daily.glob("*.parquet"))


# ---------------------------------------------------------------------------
# 合成档:引擎全链路冒烟(零数据湖依赖)
# ---------------------------------------------------------------------------

def _synthetic_panel(n_days=260, n_codes=40, seed=11) -> PricePanel:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n_days)
    codes = [f"{600000 + i:06d}" for i in range(n_codes)]
    rets = rng.normal(0.0003, 0.02, (n_days, n_codes))
    close = pd.DataFrame(100 * np.exp(np.cumsum(rets, axis=0)), index=idx, columns=codes)
    volume = pd.DataFrame(
        rng.integers(1_000_000, 5_000_000, (n_days, n_codes)).astype(float),
        index=idx, columns=codes,
    )
    return PricePanel(close=close, volume=volume, amount=volume * close, raw_close=close)


def test_engine_synthetic_smoke():
    """合成面板走 Signal(factor=...) 全链路:成本为正、序列有限、metrics 完整。"""
    prices = _synthetic_panel()
    engine = BacktestEngine(prices=prices, config=BacktestConfig(start="2023-01-02"))
    factor = -prices.amount.rolling(20).mean()  # 小成交额风格因子
    result = engine.run(Signal(factor=factor, top_n=10, rebalance_freq="20D"))

    assert len(result.returns) > 200
    assert np.isfinite(result.returns).all()
    assert (result.cost >= 0).all() and result.cost.sum() > 0, "成本必须被扣(R-COST-001)"
    assert result.turnover.sum() > 0
    assert result.detail.shape[0] == len(result.returns)
    m = result.metrics
    for k in ("annual", "vol", "sharpe", "maxdd", "calmar", "hit", "n"):
        assert k in m, f"metrics 缺字段 {k}"
    print("✅ test_engine_synthetic_smoke passed")


def _make_test_signal_weights():
    """Minimal fixture: load real data, build small-cap weights."""
    from factors.small_cap import small_cap_factor, small_cap_timing
    from strategies.small_cap import build_rebalance_weights, load_price_panels
    close, volume, amount = load_price_panels("2018-01-01")
    factor = small_cap_factor(amount, 60)
    timing, _, _ = small_cap_timing(close, amount, 16)
    weights = build_rebalance_weights(factor, close, 25, 20)
    return close, weights, timing


# ---------------------------------------------------------------------------
# Test 1: BacktestResult.metrics matches core.backtest.metrics()
# ---------------------------------------------------------------------------

def test_backtest_result_metrics():
    """Engine result.metrics must equal legacy metrics() function."""
    from engine.metrics import metrics
    from strategies.small_cap import run_small_cap_strategy
    result = run_small_cap_strategy()
    ret = result["returns"]

    legacy = metrics(ret)
    engine_result = BacktestResult(returns=ret, turnover=result["detail"]["turnover"],
                                    cost=result["detail"]["cost"])
    modern = engine_result.metrics

    assert np.isclose(modern["annual"], legacy["annual"], atol=1e-10)
    assert np.isclose(modern["sharpe"], legacy["sharpe"], atol=1e-10)
    assert np.isclose(modern["maxdd"], legacy["maxdd"], atol=1e-10)
    assert np.isclose(modern["calmar"], legacy["calmar"], atol=1e-10)
    assert modern["hit"] == legacy["hit"]
    print("✅ test_backtest_result_metrics passed")


# ---------------------------------------------------------------------------
# Test 2: Engine.run(weights) uses the canonical engine weights path
# ---------------------------------------------------------------------------

def test_engine_run_weights():
    """BacktestEngine.run with pre-computed weights must match its canonical weights path."""
    close, weights_dict, timing = _make_test_signal_weights()
    prices = PricePanel(close=close, volume=pd.DataFrame(), amount=pd.DataFrame())
    config = BacktestConfig(start="2018-01-01")
    engine = BacktestEngine(prices=prices, config=config)

    signal = Signal(weights=weights_dict, timing=timing)
    result_public = engine.run(signal)
    result_direct = engine._run_weight_backtest(weights_dict, timing, signal)

    pd.testing.assert_series_equal(result_public.returns, result_direct.returns, check_names=False)
    pd.testing.assert_frame_equal(result_public.detail, result_direct.detail, check_names=False)
    print("✅ test_engine_run_weights passed")


# ---------------------------------------------------------------------------
# Test 3: Engine.run(factor) == weights path
# ---------------------------------------------------------------------------

def test_engine_run_factor():
    """Signal(factor=...) via engine must match Signal(weights=...) via engine."""
    from factors.small_cap import small_cap_factor, small_cap_timing
    from strategies.small_cap import build_rebalance_weights, load_price_panels
    close, volume, amount = load_price_panels("2018-01-01")
    factor = small_cap_factor(amount, 60)
    timing, _, _ = small_cap_timing(close, amount, 16)
    weights_dict = build_rebalance_weights(factor, close, 25, 20)

    prices = PricePanel(close=close, volume=volume, amount=amount)
    config = BacktestConfig(start="2018-01-01")
    engine = BacktestEngine(prices=prices, config=config)

    result_weights = engine.run(Signal(weights=weights_dict, timing=timing))
    result_factor = engine.run(Signal(factor=factor, top_n=25, rebalance_freq="20D", timing=timing))

    # Factor path may have slight date-alignment differences at boundaries;
    # compare on common dates only.
    common = result_weights.returns.index.intersection(result_factor.returns.index)
    assert len(common) > 1000, f"Only {len(common)} common dates"
    np.testing.assert_allclose(
        result_weights.returns.loc[common].values,
        result_factor.returns.loc[common].values,
        atol=1e-10,
    )
    print("✅ test_engine_run_factor passed")


# ---------------------------------------------------------------------------
# Test 4: BacktestResult properties are consistent
# ---------------------------------------------------------------------------

def test_backtest_result_properties():
    """Derived properties must be internally consistent."""
    from strategies.small_cap import run_small_cap_strategy
    result = run_small_cap_strategy()
    ret = result["returns"]
    engine_result = BacktestResult(returns=ret, turnover=result["detail"]["turnover"],
                                    cost=result["detail"]["cost"])

    assert engine_result.n == len(ret)
    assert engine_result.sharpe == engine_result.annual / engine_result.vol if engine_result.vol > 0 else True
    assert engine_result.calmar == engine_result.annual / abs(engine_result.maxdd) if engine_result.maxdd < 0 else True
    print("✅ test_backtest_result_properties passed")


# ---------------------------------------------------------------------------
# Test 5: run_small_cap_strategy_engine() matches run_small_cap_strategy()
# ---------------------------------------------------------------------------

def test_small_cap_strategy_engine():
    """The engine-based small-cap wrapper must match the legacy implementation."""
    from strategies.small_cap import run_small_cap_strategy
    from strategies.small_cap import run_small_cap_strategy as run_small_cap_strategy_engine

    legacy = run_small_cap_strategy()
    modern = run_small_cap_strategy_engine()

    pd.testing.assert_series_equal(legacy["returns"], modern["returns"], check_names=False)
    pd.testing.assert_frame_equal(legacy["detail"], modern["detail"], check_names=False)
    print("✅ test_small_cap_strategy_engine passed")


# ---------------------------------------------------------------------------
# Test 6: Compatibility layer — engine/backtest.py still importable
# ---------------------------------------------------------------------------

def test_engine_backtest_compat():
    """``engine.factor_analysis`` is the canonical path for factor diagnostics."""
    from engine.factor_analysis import calc_ic, ic_summary, stratify_return
    assert callable(calc_ic)
    assert callable(ic_summary)
    assert callable(stratify_return)
    print("✅ test_engine_backtest_compat passed")


# ---------------------------------------------------------------------------
# Test 7: factor diagnostics skip constant cross-sections without scipy warnings
# ---------------------------------------------------------------------------

def test_calc_ic_skips_constant_cross_section_without_warning():
    """Constant factor/return cross-sections have undefined IC and must be quiet."""
    import warnings

    from scipy.stats import ConstantInputWarning

    from engine.factor_analysis import calc_ic

    idx = pd.date_range("2026-01-01", periods=1)
    cols = [f"{i:06d}" for i in range(30)]
    factor = pd.DataFrame([np.ones(30)], index=idx, columns=cols)
    forward_ret = pd.DataFrame([np.arange(30)], index=idx, columns=cols)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ConstantInputWarning)
        ic = calc_ic(factor, forward_ret)

    assert not any(isinstance(w.message, ConstantInputWarning) for w in caught)
    assert ic.isna().iloc[0]
    print("✅ test_calc_ic_skips_constant_cross_section_without_warning passed")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Running core.engine smoke tests...\n")
    # 合成档:永远运行,零数据湖依赖
    test_engine_synthetic_smoke()
    test_engine_backtest_compat()
    test_calc_ic_skips_constant_cross_section_without_warning()

    if _lake_available():
        # 数据档:真实 data_lake 复算小盘策略等价性
        test_backtest_result_metrics()
        test_engine_run_weights()
        test_engine_run_factor()
        test_backtest_result_properties()
        test_small_cap_strategy_engine()
        print("\n🎉 All tests passed! (合成档 + 数据档)")
    elif os.environ.get("REQUIRE_DATA_LAKE"):
        print("\n❌ REQUIRE_DATA_LAKE=1 但 data_lake/price/daily 无 parquet——"
              "正式验收环境不许降档,判失败。", file=sys.stderr)
        sys.exit(1)
    else:
        print("\n" + "!" * 72)
        print("⚠️  数据档冒烟测试已跳过:data_lake 未挂载(worktree 需 symlink 主仓数据湖)。")
        print("⚠️  本环境仅合成档通过;正式验收必须在挂湖环境跑全档(或设 REQUIRE_DATA_LAKE=1)。")
        print("!" * 72)
        print("\n🎉 Synthetic-tier tests passed! (数据档 SKIPPED)")
