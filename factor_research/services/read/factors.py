"""只读因子(alpha 家族)视图 —— 由 registry 家族级派生(Phase 0,零计算)。

Phase 0 不做 IC 计算(那是因子研究页 Phase 1/2 的事),只列出我们台账里
登记的 alpha 家族及其元信息。这样读路径是真实的、registry 派生的,不硬编结论。
"""
from __future__ import annotations

from contracts.views import FactorView


def list_factors() -> list[FactorView]:
    import strategy_registry  # 受控接缝

    data = strategy_registry._load()
    return [
        FactorView(
            name=fam["id"],
            display_name=fam.get("name", ""),
            hypothesis=fam.get("hypothesis", ""),
            regime=fam.get("regime", ""),
            n_versions=len(fam.get("versions", [])),
            n_registered=sum(1 for v in fam.get("versions", []) if v.get("status") == "在册"),
            status=fam.get("status", ""),
        )
        for fam in data.get("families", [])
    ]
