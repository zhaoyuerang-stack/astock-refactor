"""Exposure Reporting.

Monitors portfolio concentration, style factors (Beta, Size),
and industry sector exposure over time.
"""
from __future__ import annotations

from typing import Any

import pandas as pd


def generate_exposure_report(
    weights: pd.DataFrame,
    style_exposures: dict[str, pd.DataFrame], # factor -> date x asset
    industry_mapping: pd.Series | None = None
) -> dict[str, Any]:
    """Report average and maximum portfolio factor and sector exposures."""
    report = {}
    
    # Portfolio concentration: Herfindahl-Hirschman Index (HHI)
    # HHI = sum(w_i^2)
    hhi = (weights ** 2).sum(axis=1)
    report["mean_hhi"] = float(hhi.mean())
    report["max_hhi"] = float(hhi.max())

    # Style exposures
    style_report = {}
    for factor, exp_df in style_exposures.items():
        exp_aligned = exp_df.reindex(index=weights.index, columns=weights.columns, fill_value=0.0)
        port_exp = (weights * exp_aligned).sum(axis=1)
        style_report[factor] = {
            "mean": float(port_exp.mean()),
            "max": float(port_exp.max()),
            "min": float(port_exp.min())
        }
    report["styles"] = style_report

    # Industry exposures
    if industry_mapping is not None:
        ind_exposures = {}
        for ind in industry_mapping.unique():
            is_ind = (industry_mapping == ind).astype(float)
            # Alignment
            is_ind_aligned = is_ind.reindex(weights.columns, fill_value=0.0)
            ind_w = weights.dot(is_ind_aligned)
            ind_exposures[str(ind)] = {
                "mean": float(ind_w.mean()),
                "max": float(ind_w.max())
            }
        report["industries"] = ind_exposures

    return report
