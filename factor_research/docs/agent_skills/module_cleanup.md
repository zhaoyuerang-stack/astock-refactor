# module-cleanup

## Inputs

- Module name or status class.

## Allowed Tools

- `services.read.module_inventory`
- `services.read.action_policy`
- `scripts/ci/check_layer_deps.py`

## Forbidden

- Do not delete or move modules without explicit human approval.
- Do not archive modules with active production, workflow, or service callers.

## Success Criteria

- Classify as keep, rehome, archive, or investigate, with caller evidence and required tests.
