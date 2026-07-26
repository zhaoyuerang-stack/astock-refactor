"""状态层端点:GET /state/health(策略健康)、GET /state/market(持仓状态)。"""
from __future__ import annotations

from fastapi import APIRouter

from contracts.views import FactorHealthView, MarketStateView
from services.read.state import market_state, strategy_health

router = APIRouter(prefix="/state", tags=["state"])


@router.get("/health", response_model=list[FactorHealthView])
def health() -> list[FactorHealthView]:
    return strategy_health()


@router.get("/market", response_model=MarketStateView)
def market() -> MarketStateView:
    return market_state()
