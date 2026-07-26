"""GET /portfolio —— 当前组合(纸面)+ 目标组合(选股层 top-N)。"""
from __future__ import annotations

from fastapi import APIRouter

from contracts.views import PortfolioView
from services.read.portfolio import current_portfolio

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("", response_model=PortfolioView)
def portfolio(target: bool = True) -> PortfolioView:
    return current_portfolio(with_target=target)
