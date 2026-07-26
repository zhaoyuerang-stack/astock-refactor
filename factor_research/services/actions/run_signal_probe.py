"""Controlled L0 signal probe wrapper (ADR-037).

Writes only under reports/research/. Never writes registry.
full=True runs scripts/research/signal_source_probe.probe for registered factors.
Not alpha admission (R-LLM-001 / R-WF-001).
"""
from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app_config.settings import get_settings

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "reports" / "research"
_PROBE_SCRIPT = ROOT / "scripts" / "research" / "signal_source_probe.py"


def _holdout_boundary() -> str:
    try:
        s = get_settings()
        hb = getattr(getattr(s, "research", None), "holdout_boundary", None) or getattr(
            getattr(s, "holdout", None), "boundary", None
        )
        if hb:
            return str(hb)[:10]
    except Exception:
        pass
    return "2025-01-01"


def _load_probe_fn() -> Callable:
    if not _PROBE_SCRIPT.exists():
        raise FileNotFoundError(f"probe script missing: {_PROBE_SCRIPT}")
    spec = importlib.util.spec_from_file_location("signal_source_probe_mod", _PROBE_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load signal_source_probe")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.probe


def _default_params(record) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key, val in (record.params or {}).items():
        if isinstance(val, tuple) and len(val) == 2:
            lo, hi = val
            if isinstance(lo, int) and isinstance(hi, int):
                params[key] = int((lo + hi) // 2)
            else:
                params[key] = lo
        else:
            params[key] = val
    return params


def run_signal_probe(
    *,
    factor_name: str,
    idea: str = "",
    start: str = "2018-01-01",
    cutoff: str = "2022-12-31",
    end: str | None = None,
    universe: str = "all",
    full: bool | str = True,
    probe_fn: Callable | None = None,
) -> dict[str, Any]:
    """Run L0 probe for a registered factor, or block unregistered names.

    ``full`` accepts bool or strings \"true\"/\"false\" (CLI JSON).
    """
    if isinstance(full, str):
        full = full.strip().lower() in {"1", "true", "yes", "y"}

    boundary = _holdout_boundary()
    end = end or "2024-12-31"
    if str(end) >= boundary:
        raise ValueError(
            f"probe end={end} must be < holdout boundary={boundary} (ADR-021)"
        )
    if str(cutoff) >= boundary:
        raise ValueError(f"probe cutoff={cutoff} must be < holdout boundary={boundary}")

    from factors.registry import FACTOR_REGISTRY, discover

    discover()
    registered = factor_name in FACTOR_REGISTRY
    created = datetime.now(UTC).isoformat()

    base: dict[str, Any] = {
        "factor_name": factor_name,
        "idea": idea,
        "start": start,
        "cutoff": cutoff,
        "end": end,
        "universe": universe,
        "holdout_boundary": boundary,
        "registered": registered,
        "can_claim_valid": False,
        "formal_path_allowed": "reports/research/",
        "created_at": created,
    }

    if not registered:
        receipt = {
            **base,
            "status": "blocked_unregistered",
            "evidence_tier": "precheck",
            "full": False,
            "note": "Unregistered factor: run data_gap_audit / onboarding before probe.",
        }
        return _write_report(factor_name, receipt)

    record = FACTOR_REGISTRY[factor_name]
    factor_ref = f"{record.fn.__module__}:{record.fn.__name__}"
    params = _default_params(record)
    base.update({
        "factor_ref": factor_ref,
        "params": params,
        "definition": record.definition,
        "evidence_pointer": record.evidence,
    })

    if not full:
        receipt = {
            **base,
            "status": "queued_l0_receipt",
            "evidence_tier": "l0_probe",
            "full": False,
            "note": (
                "L0 receipt only — pass full=true to run signal_source_probe; "
                "not alpha admission."
            ),
        }
        return _write_report(factor_name, receipt)

    try:
        fn = probe_fn or _load_probe_fn()
        probe_result = fn(factor_ref, params, universe, start, cutoff, end)
    except Exception as exc:
        failed = {
            **base,
            "status": "probe_failed",
            "evidence_tier": "l0_probe",
            "full": True,
            "error": str(exc),
            "note": "Full L0 probe failed; not alpha. Check data_lake and factor implementation.",
        }
        return _write_report(factor_name, failed)

    # Strip nothing essential; mark honesty fields
    out = {
        **base,
        "status": "l0_probe_complete",
        "evidence_tier": "l0_probe",
        "full": True,
        "probe": probe_result,
        "note": (
            "L0 evidence only (IC/orthogonality/IS-OOS). "
            "No costs/DSR/9-Gate. Not alpha admission (R-LLM-001/R-WF-001)."
        ),
    }
    return _write_report(factor_name, out)


def _write_report(factor_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in factor_name)[:60]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = REPORTS / f"agent_l0_probe_{safe}_{stamp}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    payload["report_path"] = str(out.relative_to(ROOT))
    return payload
