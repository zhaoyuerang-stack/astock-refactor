"""Small-cap size strategy implementation.

This is the canonical implementation of the small-cap-size v2.0 strategy.
It uses core.engine.BacktestEngine as the unified backtest path.
"""
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.small_cap import (  # noqa: F401  small_cap_timing re-exported for back-compat
    small_cap_exposure_signal,
    small_cap_factor,
    small_cap_timing,
)
from lake.load_lake import load_prices, load_raw_close
from lake.units import implied_amount
from research_toolkit import apply_veto_filter

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StrategyConfig:
    family: str = "small-cap-size"
    version: str = "v2.0"
    start: str = "2018-01-01"
    size_window: int = 60
    timing_ma: int = 16
    top_n: int = 25
    rebalance_days: int = 20
    leverage: float = 1.25
    cost: CostModel = CostModel()
    exclude_star: bool = True  # 排除科创板(688):保留验证过的口径(50万门槛/20cm,tradability 受限);
                               # 修复 688 amount bug 后它们会入选,纳入与否是显式策略决策(默认排除,待专项验证)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# B008 修复:frozen dataclass 实例不可变,模块级单例做缺省与
# 原来的默认 StrategyConfig() 语义逐字等价(本就共享同一实例)。
_DEFAULT_CONFIG = StrategyConfig()


def _drop_star(*panels: pd.DataFrame) -> list[pd.DataFrame]:
    """从价量面板剔除科创板(688)列 —— 显式 universe 策略,不依赖数据 bug 隐式排除。"""
    out = []
    for p in panels:
        star = [c for c in p.columns if str(c).startswith("688")]
        out.append(p.drop(columns=star) if star else p)
    return out


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_price_panels(start: str = "2010-01-01") -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load canonical close/volume/amount panels.

    The lake amount field is authoritative. Missing cells are repaired from
    canonical volume(shares) × raw_close(CNY/share), without board-specific
    unit branches.
    """
    px = load_prices(start=start, fields=("close", "volume", "amount"))
    raw = load_raw_close(start=start)
    close, volume = px["close"], px["volume"]
    amount = px["amount"].copy()
    if not raw.empty:
        amount = amount.combine_first(implied_amount(volume, raw))
    # Truncate tail where raw prices may lag (causing NaN amount on latest days)
    valid = amount.notna().sum(axis=1)
    if len(valid) > 5:
        typical = valid.iloc[-60:].median()
        good = valid[valid >= typical * 0.7]
        if len(good):
            cutoff = good.index[-1]
            close, volume, amount = close.loc[:cutoff], volume.loc[:cutoff], amount.loc[:cutoff]
    return close, volume, amount


# ---------------------------------------------------------------------------
# Weight construction
# ---------------------------------------------------------------------------

def build_rebalance_weights(factor: pd.DataFrame, close: pd.DataFrame, top_n: int, rebalance_days: int, *, veto_factor: pd.DataFrame | None = None, veto_q: float = 0.10) -> dict[pd.Timestamp, pd.Series]:
    """Convert factor panel to scheduled target weights.

    ``veto_factor`` is a policy-layer VetoFilter. It filters the candidate pool
    before top-N selection and then refills from survivors; it never reduces
    target position count or acts between rebalance dates.
    """
    fdates = factor.dropna(how="all").index.intersection(close.index)
    if len(fdates) < 100:
        return {}

    weights = {}
    for rd in list(fdates[::rebalance_days]):
        f = factor.loc[rd].dropna()
        active = close.loc[rd].dropna().index
        f = f.reindex(active).dropna()
        if veto_factor is not None:
            selected = apply_veto_filter(f, veto_factor.loc[rd], top_n=top_n, veto_q=veto_q)
        else:
            selected = (
                pd.Series(1.0 / top_n, index=f.nlargest(top_n).index, dtype="float64")
                if len(f) >= top_n
                else pd.Series(dtype="float64")
            )
        if len(selected) == top_n:
            weights[rd] = selected
    return weights


# ---------------------------------------------------------------------------
# Strategy execution via unified engine
# ---------------------------------------------------------------------------

def run_small_cap_strategy(config: StrategyConfig = _DEFAULT_CONFIG) -> dict[str, Any]:
    """Run small-cap-size strategy via BacktestEngine."""
    close, volume, amount = load_price_panels(config.start)
    if config.exclude_star:
        close, volume, amount = _drop_star(close, volume, amount)
    prices = PricePanel(close=close, volume=volume, amount=amount)
    if not load_raw_close(start=config.start).empty:
        raw = load_raw_close(start=config.start).reindex(index=close.index, columns=close.columns)
        prices = PricePanel(close=close, volume=volume, amount=amount, raw_close=raw)

    factor = small_cap_factor(amount, config.size_window)
    timing, small_nav, timing_dist = small_cap_exposure_signal(close, amount, config.timing_ma)
    scheduled = build_rebalance_weights(factor, close, config.top_n, config.rebalance_days)

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


def latest_signal(config: StrategyConfig = _DEFAULT_CONFIG) -> dict[str, Any]:
    """Backward-compatible wrapper for :func:`latest_decision`."""
    return latest_decision(config)


def latest_decision(config: StrategyConfig = _DEFAULT_CONFIG) -> dict[str, Any]:
    """Latest signal for live trading."""
    result = run_small_cap_strategy(config)
    close = result["close"]
    factor = result["factor"]
    timing = result["timing"]
    dist = result["timing_dist"]
    last = close.index[-1]
    f = factor.loc[last].dropna()
    active = close.loc[last].dropna().index
    holdings = f.reindex(active).dropna().nlargest(config.top_n).index.tolist()
    return {
        "date": last,
        "in_market": bool(timing.loc[last]),
        "timing_dist": float(dist.loc[last]),
        "holdings": holdings,
        "result": result,
    }


# ---------------------------------------------------------------------------
# Legacy compat: delegate to engine
# ---------------------------------------------------------------------------

def backtest_weights(close: pd.DataFrame, scheduled_weights: dict[pd.Timestamp, pd.Series] | pd.DataFrame, timing_signal: pd.Series | None = None, config: StrategyConfig = _DEFAULT_CONFIG) -> tuple[pd.Series, pd.DataFrame]:
    """.. deprecated:: Use core.engine.BacktestEngine.run() instead.

    Kept as a thin compatibility wrapper for research scripts that have not
    yet migrated.  Internally delegates to BacktestEngine for identical
    numerical results.
    """
    import warnings
    warnings.warn(
        "backtest_weights is deprecated. Use core.engine.BacktestEngine.run() for new code.",
        DeprecationWarning,
        stacklevel=2,
    )
    # Build dummy volume/amount matching close shape (engine needs them as PricePanel
    # fields, but the portfolio backtest logic only uses close for returns)
    dummy = pd.DataFrame(1.0, index=close.index, columns=close.columns)
    prices = PricePanel(close=close, volume=dummy, amount=dummy)
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

    # Convert dict-of-Series weights to DataFrame if necessary
    if isinstance(scheduled_weights, dict):
        from core.engine import _dict_weights_to_df
        scheduled_weights = _dict_weights_to_df(scheduled_weights, close.index)

    signal = Signal(weights=scheduled_weights, timing=timing_signal)
    result = engine.run(signal)
    return result.returns, result.detail
