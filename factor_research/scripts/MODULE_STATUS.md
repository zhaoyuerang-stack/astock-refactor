# MODULE_STATUS: scripts
Status: MIXED_ENTRYPOINTS
Role: CI guards, data pipelines, ops, repair, research scripts (ci/, data/, ops/, research/).
Keep because: Guard + operational entrypoints; test_all.sh orchestrates guards.

Boundary:
- Guards are the single truth for their rules (CLAUDE.md §16).
