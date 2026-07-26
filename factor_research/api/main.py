"""FastAPI app —— Phase 0 装配。

运行(cwd 必须是 factor_research/,以便 import services/contracts/引擎):
    cd factor_research && uvicorn api.main:app --reload
或直接:
    cd factor_research && python3 -m api.main
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import (
    agent,
    agent_control,
    backtest,
    data,
    experiments,
    factors,
    governance,
    inbox,
    paper,
    paper_accounts,
    portfolio,
    risk,
    settings,
    state,
    strategies,
    system,
    trade_readiness,
)

app = FastAPI(title="Quant Research Platform API", version="0.0-phase0")

# Phase 1:允许本地 Next.js 开发服务器跨域调用。端口不固定(3000 被占时 dev server
# 会 fallback 到任意空闲端口),故放开全部本地回环端口;仅限 localhost,不涉及外网暴露。
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(strategies.router)
app.include_router(factors.router)
app.include_router(backtest.router)
app.include_router(data.router)
app.include_router(state.router)
app.include_router(portfolio.router)
app.include_router(paper.router)
app.include_router(paper_accounts.router)
app.include_router(risk.router)
app.include_router(experiments.router)
app.include_router(agent.router)
app.include_router(agent_control.router)
app.include_router(settings.router)
app.include_router(trade_readiness.router)
app.include_router(governance.router)
app.include_router(system.router)
app.include_router(inbox.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "phase": 0}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8011)
