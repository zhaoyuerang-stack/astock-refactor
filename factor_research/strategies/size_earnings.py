"""Size + Earnings Growth dual-factor strategy.

Canonical implementation of the size-earnings v1.0 strategy.
Uses core.engine.BacktestEngine as the unified backtest path.

Factor: 0.5 × size(60d amount) + 0.5 × net_profit_yoy (z-scored)
Timing: PureTrend(MA16) × VolTarget(25%/60d)
Leverage: 1.10x
"""
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.composite import size_earnings_factor
from factors.small_cap import small_cap_exposure_signal
from lake.load_lake import load_fundamental_panel, load_prices, load_raw_close

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StrategyConfig:
    family: str = "size-earnings"
    version: str = "v1.0"
    start: str = "2018-01-01"
    size_window: int = 60
    blend_weight: float = 0.5       # λ: weight on size (1-λ on NPY)
    timing_ma: int = 16
    vol_target: float = 0.25        # annualized target vol
    vol_lookback: int = 60           # lookback days for realized vol
    vol_min_exp: float = 0.3         # min exposure multiplier
    vol_max_exp: float = 1.5         # max exposure multiplier
    top_n: int = 25
    rebalance_days: int = 20
    leverage: float = 1.10
    cost: CostModel = CostModel()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_price_panels(start: str = "2010-01-01") -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load close/volume/amount panels.  Amount uses unadjusted price."""
    from lake.units import implied_amount

    px = load_prices(start=start, fields=("close", "volume"))
    raw = load_raw_close(start=start)
    raw = raw.reindex(index=px["volume"].index, columns=px["volume"].columns)
    amount = implied_amount(px["volume"], raw)
    close, volume = px["close"], px["volume"]
    # Truncate tail where raw prices lag (causing NaN amount on latest days)
    valid = amount.notna().sum(axis=1)
    if len(valid) > 5:
        typical = valid.iloc[-60:].median()
        good = valid[valid >= typical * 0.7]
        if len(good):
            cutoff = good.index[-1]
            close, volume, amount = close.loc[:cutoff], volume.loc[:cutoff], amount.loc[:cutoff]
    return close, volume, amount


# ---------------------------------------------------------------------------
# Factor construction
# ---------------------------------------------------------------------------

def build_factor(amount: pd.DataFrame, trade_dates: pd.DatetimeIndex, blend_weight: float = 0.5) -> pd.DataFrame:
    """Build size + NPY blended factor (date×code, z-scored)."""
    fund = load_fundamental_panel(trade_dates, codes=None, fields=["net_profit_yoy"])
    npy = fund.get("net_profit_yoy", pd.DataFrame())
    return size_earnings_factor(
        amount,
        npy,
        size_window=60,
        blend_weight=blend_weight,
    )


# ---------------------------------------------------------------------------
# Vol Target timing
# ---------------------------------------------------------------------------

def build_vol_target(close: pd.DataFrame, amount: pd.DataFrame,
                     target_vol: float = 0.25, lookback: int = 60,
                     min_exp: float = 0.3, max_exp: float = 1.5) -> pd.Series:
    """Build continuous vol-target exposure multiplier.

    Uses small-cap portfolio returns as vol proxy, shifts by 1 to avoid
    look-ahead (T-day exposure uses vol computed through T-1).
    """
    ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    small_mask = amount.rolling(20).mean().rank(axis=1, pct=True) < 0.5
    small_idx = (ret * small_mask).sum(axis=1) / small_mask.sum(axis=1)
    realized_vol = small_idx.rolling(lookback, min_periods=10).std() * np.sqrt(252)
    exposure = target_vol / realized_vol.replace(0, np.nan)
    return exposure.clip(min_exp, max_exp).shift(1)


# ---------------------------------------------------------------------------
# Weight construction
# ---------------------------------------------------------------------------

def build_rebalance_weights(factor: pd.DataFrame, close: pd.DataFrame, top_n: int, rebalance_days: int, *, veto_factor: pd.DataFrame | None = None, veto_q: float = 0.10) -> dict[pd.Timestamp, pd.Series]:
    """Convert factor panel to scheduled target weights.

    ``veto_factor`` is a policy-layer VetoFilter applied to the candidate pool
    before top-N selection (refill from survivors, rebalance-day only).
    """
    fdates = factor.dropna(how="all").index.intersection(close.index)
    if len(fdates) < 100:
        return {}

    weights = {}
    for rd in list(fdates[::rebalance_days]):
        pos = close.index.get_loc(rd)
        if pos + 1 >= len(close.index):
            continue
        effective = close.index[pos + 1]
        f = factor.loc[rd].dropna()
        active = close.loc[rd].dropna().index
        f = f.reindex(active).dropna()
        if veto_factor is not None:
            v = veto_factor.loc[rd].reindex(f.index).dropna()
            if len(v):
                f = f.reindex(v[v > v.quantile(veto_q)].index).dropna()
        if len(f) < top_n:
            continue
        weights[effective] = pd.Series(1.0 / top_n, index=f.nlargest(top_n).index)
    return weights


# ---------------------------------------------------------------------------
# Strategy execution
# ---------------------------------------------------------------------------

def run_strategy(config: StrategyConfig | None = None) -> dict[str, Any]:
    """Run size-earnings strategy via BacktestEngine."""
    config = config or StrategyConfig()
    close, volume, amount = load_price_panels(config.start)
    trade_dates = close.index
    prices = PricePanel(close=close, volume=volume, amount=amount)

    # Factor
    factor = build_factor(amount, trade_dates, blend_weight=config.blend_weight)

    # Timing: PureTrend (direction) × VolTarget (size)
    pt_timing, small_nav, timing_dist = small_cap_exposure_signal(close, amount, config.timing_ma)
    vt = build_vol_target(close, amount, target_vol=config.vol_target,
                          lookback=config.vol_lookback,
                          min_exp=config.vol_min_exp, max_exp=config.vol_max_exp)
    timing = pt_timing.astype(float) * vt

    # Weights
    scheduled = build_rebalance_weights(factor, close, config.top_n, config.rebalance_days)

    # Engine
    engine_config = BacktestConfig(
        start=config.start,
        cost=CostModel(
            buy_cost=config.cost.buy_cost,
            sell_cost=config.cost.sell_cost,
            financing_rate=config.cost.financing_rate,
        ),
        leverage=config.leverage,
    )
    engine = BacktestEngine(prices=prices, config=engine_config)
    signal = Signal(
        weights=scheduled,
        timing=timing,
        family=config.family,
        version=config.version,
    )
    result = engine.run(signal)

    return {
        "close": close,
        "volume": volume,
        "amount": amount,
        "factor": factor,
        "timing": timing,
        "timing_dist": timing_dist,
        "scheduled_weights": scheduled,
        "returns": result.returns,
        "detail": result.detail,
        "engine_result": result,
    }


def latest_signal(config: StrategyConfig | None = None) -> dict[str, Any]:
    """Backward-compatible wrapper for :func:`latest_decision`."""
    return latest_decision(config)


def latest_decision(config: StrategyConfig | None = None) -> dict[str, Any]:
    """Latest signal for live trading."""
    config = config or StrategyConfig()
    result = run_strategy(config)
    close = result["close"]
    factor = result["factor"]
    timing = result["timing"]
    last = close.index[-1]
    f = factor.loc[last].dropna()
    active = close.loc[last].dropna().index
    holdings = f.reindex(active).dropna().nlargest(config.top_n).index.tolist()
    return {
        "date": last,
        "in_market": bool(timing.loc[last] > 0),
        "exposure": float(timing.loc[last]),
        "holdings": holdings,
        "result": result,
    }
