"""run_backtest —— 包住当前生产回测路径。

铁律护栏(违反 = 结论作废):
- 回测**只**走 core.engine.BacktestEngine,不在此旁路重算因子/估值。
- 成本固化为 CostModel(买 0.225% / 卖 0.275% / 融资 6.5%),**不可调低**。
- 口径为 data_lake(load_price_panels),绝不用 data_full。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from contracts.views import BacktestResult

# 成本铁律(与 CLAUDE.md / strategy_lake.py 一致),固定不暴露给调用方调低
_BUY_COST = 0.00225
_SELL_COST = 0.00275
_FINANCING_RATE = 0.065
_BOND_CODE = "511010"
_VETO_Q = 0.30


def _band_exposure(timing_dist: pd.Series) -> pd.Series:
    """PureTrend MA16 Band timing exposure: dynamic 0~1.5x, lagged one day."""
    dist = timing_dist.shift(1).clip(lower=-0.5, upper=0.5)
    return ((1.0 + dist * 8.0).clip(lower=0.0, upper=1.5) * (dist > 0)).fillna(0.0)


def _load_bond_returns(code: str = _BOND_CODE) -> pd.Series:
    df = pd.read_parquet(f"data_lake/cross_asset/etf/{code}.parquet")
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")
    return df["close"].pct_change(fill_method=None).dropna()


def run_production_engine_backtest(
    *,
    start: str = "2018-01-01",
    top_n: int = 25,
    rebalance_days: int = 20,
    factor_window: int = 20,
    timing_ma: int = 16,
):
    """Run current LIVE production strategy: illiquidity v3.1.

    Stock leg:
    AmihudIlliq 20d + Salience Veto 30% + PureTrend MA16 Band exposure.
    Rotation leg:
    when lagged PureTrend dist is <= 0, returns are replaced by 511010.
    """
    from app_config.settings import get_settings
    from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
    from core.engine import BacktestResult as EngineBacktestResult
    from factors.alpha import transforms  # noqa: F401 - register zscore/mad_clip/shift
    from factors.alpha.base import FactorData
    from factors.alpha.builtins.illiq import AmihudIlliq
    from factors.small_cap import small_cap_timing
    from factors.tradable_mask import load_feature_tradable_context, price_panel_kwargs_from_context
    from factors.veto import salience_covariance_veto
    from strategies.small_cap import build_rebalance_weights

    warmup_start = get_settings().data.warmup_start
    data_start = str(min(pd.Timestamp(start), pd.Timestamp(warmup_start)).date())
    # ADR-040: production backtest loads limit panels + feature-tradable; engine
    # enforce_fill_tradable defaults True (blocks limit-up buys / limit-down sells).
    ctx = load_feature_tradable_context(data_start)
    prices = PricePanel(**price_panel_kwargs_from_context(ctx))
    close, volume, amount = prices.close, prices.volume, prices.amount

    data = FactorData(
        close=close,
        volume=volume,
        amount=amount,
        raw_close=ctx.raw_close,
        tradable=ctx.tradable,
    )
    factor = AmihudIlliq(window=factor_window).mad_clip(5).zscore().shift(1).compute(data)
    veto = salience_covariance_veto(close).shift(1)
    _, _, timing_dist = small_cap_timing(close, amount, ma_window=timing_ma)
    scheduled = build_rebalance_weights(
        factor,
        close,
        top_n=top_n,
        rebalance_days=rebalance_days,
        veto_factor=veto,
        veto_q=_VETO_Q,
    )

    config = BacktestConfig(
        start=start,
        cost=CostModel(
            buy_cost=_BUY_COST,
            sell_cost=_SELL_COST,
            financing_rate=_FINANCING_RATE,
        ),
        # v3.1 的杠杆由 Band exposure(0~1.5)表达,不再用固定 1.25x。
        leverage=1.0,
    )
    stock_result = BacktestEngine(prices=prices, config=config).run(
        Signal(
            weights=scheduled,
            timing=_band_exposure(timing_dist),
            exposure_cap=1.5,
            family="illiquidity",
            version="v3.1",
        )
    )

    bond_ret = _load_bond_returns(_BOND_CODE)
    dist_lagged = timing_dist.shift(1)
    common = stock_result.returns.index.intersection(bond_ret.index).intersection(dist_lagged.dropna().index)
    if len(common) == 0:
        return stock_result, close

    bull = dist_lagged.reindex(common) > 0
    stock_ret = stock_result.returns.reindex(common).fillna(0.0)
    bond_ret = bond_ret.reindex(common).fillna(0.0)
    rotated_returns = pd.Series(np.where(bull, stock_ret, bond_ret), index=common)
    rotated = EngineBacktestResult(
        returns=rotated_returns,
        turnover=stock_result.turnover.reindex(common).fillna(0.0),
        cost=stock_result.cost.reindex(common).fillna(0.0),
        family="illiquidity",
        version="v3.1",
        config=config,
    )
    return rotated, close


def run_backtest(
    start: str = "2018-01-01",
    top_n: int = 25,
    rebalance_days: int = 20,
    factor_window: int = 20,
    timing_ma: int = 16,
    family: str = "illiquidity",
    version: str = "v3.1",
) -> BacktestResult:
    if (family, version) != ("illiquidity", "v3.1"):
        raise ValueError("run_backtest only supports current production strategy: illiquidity/v3.1")

    result, close = run_production_engine_backtest(
        start=start,
        top_n=top_n,
        rebalance_days=rebalance_days,
        factor_window=factor_window,
        timing_ma=timing_ma,
    )

    m = result.metrics
    yearly = {int(y): float(r) for y, r in result.yearly_returns.items()}
    start_out = str(result.returns.index[0].date()) if len(result.returns) else str(close.index[0].date())
    end_out = str(result.returns.index[-1].date()) if len(result.returns) else str(close.index[-1].date())
    return BacktestResult(
        annual=m["annual"], vol=m["vol"], sharpe=m["sharpe"], maxdd=m["maxdd"],
        calmar=m["calmar"], hit=bool(m["hit"]), n=int(m["n"]),
        turnover_annual=float(result.detail["turnover"].mean() * 252),
        cost_annual=float(result.detail["cost"].mean() * 252),
        yearly_returns=yearly,
        n_stocks=int(close.shape[1]), n_days=int(result.n),
        start=start_out, end=end_out,
        family=result.family, version=result.version,
    )
