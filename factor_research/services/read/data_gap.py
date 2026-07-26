"""data_gap_audit — lake / registry reachability for an idea (ADR-037).

Readonly. Does not invent data or declare alpha.
"""
from __future__ import annotations

import re
from typing import Any

from lake.schema import TUSHARE_DATASETS
from services.read.strategy_idea import check_strategy_idea

# Tokens that typically need external inputs beyond current lake defaults.
_SPECIAL_NEEDS: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"WACC|wacc|资本成本|股权成本|债务成本", re.I), [
        "equity_cost_inputs: beta / risk_free / ERP (not in lake)",
        "debt_cost_inputs: interest_expense / credit spread (income has no interest field)",
        "tax_rate for after-tax WACC",
    ]),
    (re.compile(r"一致预期|分析师|目标价", re.I), [
        "analyst consensus PIT series (not fully canonical)",
    ]),
    (re.compile(r"期权|隐波|IV", re.I), [
        "options IV surface (not in TUSHARE_DATASETS core)",
    ]),
]


def _registered_factor_names() -> set[str]:
    try:
        from factors.registry import FACTOR_REGISTRY, discover

        discover()
        return {str(n) for n in FACTOR_REGISTRY.keys()}
    except Exception:
        return set()


def data_gap_audit(idea: str) -> dict[str, Any]:
    text = (idea or "").strip()
    pre = check_strategy_idea(text)
    terms = list(pre.get("parsed_hints", {}).get("candidate_terms") or [])
    registry_hits = list(pre.get("parsed_hints", {}).get("registry_factor_hits") or [])
    registry_names = _registered_factor_names()

    datasets = {
        name: {"store": store, "mode": mode, "default_fields": list(fields)}
        for name, (store, mode, fields) in TUSHARE_DATASETS.items()
    }

    missing: list[str] = []
    for term in terms:
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,40}", term):
            if term.lower() not in {n.lower() for n in registry_hits}:
                if term.lower() not in {n.lower() for n in registry_names}:
                    missing.append(f"factor_not_registered:{term}")

    special: list[str] = []
    for pattern, needs in _SPECIAL_NEEDS:
        if pattern.search(text):
            special.extend(needs)
    missing.extend(special)

    playbook = "factor_research/docs/agent_skills/data_source_onboarding.md"

    return {
        "idea_text": text,
        "candidate_terms": terms,
        "registry_factor_hits": registry_hits,
        "registry_size": len(registry_names),
        "available_datasets": sorted(datasets.keys()),
        "dataset_catalog_size": len(datasets),
        "missing": missing,
        "factor_ready": bool(registry_hits) and not special,
        "onboarding_playbook": playbook,
        "related_precheck": {
            "validation_status": pre.get("validation_status"),
            "can_claim_valid": False,
            "trust": pre.get("trust"),
        },
        "next_protocol": "data_source_onboarding" if missing else "proxy_or_signal_probe",
        "limits": [
            "data_gap_audit is reachability only; not alpha",
            "can_claim_valid=false",
            "do not invent fields that are missing",
        ],
    }
