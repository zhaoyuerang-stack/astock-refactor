"""Hypothesis — 因子假设，工厂原料。

身份 = content_hash(factor + params + timing + 数据依赖)。
不可变；改 = 新 Hypothesis（带 parent_id）。
"""
import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class HypothesisStatus(Enum):
    """Hypothesis 在 Cheap-First 流水线中的位置。"""
    DRAFTED = "drafted"        # 刚产生，待评分入队
    QUEUED = "queued"          # 入队，等 L0
    L0_PASSED = "l0_passed"
    L1_PASSED = "l1_passed"
    L2_PASSED = "l2_passed"
    L3_PASSED = "l3_passed"    # 等 marginal eval
    PROMOTED = "promoted"      # 升 LIVE_X（C/K/P）
    DISCARDED = "discarded"
    SHELVED = "shelved"        # 边际不够，留池子


@dataclass(frozen=True)
class EconomicThesis:
    """经济学论证（F-1 必填）。轻量化：一句机制 + 一个引用即可。"""

    mechanism: str               # "尾部风险厌恶 → 小盘低波风险溢价"
    citation: str = ""           # "Kahneman 1979" / "中信研报 XX" / "经验观察"
    falsifiability: str = ""     # 怎么算被证伪（可选）

    def is_valid(self) -> bool:
        return bool(self.mechanism.strip())


@dataclass(frozen=True)
class Hypothesis:
    """因子假设 = 原料。

    内容哈希（前 16 位）作为 id。同因子+同参数 → 同 id。
    """

    name: str                                       # "small_cap_window90"
    description: str
    factor_fn_name: str                             # "factors.small_cap.small_cap_factor"
    factor_params: dict[str, Any] = field(default_factory=dict)
    timing_fn_name: str | None = None
    timing_params: dict[str, Any] = field(default_factory=dict)
    data_dependencies: tuple[str, ...] = ()         # ("price/close", "price/amount")
    thesis: EconomicThesis | None = None

    source: str = "manual"                          # mutation | llm_paper | anomaly | manual
    source_ref: str | None = None                # 父 hyp_id / DOI / 异象 id
    parent_hypothesis_id: str | None = None
    novelty_score: float = 0.0                      # 与现有 LIVE 的距离，0=完全重复
    estimated_cost_seconds: float = 0.0
    status: HypothesisStatus = HypothesisStatus.DRAFTED
    created_at: str = ""

    @property
    def id(self) -> str:
        payload = {
            "factor": self.factor_fn_name,
            "params": _stable(self.factor_params),
            "timing": self.timing_fn_name or "",
            "timing_params": _stable(self.timing_params),
        }
        digest = hashlib.sha256(_stable(payload).encode()).hexdigest()
        return digest[:16]

    def __repr__(self):
        return (f"Hypothesis({self.name}, id={self.id[:8]}, "
                f"status={self.status.value}, source={self.source})")


def _stable(obj: Any) -> str:
    """JSON encoding stable for content hashing."""
    return json.dumps(obj, sort_keys=True, default=str)
