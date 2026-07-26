# data-health

## Inputs

- Optional date or expected freshness window.

## Allowed Tools

- `services.read.state`
- `services.read.artifact_inventory`
- `lake.validator`
- `validate_final.py`

## Forbidden

- Do not silently repair data.
- Do not write `data_lake/` except through `scripts/data`.

## Success Criteria

- Report latest data date, quality status, stale status, and blocking issues.
