"""M2 Mother Strategy: Large-cap Growth+Valuation Hedged + Hysteresis Timing.

Implementation of the second mother strategy family.
"""
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.large_cap import (
    build_large_cap_premium_factor,
    large_cap_timing_hysteresis,
    load_clean_panels_with_growth,
)
from strategies.small_cap import build_rebalance_weights


@dataclass(frozen=True)
class StrategyConfig:
    family: str = "large-cap-growth-hedged"
    version: str = "v1.0"
    start: str = "2010-01-01"
    universe_size: int = 200
    top_n: int = 25
    rebalance_days: int = 40
    leverage: float = 1.0
    ma_window: int = 120
    buffer_size: float = 0.01
    hedge_cost_annual: float = 0.015
    switch_friction: float = 0.0025
    w_cpv_max: float = 0.0  # Weight for adaptive CPV penalty (0.0 for v1.0, 0.5 for v1.1)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# B008 修复:frozen dataclass 不可变,模块级单例做缺省,与旧内联缺省语义等价。
_DEFAULT_CONFIG = StrategyConfig()


def run_large_cap_strategy(config: StrategyConfig = _DEFAULT_CONFIG) -> dict[str, Any]:
    """Runs the complete Large-cap Growth Hedged strategy.
    
    1. Runs unified engine on growth premium long portfolio.
    2. Subtracts shifted Top 200 equal-weighted index (including 1.5% hedge cost).
    3. Applies hysteresis timing signal on NAV (with 0.25% transition friction).
    4. Slices outputs to config.start (ensuring proper MA warm-up).
    """
    # Load data
    panels = load_clean_panels_with_growth()
    close = panels["close"]
    volume = panels["amount"] * 0.0
    amount = panels["amount"]
    raw_close = panels["raw_close"]
    prices = PricePanel(close=close, volume=volume, amount=amount, raw_close=raw_close)

    # Build factor (with optional adaptive CPV penalty)
    comp_premium, univ = build_large_cap_premium_factor(
        panels, universe_size=config.universe_size, w_cpv_max=config.w_cpv_max
    )

    # Scheduled weights
    scheduled = build_rebalance_weights(comp_premium, close, config.top_n, config.rebalance_days)
    
    # Engine configuration (Always run from 2010-01-01 to warm up MAs)
    engine_config = BacktestConfig(
        start="2010-01-01",
        cost=CostModel(buy_cost=0.0, sell_cost=0.0, financing_rate=0.0),
        leverage=config.leverage,
    )
    engine = BacktestEngine(prices=prices, config=engine_config)
    long_signal = Signal(weights=scheduled, timing=None)
    res_long = engine.run(long_signal)

    # Compute Universe Benchmark (Shifted 1 Day to avoid leak)
    daily_ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    bench_returns = pd.Series(0.0, index=daily_ret.index)
    univ_shifted = univ.shift(1)
    for dt in daily_ret.index:
        active = univ_shifted.loc[dt] if dt in univ_shifted.index else pd.Series(False, index=univ.columns)
        active = active.fillna(False).astype(bool)
        active_codes = active[active].index
        if len(active_codes) > 0:
            bench_returns.loc[dt] = daily_ret.loc[dt, active_codes].mean()

    # Align dates
    common_idx = res_long.returns.index.intersection(bench_returns.index)
    r_long = res_long.returns.loc[common_idx]
    r_bench = bench_returns.loc[common_idx]

    # Hedged long-short return
    daily_hedge_cost = config.hedge_cost_annual / 252.0
    r_neutral = r_long - r_bench - daily_hedge_cost

    # Hysteresis timing on neutral NAV
    nav_neutral = (1 + r_neutral).cumprod()
    timing_signal = large_cap_timing_hysteresis(nav_neutral, window=config.ma_window, buffer=config.buffer_size)

    # Timed returns (including switch friction)
    transitions = timing_signal.diff().fillna(0.0) != 0.0
    r_timed = r_neutral * timing_signal - config.switch_friction * transitions

    # Slice outputs to the requested start date to ensure MA warm-up
    start_dt = pd.Timestamp(config.start)
    r_timed_sliced = r_timed.loc[start_dt:]
    r_long_sliced = r_long.loc[start_dt:]
    r_bench_sliced = r_bench.loc[start_dt:]
    r_neutral_sliced = r_neutral.loc[start_dt:]
    timing_signal_sliced = timing_signal.loc[start_dt:]

    return {
        "returns": r_timed_sliced,
        "long_returns": r_long_sliced,
        "bench_returns": r_bench_sliced,
        "neutral_returns": r_neutral_sliced,
        "timing": timing_signal_sliced,
        "scheduled_weights": scheduled,
        "factor": comp_premium,
        "close": close,
        "engine_result": res_long,
    }

def latest_signal(config: StrategyConfig = _DEFAULT_CONFIG) -> dict[str, Any]:
    """Backward-compatible wrapper for :func:`latest_decision`."""
    return latest_decision(config)


def latest_decision(config: StrategyConfig = _DEFAULT_CONFIG) -> dict[str, Any]:
    """Returns the latest signal and holdings for live trading."""
    # Run strategy
    result = run_large_cap_strategy(config)
    
    close = result["close"]
    comp_premium = result["factor"]
    
    last = close.index[-1]
    f = comp_premium.loc[last].dropna()
    active = close.loc[last].dropna().index
    holdings = f.reindex(active).dropna().nlargest(config.top_n).index.tolist()
    
    # Timing status
    in_market = bool(result["timing"].loc[last])
    
    return {
        "date": last,
        "in_market": in_market,
        "holdings": holdings,
        "result": result,
    }
