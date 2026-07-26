"""回测默认配置的只读视图(生产 illiquidity/v3.1 口径)。

这些值与 services/actions/run_backtest 的默认一致,且对齐成本铁律
(买 0.225% / 卖 0.275% / 融资 6.5%)。设置页将以"只读"展示它们。
"""
from __future__ import annotations


def production_defaults() -> dict:
    return {
        "start": "2018-01-01",
        "top_n": 25,
        "rebalance_days": 20,
        "factor_window": 20,
        "timing_ma": 16,
        "exposure_mode": "PureTrend MA16 Band dynamic 0-1.5x",
        # 成本铁律(只读,UI 不可调低)
        "buy_cost": 0.00225,
        "sell_cost": 0.00275,
        "financing_rate": 0.065,
    }
