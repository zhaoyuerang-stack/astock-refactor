#!/usr/bin/env bash
# Verification tier for the public source-only repository.
# This proves code/architecture integrity; it does not claim production data readiness.
set -euo pipefail

cd "$(dirname "$0")/.."

python3 scripts/ci/check_layer_deps.py
python3 scripts/ci/check_test_discovery.py
python3 scripts/ci/check_control_exceptions.py
python3 scripts/ci/check_registry_evidence.py
python3 scripts/ci/check_holdout_compliance.py
python3 scripts/ci/check_cost_model_pin.py
python3 scripts/ci/check_no_force_promote.py
python3 scripts/ci/check_no_legacy_data.py
python3 scripts/ci/check_amount_units.py
python3 scripts/ci/check_print_budget.py
python3 scripts/ci/check_lake_writers.py
python3 scripts/ci/check_factor_registry.py

python3 -m ruff check --select F,B,I,UP .
if python3 -m mypy --version >/dev/null 2>&1; then
  mypy=(python3 -m mypy)
elif command -v uv >/dev/null 2>&1; then
  mypy=(uv tool run mypy==2.3.0)
else
  echo "mypy is required for source-only verification" >&2
  exit 1
fi
"${mypy[@]}" \
  --disallow-untyped-defs \
  --ignore-missing-imports \
  --follow-imports=skip \
  strategy_registry.py workflow/ strategies/
python3 -m pytest -q

echo "SOURCE-ONLY VERIFICATION PASSED"
echo "Production readiness remains unverified until the private data_lake is mounted."
