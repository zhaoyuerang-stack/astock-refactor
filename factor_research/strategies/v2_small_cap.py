"""M1 母策略：小盘量价因子 —— 各版本实例化."""
from core.overlays.pure_trend_overlay import PureTrendOverlay
from factors.small_cap import small_cap_factor, small_cap_timing

from .base import StrategyVersion
from .lifecycle import Lifecycle

# ── v2.0: M1 基线（小盘量价 + MA16 择时，无 overlay）──
v20 = StrategyVersion(
    family="small-cap-size",
    version="v2.0",
    desc="小盘量价因子(size60) + MA16 择时 + 1.25x杠杆, data_lake干净口径",
    factor_fn=small_cap_factor,
    factor_config={"window": 60},
    timing_fn=small_cap_timing,
    timing_config={"ma_window": 16},
    overlay=None,
    top_n=25,
    rebalance_days=20,
    leverage=1.25,
    data_sources=("price/daily",),
    status=Lifecycle.LIVE,
    created="2026-06-03",
    parent=None,
    registered=True,
)

# ── v2.1: sw=30 + reb=15 + top=30（参数优化版，WF 验证通过）──
v21 = StrategyVersion(
    family="small-cap-size",
    version="v2.1",
    desc="sw30+reb15+top30参数优化, WF 12年75%胜率+3.5pp中位改善, 夏普1.64(原1.38)",
    factor_fn=small_cap_factor,
    factor_config={"window": 30},
    timing_fn=small_cap_timing,
    timing_config={"ma_window": 16},
    overlay=None,
    top_n=30,
    rebalance_days=15,
    leverage=1.25,
    data_sources=("price/daily",),
    status=Lifecycle.LIVE,
    created="2026-06-06",
    parent="v2.0",
    registered=True,
)

# ── v2.2: v2.0 × 纯趋势 tw=2 overlay（前视泄漏修复后不工作, 退役）──
v22 = StrategyVersion(
    family="small-cap-size",
    version="v2.2",
    desc="v2.0×PT2, WF选tw=2但shift(1)修复后年化仅+2.3%低于基线, 已退役",
    factor_fn=small_cap_factor,
    factor_config={"window": 60},
    timing_fn=small_cap_timing,
    timing_config={"ma_window": 16},
    overlay=PureTrendOverlay(trend_window=2),
    top_n=25,
    rebalance_days=20,
    leverage=1.25,
    data_sources=("price/daily",),
    status=Lifecycle.RETIRED,
    created="2026-06-06",
    parent="v2.0",
    registered=False,
)
