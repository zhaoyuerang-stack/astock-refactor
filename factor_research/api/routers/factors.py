"""GET /factors —— alpha 家族(因子)只读列表。"""
from __future__ import annotations

from fastapi import APIRouter

from contracts.views import FactorView
from services.read.factors import list_factors

router = APIRouter(prefix="/factors", tags=["factors"])


@router.get("", response_model=list[FactorView])
def factors() -> list[FactorView]:
    return list_factors()
