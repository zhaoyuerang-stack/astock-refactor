"""Line 3 — 边际贡献评估 (合并版)。

将候选 hypothesis 对当前 LIVE 组合的"贡献度"作为唯一评判标准。
v3 合并：调用 portfolio.marginal.evaluate (regime-aware + LIVE_D 防御档)
        + 工厂的 config grid (top_n × timing) 创造差异化 returns
"""
from .marginal_eval import (
    DEFAULT_CONFIG_GRID,
    GRADE_PRIORITY,
    NON_SHELVE_GRADES,
    MarginalReport,
    StrategyConfig,
    evaluate_candidate,
    run_candidate_returns,
)

__all__ = [
    "evaluate_candidate",
    "run_candidate_returns",
    "MarginalReport",
    "StrategyConfig",
    "DEFAULT_CONFIG_GRID",
    "GRADE_PRIORITY",
    "NON_SHELVE_GRADES",
]
