# MODULE_STATUS: app_config
Status: ONLINE_CONFIG
Role: Canonical settings entrypoint (settings.yaml + get_settings) and holdout boundary history.
Keep because: R-ARCH-003 single config entrypoint; holdout ledger lives here.

Boundary:
- No hardcoded model/path/fee/proxy in business logic; changes are auditable.
