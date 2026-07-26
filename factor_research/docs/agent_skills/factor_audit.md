# factor-audit

## Inputs

- Factor name, formula, panel path, or candidate id.

## Allowed Tools

- `factory.lines.line2_validation`
- `factor_store.scoring`
- `core.analysis`
- `services.actions.run_backtest`

## Forbidden

- Do not declare alpha valid from language alone.
- Do not use scratch output as final evidence.

## Success Criteria

- Produce IC, ICIR, monotonicity, decay, cost sensitivity, and next recommended gate.
