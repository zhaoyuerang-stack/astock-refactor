"""GET /risk —— 声明式 risk_policy 评估 + 超限控制动作(待人工确认)。"""
from __future__ import annotations

from fastapi import APIRouter

from contracts.views import RiskReport
from services.read.risk import risk_report

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("", response_model=RiskReport)
def risk() -> RiskReport:
    return risk_report()
