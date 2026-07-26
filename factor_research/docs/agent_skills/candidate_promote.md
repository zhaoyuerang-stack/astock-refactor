# candidate-promote

## Inputs

- Hypothesis id, AutoResearch fingerprint, or approved candidate id.

## Allowed Tools

- `services.read.action_policy`
- `workflow.research_stages`
- `workflow.promote`
- `workflow.nine_gate_runner`

## Forbidden

- Do not write registry files directly.
- Do not skip phase1 synthetic audit.
- Do not bypass human review when required.

## Success Criteria

- Candidate is promoted, rejected, blocked, or left in review with exact mechanical evidence.
