"""M4 Strategy: High-Quality Momentum Hedged.

Long top N highest smooth momentum stocks from a high-quality fundamental universe, hedged with CSI 800 equal-weighted index.
"""
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.hq_momentum import build_hq_momentum_factor
from strategies.small_cap import build_rebalance_weights


@dataclass(frozen=True)
class StrategyConfig:
    family: str = "hq-momentum-hedged"
    version: str = "v1.0"
    start: str = "2012-01-01"
    universe_size: int = 800
    lookback: int = 60
    q_filter_threshold: float = 0.60  # Only keep top 40% quality (1 - threshold)
    top_n: int = 25
    rebalance_days: int = 20
    leverage: float = 1.0
    hedge_cost_annual: float = 0.015

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# B008 修复:frozen dataclass 不可变,模块级单例做缺省,与旧内联缺省语义等价。
_DEFAULT_CONFIG = StrategyConfig()


def run_hq_momentum_strategy(config: StrategyConfig = _DEFAULT_CONFIG) -> dict[str, Any]:
    """Runs the complete High-Quality Momentum Hedged strategy."""
    from factors.large_cap import load_clean_panels_with_growth
    
    # Load data
    panels = load_clean_panels_with_growth()
    close = panels["close"]
    volume = panels["amount"] * 0.0
    amount = panels["amount"]
    raw_close = panels["raw_close"]
    prices = PricePanel(close=close, volume=volume, amount=amount, raw_close=raw_close)

    # Build factor
    comp_factor, univ = build_hq_momentum_factor(
        panels, 
        universe_size=config.universe_size, 
        lookback=config.lookback, 
        q_filter_threshold=config.q_filter_threshold
    )

    # Scheduled weights
    scheduled = build_rebalance_weights(comp_factor, close, config.top_n, config.rebalance_days)
    
    # Engine configuration
    engine_config = BacktestConfig(
        start="2010-01-01",  # Warm-up from 2010
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

    # Slice outputs to the requested start date
    start_dt = pd.Timestamp(config.start)
    
    return {
        "returns": r_neutral.loc[start_dt:],
        "long_returns": r_long.loc[start_dt:],
        "bench_returns": r_bench.loc[start_dt:],
        "scheduled_weights": scheduled,
        "factor": comp_factor,
        "close": close,
        "engine_result": res_long,
    }

def latest_signal(config: StrategyConfig = _DEFAULT_CONFIG) -> dict[str, Any]:
    """Backward-compatible wrapper for :func:`latest_decision`."""
    return latest_decision(config)


def latest_decision(config: StrategyConfig = _DEFAULT_CONFIG) -> dict[str, Any]:
    """Returns the latest signal and holdings for live trading."""
    result = run_hq_momentum_strategy(config)
    
    close = result["close"]
    comp_factor = result["factor"]
    
    last = close.index[-1]
    f = comp_factor.loc[last].dropna()
    active = close.loc[last].dropna().index
    holdings = f.reindex(active).dropna().nlargest(config.top_n).index.tolist()
    
    return {
        "date": last,
        "in_market": True,
        "holdings": holdings,
        "result": result,
    }
