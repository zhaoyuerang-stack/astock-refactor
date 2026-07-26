"""Canonical builders for core.engine.Signal objects."""
from __future__ import annotations

import pandas as pd


def factor_to_signal(
    factor: pd.DataFrame,
    *,
    top_n: int = 25,
    direction: int = 1,
    rebalance_freq: str = "20D",
    timing: pd.Series | None = None,
    family: str = "",
    version: str = "",
):
    """Wrap a factor panel into ``core.engine.Signal``."""
    from core.engine import Signal

    return Signal(
        factor=factor,
        top_n=top_n,
        direction=direction,
        rebalance_freq=rebalance_freq,
        timing=timing,
        family=family,
        version=version,
    )
