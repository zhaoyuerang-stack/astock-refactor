"""Benchmark Comparison.

Evaluates tracking error, active premium, correlation, and beta against benchmark indices.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def compare_to_benchmark(
    strategy_returns: pd.Series,
    benchmark_returns: pd.Series
) -> dict[str, Any]:
    """Calculate comparative statistics against a benchmark index."""
    common_idx = strategy_returns.index.intersection(benchmark_returns.index)
    if len(common_idx) == 0:
        return {}

    s = strategy_returns.loc[common_idx]
    b = benchmark_returns.loc[common_idx]

    s_ann = float(s.mean() * 252)
    b_ann = float(b.mean() * 252)
    s_vol = float(s.std() * np.sqrt(252))
    b_vol = float(b.std() * np.sqrt(252))

    active = s - b
    tracking_error = float(active.std() * np.sqrt(252))
    information_ratio = (s_ann - b_ann) / tracking_error if tracking_error > 0 else 0.0

    covariance = np.cov(s, b)[0, 1]
    b_var = np.var(b)
    beta = float(covariance / b_var) if b_var > 0 else 1.0
    correlation = float(np.corrcoef(s, b)[0, 1]) if b_var > 0 and np.var(s) > 0 else 0.0

    return {
        "annual_return": s_ann,
        "benchmark_annual_return": b_ann,
        "volatility": s_vol,
        "benchmark_volatility": b_vol,
        "beta": beta,
        "correlation": correlation,
        "tracking_error": tracking_error,
        "information_ratio": information_ratio
    }
