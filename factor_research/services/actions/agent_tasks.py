"""Safe agent task wrappers.

These functions do not execute risky mutations. They assemble the facts and
policy decisions an agent needs before selecting a deterministic workflow.
"""
from __future__ import annotations

from contracts.agent_control import AgentAction
from services.read.action_policy import can_agent_do
from services.read.module_inventory import get_module_status


def guard_agent_action(action: str, target: str, context: dict | None = None) -> dict:
    return can_agent_do(AgentAction(action), target, context).to_dict()


def describe_agent_task(task: str, *, target: str) -> dict:
    if task == "module_cleanup":
        module = get_module_status(target).to_dict()
        archive_policy = can_agent_do(AgentAction.ARCHIVE_MODULE, target).to_dict()
        return {
            "task": task,
            "target": target,
            "module": module,
            "archive_policy": archive_policy,
            "next_step": "collect callers and request human approval before moving or deleting files",
        }

    return {
        "task": task,
        "target": target,
        "policy": can_agent_do(AgentAction.READ, target).to_dict(),
        "next_step": "select a specific skill playbook",
    }
