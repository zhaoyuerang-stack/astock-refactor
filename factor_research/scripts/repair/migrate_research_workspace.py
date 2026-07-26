"""Idempotent migration for the unified Web research workspace.

Actions:
1. Copy legacy AutoResearch review decisions into the generic review ledger.
2. Register ontology_industry/v1.0-shadow through strategy_registry.
3. Link existing timing/shadow JSON artifacts to family/version research runs.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def migrate_legacy_reviews(*, review_repo=None, legacy_queue=None) -> int:
    from factory.autoresearch import ReviewQueue
    from research_ledger.workspace import ResearchReviewRepository

    review_repo = review_repo if review_repo is not None else ResearchReviewRepository()
    legacy_queue = legacy_queue if legacy_queue is not None else ReviewQueue()
    return review_repo.migrate_autoresearch(legacy_queue.all())


def ensure_ontology_shadow_registered() -> bool:
    import strategy_registry

    family_id = "ontology_industry"
    version_id = "v1.0-shadow"
    data = strategy_registry._load()
    existing = next((row for row in data.get("families", []) if row.get("id") == family_id), None)
    if existing and any(row.get("version") == version_id for row in existing.get("versions", [])):
        return False

    strategy_registry.register_family(
        family_id,
        "产业链本体与 BOM 成本传导",
        hypothesis="BOM 成本传导与议价权差异形成行业层面的预期差；当前仅作影子观察，不参与生产权重。",
        regime="产业成本或供需结构发生可验证变化的观察期",
        decay_signal="影子组合持续跑输行业基准，或后续财报方向与传导预测长期相反",
        status="active",
        capacity_m=0.0,
        failure_boundaries={},
    )
    strategy_registry.register(
        family_id,
        version_id,
        "产业链本体研究的正式观察版本；身份已入台账，但尚无可声明的 Nine-Gate 或正式绩效。",
        config={
            "mode": "SHADOW",
            "signal_source": "data_lake/research_signals/ontology_predictions.json",
            "production_weight": 0.0,
        },
        data_scope={
            "source": "research_signals + shadow observation",
            "period": "forward observation",
            "survivorship_bias": None,
        },
        metrics={},
        status="候选",
        notes="观察版本：不得参与生产组合；未编造收益、回撤、夏普或 Gate 结果。",
        evidence={
            "artifact_paths": [
                "data_lake/agent/shadow_incubation_log.json",
                "data_lake/research_signals/ontology_predictions.json",
                "reports/islands/shadow_ontology_performance.json",
            ]
        },
        admission={},
        nine_gate={},
    )
    return True


def migrate_artifact_links(*, ledger=None, index_path=None) -> int:
    from research_ledger.ledger import ResearchLedger, ResearchRunRecord, record_research_run

    ledger = ledger if ledger is not None else ResearchLedger()
    existing = {(row.hypothesis, row.source) for row in ledger.list_research_runs()}
    specs = [
        ResearchRunRecord(
            script="scripts/research/validate_amount_timing.py",
            hypothesis="amount-timing/v1.0",
            source="amount_timing_validation",
            data_vintage={"artifact_migration": True},
            metrics={},
            verdict="REFERENCE",
            next_action="REVIEW",
            artifact_paths=["reports/ops/amount_timing_validation.json"],
            notes="Existing timing sensitivity report linked to its registered reference version.",
        ),
        ResearchRunRecord(
            script="scripts/research/run_ontology_shadow_pipeline.py",
            hypothesis="ontology_industry/v1.0-shadow",
            source="ontology_shadow",
            data_vintage={"artifact_migration": True},
            metrics={},
            verdict="SHADOW",
            next_action="KEEP_SHADOW",
            artifact_paths=[
                "data_lake/agent/shadow_incubation_log.json",
                "data_lake/research_signals/ontology_predictions.json",
                "reports/islands/shadow_ontology_performance.json",
            ],
            notes="Existing ontology shadow artifacts linked without inventing performance or Gate evidence.",
        ),
    ]
    added = 0
    for record in specs:
        if (record.hypothesis, record.source) in existing:
            continue
        record_research_run(record, ledger=ledger, index_path=index_path)
        added += 1
    return added


def main() -> int:
    reviews = migrate_legacy_reviews()
    registered = ensure_ontology_shadow_registered()
    artifacts = migrate_artifact_links()
    print(f"legacy_reviews={reviews} ontology_registered={registered} artifact_links={artifacts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
