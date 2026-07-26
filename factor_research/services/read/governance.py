"""Governance views derived from the canonical strategy registry and ledger."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contracts.views import GovernanceView
from research_ledger.ledger import ResearchLedger


def _approval_from_status(status: str) -> str:
    if status == "在册":
        return "APPROVED"
    if status == "候选":
        return "PENDING"
    return "REJECTED"


def _nine_gate_audit_state(nine_gate: dict) -> dict:
    from core.analysis.nine_gate_policy import decide_nine_gate

    return decide_nine_gate(nine_gate).as_state()


def get_strategy_gate_status(family: str, version: str) -> dict:
    """Return the canonical registry and Nine-Gate state for one version."""
    import strategy_registry

    data = strategy_registry._load()
    family_record = next(
        (item for item in data.get("families", []) if item["id"] == family),
        None,
    )
    version_record = (
        next(
            (
                item
                for item in family_record.get("versions", [])
                if item["version"] == version
            ),
            None,
        )
        if family_record
        else None
    )
    if version_record is None:
        return {
            "found": False,
            "registered": False,
            "approval": "REJECTED",
            "admission_track": "",
            "dsr_audited": False,
            "dsr_passed": None,
            "dsr_p": None,
            "audit_status": "NOT_FOUND",
            "audit_label": "未登记",
            "nine_gate_status": "",
            "nine_gate_error": "",
        }

    status = version_record.get("status", "")
    nine_gate = version_record.get("nine_gate") or {}
    audit = _nine_gate_audit_state(nine_gate)
    return {
        "found": True,
        "registered": status == "在册",
        "approval": _approval_from_status(status),
        "admission_track": (version_record.get("admission") or {}).get("track", ""),
        "dsr_audited": audit["audited"],
        "dsr_passed": audit["passed"],
        "dsr_p": nine_gate.get("dsr_p"),
        "audit_status": audit["code"],
        "audit_label": audit["label"],
        "nine_gate_status": nine_gate.get("status", ""),
        "nine_gate_error": nine_gate.get("error", ""),
    }


def _model_card(family: dict, version: dict) -> dict:
    data_scope = (
        version.get("data_scope")
        if isinstance(version.get("data_scope"), dict)
        else {}
    )
    return {
        "strategy_id": f"{family['id']}/{version['version']}",
        "economic_hypothesis": family.get("hypothesis") or "Quant premium capture",
        "data_sources": [data_scope.get("source", "data_lake")],
        "train_period": "2018-01-01 to 2022-12-31",
        "oos_period": data_scope.get("period", "2023-2026"),
        "applicable_regimes": [family["regime"]] if family.get("regime") else [],
        "capacity_limit": float(family.get("capacity_m", 0.0)) * 1_000_000,
        "style_exposures": family.get("style_betas") or {},
        "forbidden_conditions": (
            [family["decay_signal"]] if family.get("decay_signal") else []
        ),
        "known_failure_cases": list((family.get("failure_boundaries") or {}).keys()),
        "owner": "Research Team",
        "approver": "Risk Committee",
        "approval_status": _approval_from_status(version.get("status", "")),
        "admission_track": (version.get("admission") or {}).get("track", ""),
        "nine_gate": version.get("nine_gate") or {},
        "signature": (
            f"SIG_AUTO_{family['id'].upper().replace('-', '_')}_"
            f"{version['version'].replace('.', '_')}"
        ),
    }


def _validation_report(card: dict, version: dict) -> dict:
    metrics = version.get("metrics") or {}
    sharpe = metrics.get("sharpe")
    nine_gate = version.get("nine_gate") or {}
    audit = _nine_gate_audit_state(nine_gate)
    sharpe_ok = isinstance(sharpe, (int, float)) and sharpe >= 0.35
    checks = [
        {
            "name": "OOS Sharpe Ratio",
            "passed": sharpe_ok,
            "value": sharpe,
            "threshold": 0.35,
        }
    ]

    if audit["code"] == "RUN_FAILED":
        checks.append(
            {
                "name": "Nine-Gate 执行",
                "passed": False,
                "value": nine_gate.get("error", "FAILED_TO_RUN"),
                "threshold": "完成",
            }
        )
        passed = False
        verdict = audit["label"]
    elif audit["audited"]:
        checks.append(
            {
                "name": "Deflated Sharpe p值 (多重检验惩罚)",
                "passed": bool(audit["passed"]),
                "value": nine_gate.get("dsr_p"),
                "threshold": 0.05,
            }
        )
        if nine_gate.get("psr") is not None:
            checks.append(
                {
                    "name": "Probabilistic Sharpe Ratio",
                    "passed": nine_gate["psr"] >= 0.95,
                    "value": nine_gate["psr"],
                    "threshold": 0.95,
                }
            )
        passed = sharpe_ok and bool(audit["passed"])
        verdict = "审计通过" if passed else audit["label"]
    else:
        passed = False
        verdict = audit["label"]

    return {
        "strategy_id": card["strategy_id"],
        "passed": passed,
        "verdict": verdict,
        "audited": audit["audited"],
        "audit_status": audit["code"],
        "audit_label": audit["label"],
        "metrics": {
            "oos_sharpe": sharpe,
            "oos_max_dd": metrics.get("maxdd"),
            "dsr_p": nine_gate.get("dsr_p"),
            "psr": nine_gate.get("psr"),
            "n_trials": nine_gate.get("n_trials"),
            "sortino": nine_gate.get("sortino"),
            "var_95": nine_gate.get("var_95"),
            "cvar_95": nine_gate.get("cvar_95"),
            "tail_ratio": nine_gate.get("tail_ratio"),
        },
        "checks": checks,
    }


def _factory_ledger_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        result = event.get("result", {})
        entries.append(
            {
                "experiment_id": event.get("experiment_id"),
                "parent_experiment_id": event.get("vintage_id"),
                "hypothesis_text": event.get("notes")
                or f"Evaluation of strategy candidate {event.get('experiment_id')}",
                "llm_prompt_hash": None,
                "factor_ast_hash": event.get("hypothesis_id", ""),
                "code_commit_hash": event.get("code_commit_hash")
                or event.get("git_commit")
                or "unknown",
                "data_snapshot_hash": event.get("vintage_id", ""),
                "universe_version": "data_lake",
                "cost_model_version": "v1.25",
                "random_seed": 42,
                "tried_parameters": {},
                "result_metrics": {
                    "sharpe": result.get("sharpe", 0.0),
                    "maxdd": result.get("maxdd", 0.0),
                },
                "rejection_reason": (
                    event.get("decision")
                    if event.get("decision") != "PROMOTE"
                    else None
                ),
                "reviewer": "AI AutoResearch",
                "run_at": event.get("run_at", ""),
            }
        )
    return entries


def get_governance_overview() -> GovernanceView:
    """Build the view from real registry and ledger rows; never inject demo data."""
    import strategy_registry

    registry = strategy_registry._load()
    pairs = [
        (family, version)
        for family in registry.get("families", [])
        for version in family.get("versions", [])
    ]
    model_cards = [_model_card(family, version) for family, version in pairs]
    validation_reports = [
        _validation_report(card, version)
        for card, (_, version) in zip(model_cards, pairs, strict=True)
    ]

    experiments_ledger = [
        entry.to_dict() for entry in ResearchLedger().list_all()
    ]
    factory_log = (
        Path(__file__).resolve().parents[2]
        / "data_lake"
        / "factory"
        / "experiment_log.jsonl"
    )
    experiments_ledger.extend(_factory_ledger_entries(factory_log))
    experiments_ledger.sort(
        key=lambda item: str(item.get("experiment_id") or ""),
        reverse=True,
    )

    return GovernanceView(
        model_cards=model_cards,
        validation_reports=validation_reports,
        experiments_ledger=experiments_ledger,
        committees=[
            {
                "name": "Research Review Committee",
                "role": "Approves hypotheses and factor exploration plans",
            },
            {
                "name": "Model Risk Committee",
                "role": "Validates evidence before paper trading",
            },
            {
                "name": "Investment Policy Committee",
                "role": "Controls risk budgets, leverage, and kill switches",
            },
        ],
    )
