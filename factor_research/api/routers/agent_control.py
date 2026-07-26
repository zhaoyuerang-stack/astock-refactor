"""Read-only agent control-plane endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.read.action_policy import can_agent_do
from services.read.artifact_inventory import get_artifact_inventory
from services.read.module_inventory import get_module_inventory
from services.read.strategy_lifecycle import get_strategy_lifecycle, list_strategy_lifecycles

router = APIRouter(prefix="/agent-control", tags=["agent-control"])


@router.get("/modules")
def module_inventory():
    return [item.to_dict() for item in get_module_inventory()]


@router.get("/artifacts")
def artifact_inventory():
    return [item.to_dict() for item in get_artifact_inventory()]


@router.get("/policy")
def action_policy(action: str, target: str):
    try:
        return can_agent_do(action, target).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/strategies")
def strategy_lifecycles():
    return list_strategy_lifecycles()


@router.get("/strategies/{family}/{version}")
def strategy_lifecycle(family: str, version: str):
    return get_strategy_lifecycle(family, version)
