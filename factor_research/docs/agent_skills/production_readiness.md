# production-readiness

## Inputs

- Optional deployment id or date.

## Allowed Tools

- `runtime.production_readiness`
- `runtime.deployment`
- `services.read.trade_readiness`
- `run_daily.py --no-update`

## Forbidden

- Do not activate deployment.
- Do not edit `deployments/production.json` without human approval.

## Success Criteria

- Report allowed/blocked status, blockers, latest signal path or draft path, and required human action.
