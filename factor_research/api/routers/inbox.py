"""GET /inbox —— 决策收件箱与今日简报(产品主界面数据面)。

只读聚合层:两个端点都不做任何新判定、零写动作(权威与护栏见各 read service docstring)。
"""
from __future__ import annotations

from fastapi import APIRouter

from contracts.views import DailyBriefView, DecisionInboxView
from services.read.daily_brief import get_daily_brief
from services.read.decision_inbox import get_decision_inbox

router = APIRouter(prefix="/inbox", tags=["inbox"])


@router.get("", response_model=DecisionInboxView)
def decision_inbox() -> DecisionInboxView:
    """决策收件箱:今天需要人裁决的 0-N 件事(证据已装配,动作指向 canonical 入口)。

    空箱三态:有待裁决 / 全源可读无待裁决(健康) / 有源不可读(不完整,禁称无事)。"""
    return get_decision_inbox()


@router.get("/brief", response_model=DailyBriefView)
def daily_brief() -> DailyBriefView:
    """今日简报首屏:信任裁决(透传 trust-calibration)+ 待裁决数 + 系统活动 + 世界变化。"""
    return get_daily_brief()
