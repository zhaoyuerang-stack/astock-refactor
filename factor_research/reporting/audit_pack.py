"""Reconciliation Audit Pack.

Packages performance reports, drawdowns, exposures, and compliance checks
together with a deterministic verification hash.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def generate_audit_pack(
    strategy_id: str,
    performance_metrics: dict[str, Any],
    drawdown_report: dict[str, Any],
    exposure_report: dict[str, Any],
    compliance_checks: list[dict[str, Any]]
) -> dict[str, Any]:
    """Compile a signed and versioned audit pack for external governance reviews."""
    pack = {
        "strategy_id": strategy_id,
        "performance": performance_metrics,
        "drawdown": drawdown_report,
        "exposure": exposure_report,
        "compliance": compliance_checks,
        "version": "1.0.0"
    }

    # Generate deterministic signature hash
    serialized = json.dumps(pack, sort_keys=True, default=str)
    signature = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    pack["verification_signature"] = signature
    return pack
