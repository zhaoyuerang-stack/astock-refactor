"""paper 多账户并行实测端点(T4,PLAN_paper_multiaccount_loop.md;只读)。

GET /paper-accounts   多账户并排展示(实测 NAV 曲线/回撤/回测偏差/状态),
                      顺序 = 后端产物顺序(R-PROD-001,前端不得重排名)。
"""
from __future__ import annotations

from fastapi import APIRouter

from contracts.views import PaperAccountsListView
from services.read.paper_accounts import list_paper_accounts

router = APIRouter(prefix="/paper-accounts", tags=["paper-accounts"])


@router.get("", response_model=PaperAccountsListView)
def get_paper_accounts() -> PaperAccountsListView:
    return list_paper_accounts()
