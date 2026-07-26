# MODULE_STATUS: factor_store
Status: ONLINE_SUPPORT
Role: Factor persistence + scoring layer (store, scoring, core_backfill).
Keep because: Serves factor caching/scoring for research and audit.

Boundary:
- Store/score only; validity judged by deterministic gates.
