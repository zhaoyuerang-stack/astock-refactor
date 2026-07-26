# MODULE_STATUS: apps
Status: CLI_ENTRYPOINTS
Role: Operator CLIs: factory_cli, portfolio_cli, stock_cli, contribution_dashboard.
Keep because: Human/agent entrypoints into canonical workflows.

Boundary:
- CLIs orchestrate only; deterministic code decides validity.
