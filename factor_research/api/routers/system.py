"""GET /system/truth —— 系统真相层。

declared(清单声称在跑什么) / verified(fail-closed 校验后真正可激活) /
production_allowed(今日是否允许生产) 三态 + 逐腿证据链。供前端/运维/agent 从单一入口
判断「当前能不能交易、为什么」,杜绝把 manifest 里的 status:active 误读成 live。
"""
from __future__ import annotations

from fastapi import APIRouter

from contracts.views import SystemTruthView
from services.read.system_truth import get_system_truth

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/truth", response_model=SystemTruthView)
def system_truth() -> SystemTruthView:
    return get_system_truth()
