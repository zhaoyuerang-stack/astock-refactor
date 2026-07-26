# Agent Operating Model

This system is designed so agents act as constrained research operators.

## Layers

- Agent: understands the user request, selects a skill, calls tools, summarizes evidence.
- Skill: maps a task type to required steps, tools, checks, and forbidden actions.
- Tool: deterministic service/action/API entrypoint.
- Data: canonical facts and artifacts with explicit read/write boundaries.
- Strategy: lifecycle state in factory, workflow, registry, portfolio, and production.
- Governance: holdout, trial ledger, 9-Gate, registry evidence, module status, and action policy.

## Principle

Agents may orchestrate and explain. Deterministic code decides validity.

## Default Read Entrypoints

- `services.read.module_inventory`
- `services.read.artifact_inventory`
- `services.read.action_policy`
- `services.read.strategy_lifecycle`
- existing `services.read.*` views

## Default Action Entrypoints

- `workflow.promote`
- `workflow.nine_gate_runner`
- `services.actions.*`
- `scripts/data/*`
- `scripts/ops/*`
- `run_daily.py`

## Forbidden Shortcuts

- direct registry writes
- direct data-lake writes
- promotion without workflow
- formal evidence from scratch/results/logs
- deployment changes without human approval

## HITL Gate Scope (audit #6, 2026-07-18)

`ASTOCK_MID_CONFIRM_TOKEN`(agent_cli mid 门)与 `BULK_AUTO_APPROVE=1` 这类
env-var 人工确认门,**仅在桌面进程内成立**——桌面端由 capabilityService 在人点击
确认后生成 token 注入子进程,才构成真 HITL。裸 CLI/shell 语境下任何 agent 可自设
env 同时扮演"人",此门为零。这与本文层级定位一致:操作层是 advisory 纪律,
真正强制 = `scripts/ci/` 守卫 + canonical 唯一写入口(CLAUDE.md §2/ADR-030)。
勿在安全论证中把 env-var 门当作强制边界引用。
