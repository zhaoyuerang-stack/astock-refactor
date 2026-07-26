# MODULE_STATUS: signals
Status: ONLINE_ARTIFACTS
Role: Production daily signal outputs written by run_daily.py.
Keep because: Live signal artifacts consumed by production/paper; runtime-created dir.

Boundary:
- Written by run_daily.py only; agents read but do not write signals.
