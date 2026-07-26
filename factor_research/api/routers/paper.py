"""模拟盘端点:今日操作卡 / 交易流水 / 净值曲线(全自动模拟真实盘,只读)。

GET  /paper/plan     操作卡(今日成交 + 明日计划 + 债券轮动指令 + 账户)
GET  /paper/trades   交易流水(倒序)
GET  /paper/nav      净值曲线
"""
from __future__ import annotations

from fastapi import APIRouter

from contracts.views import NavCurveView, PaperTradesView, TradePlanView
from services.read.paper import nav_curve, paper_trades, trade_plan

router = APIRouter(prefix="/paper", tags=["paper"])


@router.get("/plan", response_model=TradePlanView)
def get_plan() -> TradePlanView:
    return trade_plan()


@router.get("/trades", response_model=PaperTradesView)
def get_trades(limit: int = 200) -> PaperTradesView:
    return paper_trades(limit=limit)


@router.get("/nav", response_model=NavCurveView)
def get_nav() -> NavCurveView:
    return nav_curve()
