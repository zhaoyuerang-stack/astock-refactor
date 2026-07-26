"""Policy-layer candidate-pool transformations."""
from __future__ import annotations

import pandas as pd


def apply_veto_filter(
    host_scores: pd.Series,
    veto_scores: pd.Series,
    *,
    top_n: int,
    veto_q: float = 0.10,
) -> pd.Series:
    """Filter the host candidate pool and refill top-N from survivors.

    The output is equal weights over selected survivors. It intentionally does
    not reduce exposure; exposure control is a separate overlay/position-sizing
    problem and must not be mixed with veto attribution.
    """
    host = host_scores.dropna()
    veto = veto_scores.reindex(host.index).dropna()
    if len(veto):
        survivors = veto[veto > veto.quantile(veto_q)].index
        host = host.reindex(survivors).dropna()
    if len(host) < top_n:
        return pd.Series(dtype="float64")
    selected = host.nlargest(top_n).index
    return pd.Series(1.0 / top_n, index=selected, dtype="float64")
