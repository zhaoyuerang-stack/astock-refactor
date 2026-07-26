"""研究实验只读视图:假设池漏斗 + 假设列表 + 已登记实验(可复现元数据)。

数据源(受控接缝,services 桥接探索层):
- factory.pool.HypothesisPool  ← 假设池(DRAFTED→QUEUED→L0~L3→PROMOTED/DISCARDED/SHELVED)
- strategy_registry            ← 已登记实验(晋级成功者,带 IS/OOS/压力 绩效)

Hypothesis.id 本身是内容哈希(可复现身份);registry 版本的 config 哈希作为实验可复现键。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from contracts.views import (
    FunnelView,
    HypothesisView,
    RegisteredExperimentView,
    ResearchRunIndexView,
)

ROOT = Path(__file__).resolve().parents[2]

# 漏斗顺序(SPEC factory 流水线);DISCARDED/SHELVED 为旁路终态
FUNNEL_ORDER = ["drafted", "queued", "l0_passed", "l1_passed", "l2_passed", "l3_passed", "promoted"]
_SIDE = ["discarded", "shelved"]


def _pool():
    from factory.pool.pool_repo import HypothesisPool
    return HypothesisPool()


def funnel() -> FunnelView:
    pool = _pool()
    counts = pool.count_by_status()
    n_reg = len(_registry_versions())
    counts = {**counts, "promoted": counts.get("promoted", 0) + n_reg}  # 晋级=台账登记数
    stages = [{"stage": s, "count": int(counts.get(s, 0))} for s in FUNNEL_ORDER]
    side = [{"stage": s, "count": int(counts.get(s, 0))} for s in _SIDE]
    total = len(pool)
    discarded = counts.get("discarded", 0)
    return FunnelView(
        total=total,
        stages=stages,
        side=side,
        discard_ratio=round(discarded / total, 3) if total else 0.0,
        registered=n_reg,
    )


def hypotheses(status: str | None = None, limit: int = 60) -> list[HypothesisView]:
    from factory.ontology.hypothesis import HypothesisStatus
    pool = _pool()
    if status:
        hyps = pool.list_by_status(HypothesisStatus(status))
    else:
        hyps = pool.all()
    out: list[HypothesisView] = []
    for h in hyps[:limit]:
        thesis = getattr(h, "thesis", None)
        out.append(HypothesisView(
            id=h.id,
            name=getattr(h, "name", ""),
            factor_fn_name=getattr(h, "factor_fn_name", ""),
            factor_params=getattr(h, "factor_params", {}) or {},
            timing_fn_name=getattr(h, "timing_fn_name", None),
            status=h.status.value if hasattr(h.status, "value") else str(h.status),
            source=getattr(h, "source", ""),
            mechanism=getattr(thesis, "mechanism", "") if thesis else "",
            citation=getattr(thesis, "citation", "") if thesis else "",
            created_at=getattr(h, "created_at", ""),
        ))
    return out


def _registry_versions() -> list[dict]:
    import strategy_registry
    data = strategy_registry._load()
    rows = []
    for fam in data.get("families", []):
        for v in fam.get("versions", []):
            rows.append({"family": fam["id"], "family_name": fam.get("name", ""), **v})
    return rows


def registered_experiments() -> list[RegisteredExperimentView]:
    out: list[RegisteredExperimentView] = []
    for v in _registry_versions():
        cfg = v.get("config", {}) or {}
        cfg_hash = hashlib.sha1(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12]
        out.append(RegisteredExperimentView(
            strategy_id=f"{v['family']}/{v.get('version','')}",
            family_name=v.get("family_name", ""),
            version=v.get("version", ""),
            status=v.get("status", ""),
            date=v.get("date", ""),
            config_hash=cfg_hash,
            config=cfg,
            metrics=v.get("metrics", {}) or {},
            data_scope=v.get("data_scope", {}) if isinstance(v.get("data_scope"), dict) else {},
        ))
    return out


def research_run_index() -> ResearchRunIndexView:
    """Recent research conclusions archived from research_ledger."""
    from research_ledger.ledger import load_research_run_index

    return ResearchRunIndexView(**load_research_run_index())
