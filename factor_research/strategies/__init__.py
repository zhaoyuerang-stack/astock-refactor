"""策略版本包：因子 × overlay × 参数 → 不可变 StrategyVersion 实体."""
from .base import StrategyVersion
from .lifecycle import Lifecycle, should_retire

__all__ = ["StrategyVersion", "Lifecycle", "should_retire"]
