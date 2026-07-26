# Agent Control Plane Report

## What Changed

- Added structured contracts for module status, artifact policy, action policy, and agent decisions.
- Added `MODULE_STATUS.md` for all top-level modules (prerequisite the plan assumed but did not include; created as "Task 0"): 33 dirs present at start, plus the runtime-created artifact dirs `signals/`, `paper/`, `logs/` (force-added because they are gitignored) so the guard stays consistent once those dirs materialize — 36 in total. Classifications are inspection-based, not uniform: `core=ONLINE_CRITICAL` (BacktestEngine + CostModel authority), `execution=ARCHIVE_OR_REHOME` (only one test imports it, no live caller — verified by grep).
- Added module inventory reader from top-level `MODULE_STATUS.md`.
- Added artifact inventory boundaries for data lake, reports, signals, paper, scratch, results, and logs.
- Added action policy checks for registry writes, data-lake writes, promotion, formal evidence, deployment, and daily runs.
- Added strategy lifecycle read model over `strategy_versions.json`.
- Added agent operating model and skill playbooks.
- Added safe task wrappers and read-only API views (`/agent-control/*`).
- Added CI guard for module status metadata, wired into `scripts/test_all.sh`.

## Safety Properties

**Scope: this is an advisory guidance layer, not an enforcement layer.**
`can_agent_do(...)` returns a structured decision; it does not intercept file
writes. An agent that ignores the policy and calls `open('strategy_versions.json',
'w')` directly is *not* stopped by this code. Actual enforcement of the
non-negotiable rules stays with the existing deterministic CI guards
(`check_registry_evidence`, `check_no_force_promote`, `check_holdout_compliance`,
`check_lake_writers`, `check_layer_deps`, `check_control_exceptions`) and the
`strategy_registry.register` single write entrypoint. This layer's job is to give
a compliant agent the correct answer *before* it acts, and to make the boundaries
machine-readable — not to be the last line of defense.

What this layer guarantees when consulted:

- A compliant agent asking `USE_FORMAL_EVIDENCE` is told **no** for scratch/results/logs
  (case-insensitive, any path segment) and for any path outside a known evidence area
  (positive whitelist / fail-closed).
- `WRITE_REGISTRY` / `WRITE_DATA_LAKE` return `allowed=False` and point to the
  canonical entrypoint (`strategy_registry.register`, controlled lake writers).
- `PROMOTE_CANDIDATE` points only to `workflow.promote`.
- `UPDATE_DEPLOYMENT` and `ARCHIVE_MODULE` return `allowed=False`, requiring human approval.
- An unknown action fails closed (raises `ValueError`) rather than silently allowing.

What this layer itself does not do:

- It does not block writes at the filesystem/registry level (that is the CI guards' job).
- All new services are read-only or fact-assembling; none mutate registry, data lake,
  signals, paper, or deployment manifests. Layer-dependency guard passes (no forbidden
  edges introduced), so the guidance layer cannot become a write path.

## Verification

Targeted tests (all PASS):

- `python3 tests/test_agent_control_contracts.py`
- `python3 tests/test_module_inventory.py`
- `python3 tests/test_artifact_inventory.py`
- `python3 tests/test_agent_action_policy.py`
- `python3 tests/test_strategy_lifecycle_view.py`
- `python3 tests/test_agent_skill_docs.py`
- `python3 tests/test_agent_tasks.py`
- `python3 tests/test_agent_control_api_contract.py`
- `python3 tests/test_module_status_guard.py`

Architecture guards (all PASS):

- `python3 scripts/ci/check_module_status.py`
- `python3 scripts/ci/check_layer_deps.py`

## Full-Suite Status (`bash scripts/test_all.sh`)

- All CI guards pass, including the new `check_module_status.py`.
- The run stops at `test_engine.py` with `ValueError: No objects to concatenate` from
  `lake.load_lake.load_prices` — the `data_lake/` in this worktree holds **no parquet
  files**, so the price panels cannot load. This is a **pre-existing data/environment
  gap, not a regression from this change**: the control-plane code is additive and touches
  none of `engine/`, `strategies/`, or `lake/` load paths. Reproduced independently:
  `load_prices(...)` raises the same error with no control-plane code involved.
- Once the data lake is populated, the downstream engine/strategy tests are expected to
  run as before; this change does not affect them.

## Deferred Work

- Build a Web panel for module inventory and allowed agent actions.
- Add richer strategy state-machine transitions.
- Add policy coverage for cost-model changes and holdout boundary changes.
- Add per-skill structured execution logs.
