"""GET /strategies —— 台账 family/version 只读列表。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from contracts.views import StrategyDetailView, StrategyView
from services.read.registry import get_strategy_detail, list_strategies

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("", response_model=list[StrategyView])
def strategies() -> list[StrategyView]:
    return list_strategies()


@router.get("/{family}/{version}", response_model=StrategyDetailView)
def strategy_detail(family: str, version: str) -> StrategyDetailView:
    try:
        return get_strategy_detail(family, version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"strategy not found: {family}/{version}") from exc
