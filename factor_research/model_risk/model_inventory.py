"""Model Inventory & Model Card Management.

Defines the structure for Model Cards and acts as the repository for governance and audit.
Complements Fed SR 11-7 model risk management requirements.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_INVENTORY_PATH = (
    Path(__file__).resolve().parent.parent
    / "data_lake" / "governance" / "model_inventory.json"
)

@dataclass
class ModelCard:
    strategy_id: str                   # family/version
    economic_hypothesis: str           # 经济假设
    data_sources: list[str]            # 数据来源
    train_period: str                  # 训练区间
    oos_period: str                    # 样本外区间
    applicable_regimes: list[str]      # 适用市场状态
    capacity_limit: float              # 容量上限 (CNY)
    style_exposures: dict[str, float]  # 风格暴露
    forbidden_conditions: list[str]    # 禁用条件
    known_failure_cases: list[str]     # 已知失效案例
    owner: str                         # 负责人
    approver: str                      # 审批人
    approval_status: str = "PENDING"   # PENDING | APPROVED | REJECTED
    signature: str | None = None    # 审批签名
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ModelCard:
        return cls(**data)


class ModelInventory:
    """Repository for managing Model Cards."""

    def __init__(self, path: Path = DEFAULT_INVENTORY_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cards: dict[str, ModelCard] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
                for sid, card_data in data.items():
                    self._cards[sid] = ModelCard.from_dict(card_data)
        except Exception:
            pass

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({k: v.to_dict() for k, v in self._cards.items()}, f, indent=4, ensure_ascii=False)

    def register_card(self, card: ModelCard) -> None:
        self._cards[card.strategy_id] = card
        self.save()

    def get_card(self, strategy_id: str) -> ModelCard | None:
        return self._cards.get(strategy_id)

    def list_all(self) -> list[ModelCard]:
        return list(self._cards.values())
