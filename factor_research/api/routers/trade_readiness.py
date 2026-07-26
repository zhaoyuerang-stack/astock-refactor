"""GET /trade-readiness —— 每日交易准备度与系统评估。"""
from __future__ import annotations

from fastapi import APIRouter

from contracts.views import TradeReadinessView
from services.read.trade_readiness import get_trade_readiness

router = APIRouter(prefix="/trade-readiness", tags=["governance"])

@router.get("", response_model=TradeReadinessView)
def trade_readiness() -> TradeReadinessView:
    return get_trade_readiness()
