"""StrategyVersion — 不可变的策略版本实体.

A StrategyVersion is the canonical combination of:
  factor_fn × timing_fn × overlay × hyperparameters

Once instantiated, it is frozen — changes require a new version.
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .lifecycle import Lifecycle


@dataclass(frozen=True)
class StrategyVersion:
    """不可变策略版本。factor_fn + timing_fn + overlay + 参数。

    字段说明：
      family  - 母策略 ID (small-cap-size 等，对应 strategy_versions.json 中的 family.id)
      version - 版本号 (v2.2 等)
      desc    - 一句话描述
      factor_fn  - 因子函数 (close, amount, **config) -> factor DataFrame
      timing_fn  - 择时函数 (close, amount, **config) -> timing Series (bool)
      overlay    - 覆盖层实例 (通常来自 core.overlays)
      top_n      - 持仓数量
      rebalance_days - 调仓间隔 (交易日)
      leverage   - 杠杆倍数
      data_sources - 依赖的数据源列表
      status     - 生命周期状态
      created    - 创建日期
      parent     - 进化自哪个版本 (v2.0 等)
      registered - 是否已同步到 strategy_versions.json
    """

    family: str
    version: str
    desc: str
    factor_fn: Callable
    factor_config: dict[str, Any] = field(default_factory=dict)
    timing_fn: Callable | None = None
    timing_config: dict[str, Any] = field(default_factory=dict)
    overlay: Any | None = None
    top_n: int = 25
    rebalance_days: int = 20
    leverage: float = 1.25
    data_sources: tuple[str, ...] = ()
    status: Lifecycle = Lifecycle.INCUBATING
    created: str = ""
    parent: str | None = None
    registered: bool = False

    def __repr__(self) -> str:
        return f"StrategyVersion({self.family}/{self.version}, status={self.status.value})"
