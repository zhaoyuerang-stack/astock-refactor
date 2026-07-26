"""M5 Strategy: directed Likelihood Estimation Spectral Clustering (d-LE-SC) Hedged.

Long top N highest expected return laggers identified by the d-LE-SC clustering network,
hedged with the universe equal-weighted benchmark.
"""

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.d_le_sc import build_d_le_sc_factor
from lake.load_lake import load_prices, load_raw_close


@dataclass(frozen=True)
class StrategyConfig:
    family: str = "d-le-sc-hedged"
    version: str = "v1.0"
    start: str = "2018-01-01"
    universe_size: int = 800
    lookback: int = 60
    network_type: str = "overnight_lead_daytime"
    correlation_method: str = "pearson"
    top_n: int = 25
    rebalance_days: int = 20
    leverage: float = 1.0
    buy_cost: float = 0.00225
    sell_cost: float = 0.00275
    hedge_cost_annual: float = 0.015

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# B008 修复:frozen dataclass 不可变,模块级单例做缺省,与旧内联缺省语义等价。
_DEFAULT_CONFIG = StrategyConfig()


def build_d_le_sc_weights(factor: pd.DataFrame, close: pd.DataFrame, top_n: int) -> dict:
    """Converts the d-LE-SC factor panel into target weights.

    Only rebalances on dates where the factor has non-NaN values.
    """
    fdates = factor.dropna(how="all").index.intersection(close.index)
    weights = {}
    for rd in fdates:
        pos = close.index.get_loc(rd)
        if pos + 1 >= len(close.index):
            continue
        effective = close.index[pos + 1]
        f = factor.loc[rd].dropna()
        active = close.loc[rd].dropna().index
        f = f.reindex(active).dropna()
        if len(f) < top_n:
            continue
        weights[effective] = pd.Series(1.0 / top_n, index=f.nlargest(top_n).index)
    return weights


def run_d_le_sc_strategy(config: StrategyConfig = _DEFAULT_CONFIG) -> dict[str, Any]:
    """Runs the complete d-LE-SC Hedged strategy."""
    # Compute start date for loading data to allow warm-up lookback
    start_dt = pd.Timestamp(config.start)
    load_start_dt = start_dt - pd.Timedelta(days=120)
    load_start_str = load_start_dt.strftime("%Y-%m-%d")

    # Load prices: open, close, volume, amount
    px = load_prices(start=load_start_str, fields=("open", "close", "volume", "amount"))
    raw_close = load_raw_close(start=load_start_str)

    close = px["close"]
    volume = px["volume"]
    amount = px["amount"]
    open_px = px["open"]

    # Align dates and columns
    common_idx = close.index
    if not raw_close.empty:
        raw_close = raw_close.reindex(index=common_idx, columns=close.columns)
    else:
        raw_close = close.copy()

    panels = {
        "open": open_px.reindex(index=common_idx, columns=close.columns),
        "close": close,
        "volume": volume,
        "amount": amount,
        "raw_close": raw_close,
    }

    # Build factor (passing network_type and rebalance_days)
    factor, univ = build_d_le_sc_factor(
        panels,
        universe_size=config.universe_size,
        lookback=config.lookback,
        network_type=config.network_type,
        correlation_method=config.correlation_method,
        rebalance_days=config.rebalance_days,
    )

    # Build scheduled weights using our custom builder
    scheduled = build_d_le_sc_weights(factor, close, config.top_n)

    # Configure engine
    prices = PricePanel(close=close, volume=volume, amount=amount, raw_close=raw_close)
    engine_config = BacktestConfig(
        start=config.start,
        cost=CostModel(
            buy_cost=config.buy_cost,
            sell_cost=config.sell_cost,
            financing_rate=0.0,
        ),
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
        "factor": factor,
        "close": close,
        "engine_result": res_long,
    }


def latest_signal(config: StrategyConfig = _DEFAULT_CONFIG) -> dict[str, Any]:
    """Backward-compatible wrapper for :func:`latest_decision`."""
    return latest_decision(config)


def latest_decision(config: StrategyConfig = _DEFAULT_CONFIG) -> dict[str, Any]:
    """Returns the latest signal and holdings for live trading."""
    result = run_d_le_sc_strategy(config)
    close = result["close"]
    factor = result["factor"]

    last = close.index[-1]
    f = factor.loc[last].dropna()
    active = close.loc[last].dropna().index
    holdings = f.reindex(active).dropna().nlargest(config.top_n).index.tolist()

    return {
        "date": last,
        "in_market": True,
        "holdings": holdings,
        "result": result,
    }
