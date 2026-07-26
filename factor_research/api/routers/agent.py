"""POST /agent/ask —— 研究副驾驶(规则式 planner;LLM 可插拔)。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from contracts.views import (
    AgentAskRequest,
    AgentAskResponse,
    AgentKnowledgeSourceView,
    AgentSessionAskRequest,
    AgentSessionAskResponse,
    AgentSessionCreateRequest,
    AgentSessionView,
)
from services.agent.knowledge import PROJECT_ROOT, list_knowledge_sources
from services.agent.planner import ask
from services.agent.sessions import (
    append_message,
    create_session,
    get_session,
    history_for_llm,
    list_sessions,
)

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/ask", response_model=AgentAskResponse)
def agent_ask(body: AgentAskRequest) -> AgentAskResponse:
    context = dict(body.context)
    context["messages"] = body.messages[-8:]
    return AgentAskResponse(**ask(body.request, context))


@router.post("/sessions", response_model=AgentSessionView)
def agent_create_session(body: AgentSessionCreateRequest) -> AgentSessionView:
    return AgentSessionView(**create_session(
        page_context=body.page_context,
        title=body.title,
        user_id=body.user_id,
    ))


@router.get("/sessions", response_model=list[AgentSessionView])
def agent_list_sessions(user_id: str = "local", limit: int = 20) -> list[AgentSessionView]:
    return [AgentSessionView(**s) for s in list_sessions(user_id=user_id, limit=limit)]


@router.get("/sessions/{session_id}", response_model=AgentSessionView)
def agent_get_session(session_id: str) -> AgentSessionView:
    try:
        return AgentSessionView(**get_session(session_id))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="session not found") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/sessions/{session_id}/ask", response_model=AgentSessionAskResponse)
def agent_session_ask(session_id: str, body: AgentSessionAskRequest) -> AgentSessionAskResponse:
    try:
        session = get_session(session_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="session not found") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    context = dict(body.context)
    context["messages"] = history_for_llm(session, limit=8)
    result = ask(body.request, context)
    append_message(session_id, "user", body.request, metadata={"context": body.context})
    session = append_message(
        session_id,
        "assistant",
        result["output"].get("summary", ""),
        metadata={
            "task_id": result.get("task_id"),
            "tool": result.get("tool"),
            "risk": result.get("risk"),
            "citations": result["output"].get("citations", []),
        },
    )
    return AgentSessionAskResponse(**result, session=AgentSessionView(**session))


@router.get("/sources", response_model=list[AgentKnowledgeSourceView])
def agent_sources() -> list[AgentKnowledgeSourceView]:
    out: list[AgentKnowledgeSourceView] = []
    for src in list_knowledge_sources():
        out.append(
            AgentKnowledgeSourceView(
                source_id=src.source_id,
                source_type=src.source_type,
                title=src.title,
                source_path=src.path.relative_to(PROJECT_ROOT).as_posix(),
            )
        )
    # Add virtual runtime status knowledge source
    out.append(
        AgentKnowledgeSourceView(
            source_id="runtime_status",
            source_type="runtime",
            title="系统实时状态与运行数据",
            source_path="runtime_status",
        )
    )
    return out

