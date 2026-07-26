"""GET /backtest/run —— 跑生产口径回测,返回 BacktestResult。"""
from __future__ import annotations

from fastapi import APIRouter

from contracts.views import BacktestResult
from services.actions.run_backtest import run_backtest

router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.get("/run", response_model=BacktestResult)
def run(
    start: str = "2018-01-01",
    top_n: int = 25,
    rebalance_days: int = 20,
    factor_window: int = 20,
    timing_ma: int = 16,
) -> BacktestResult:
    return run_backtest(
        start=start, top_n=top_n, rebalance_days=rebalance_days,
        factor_window=factor_window, timing_ma=timing_ma,
    )
