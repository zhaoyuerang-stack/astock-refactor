"""Portfolio construction utilities.

This module provides:
- ``to_signal()`` – bridge factor panels to ``core.engine.Signal``
- ``performance_metrics()`` – metrics from a return series
"""
import numpy as np
import pandas as pd

from engine.metrics import max_drawdown


def performance_metrics(port_ret: pd.Series, rf: float = 0.02, fmt: bool = False) -> dict:
    """Performance summary.

    Parameters
    ----------
    port_ret : pd.Series
        Daily portfolio returns.
    rf : float
        Risk-free rate (annual).
    fmt : bool
        If True, return formatted strings (legacy behaviour).
        If False (default), return raw floats for programmatic use.
    """
    ret = port_ret.dropna()
    annual_ret = ret.mean() * 252
    annual_vol = ret.std() * np.sqrt(252)
    sharpe = (annual_ret - rf) / annual_vol if annual_vol > 0 else np.nan
    # 本函数与 canonical metrics() 契约不同(减 rf / nan 兜底 / fmt 模式),整体不收编;
    # 但回撤公式与 engine.metrics.max_drawdown 逐字相同,委托之。
    maxdd = max_drawdown(ret)
    calmar = annual_ret / abs(maxdd) if maxdd != 0 else np.nan
    if fmt:
        return {
            "年化收益": f"{annual_ret:.2%}",
            "年化波动": f"{annual_vol:.2%}",
            "夏普比率": f"{sharpe:.2f}",
            "最大回撤": f"{maxdd:.2%}",
            "卡玛比率": f"{calmar:.2f}",
        }
    return {
        "annual": annual_ret,
        "vol": annual_vol,
        "sharpe": sharpe,
        "maxdd": maxdd,
        "calmar": calmar,
    }


def to_signal(factor: pd.DataFrame, n: int = 100, direction: int = 1,
              rebalance_freq: str = "W", family: str = "", version: str = ""):
    """Backward-compatible wrapper for ``engine.signal_factory.factor_to_signal``."""
    from engine.signal_factory import factor_to_signal

    return factor_to_signal(
        factor,
        top_n=n,
        direction=direction,
        rebalance_freq=rebalance_freq,
        family=family,
        version=version,
    )
