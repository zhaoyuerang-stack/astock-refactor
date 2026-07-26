"""只读包住 strategy_registry —— 台账 family/version → StrategyView。

只读 ``_load()``;绝不调 register/_save(台账唯一写入口仍是 strategy_registry)。
"""
from __future__ import annotations

import json
from pathlib import Path

from contracts.views import StrategyDetailView, StrategyView


def list_strategies() -> list[StrategyView]:
    import strategy_registry  # 受控接缝:services 可碰台账

    data = strategy_registry._load()
    out: list[StrategyView] = []
    for fam in data.get("families", []):
        for v in fam.get("versions", []):
            out.append(StrategyView(
                strategy_id=f"{fam['id']}/{v.get('version', '')}",
                family=fam["id"],
                family_name=fam.get("name", ""),
                family_status=fam.get("status", ""),
                version=v.get("version", ""),
                status=v.get("status", ""),
                hypothesis=fam.get("hypothesis", ""),
                regime=fam.get("regime", ""),
                desc=v.get("desc", ""),
                data_scope=v.get("data_scope", ""),
                metrics=v.get("metrics", {}) or {},
                config=v.get("config", {}) or {},
                notes=v.get("notes", ""),
                capacity_m=float(fam.get("capacity_m", 0.0)),
                admission=v.get("admission", {}) or {},
                nine_gate=v.get("nine_gate", {}) or {},
                style_betas=fam.get("style_betas", {}) or {},
                failure_boundaries=fam.get("failure_boundaries", {}) or {},
                decay_signal=fam.get("decay_signal", "") or "",
                decay_check=v.get("decay_check", {}) or {},
            ))
    return out


def get_strategy(family: str, version: str) -> StrategyView:
    strategy_id = f"{family}/{version}"
    item = next((row for row in list_strategies() if row.strategy_id == strategy_id), None)
    if item is None:
        raise KeyError(strategy_id)
    return item


def get_strategy_detail(family: str, version: str) -> StrategyDetailView:
    from research_ledger.ledger import load_research_run_index

    strategy = get_strategy(family, version)
    runs = [
        row for row in load_research_run_index().get("latest_runs", [])
        if row.get("hypothesis") == strategy.strategy_id
    ]
    root = Path(__file__).resolve().parents[2]
    artifacts: dict[str, object] = {}
    for run in runs:
        for raw_path in run.get("artifact_paths", []) or []:
            path = Path(raw_path)
            if not path.is_absolute():
                path = root / path
            try:
                resolved = path.resolve()
                resolved.relative_to(root.resolve())
            except (OSError, ValueError):
                continue
            if not resolved.exists() or resolved.suffix.lower() != ".json":
                continue
            try:
                artifacts[resolved.stem] = json.loads(resolved.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
    return StrategyDetailView(strategy=strategy, research_runs=runs, artifacts=artifacts)
