"""Production strategy and backtest core.

New canonical entry-point::

    from core.engine import BacktestEngine, Signal, BacktestResult
"""
from core.engine import (  # noqa: F401
    BacktestConfig,
    BacktestEngine,
    BacktestResult,
    CostModel,
    PricePanel,
    Signal,
)
