"""Participation Rate Calculations.

Measures trade sizes relative to historical ADV to prevent market disruption.
"""
from __future__ import annotations

import pandas as pd


def calculate_participation_rates(
    target_trades: pd.Series,               # Asset codes -> trade shares
    volume_df: pd.DataFrame,                # ADV or volume on trade date
    date: pd.Timestamp
) -> pd.Series:
    """Calculate the participation rate for each stock trade on a given date.

    Rate = Trade Shares / ADV
    """
    if date not in volume_df.index:
        return pd.Series(0.0, index=target_trades.index)

    adv = volume_df.loc[date]
    aligned_adv = adv.reindex(target_trades.index, fill_value=1.0)
    
    # Avoid division by zero
    aligned_adv = aligned_adv.replace(0.0, 1.0)

    rates = target_trades.abs() / aligned_adv
    return rates
