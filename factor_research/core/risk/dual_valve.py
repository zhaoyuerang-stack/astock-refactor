"""
Production Dual-Valve Risk Control Module.

Combines:
1. 1st-Order Style Gate: Portfolio NAV 40-day Moving Average Trend.
2. 2nd-Order Panic Gate: Constituent Volume Acceleration.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_volume_acceleration(
    volume: pd.DataFrame,
    weights: pd.DataFrame | dict,
    velocity_window: int = 5,
    acc_window: int = 5,
    rolling_window: int = 60
) -> pd.Series:
    """Calculate the normalized volume acceleration of the portfolio constituents.
    
    Args:
        volume: Daily volume DataFrame (date x code).
        weights: Daily target weights DataFrame or dict.
        velocity_window: Trailing window for volume velocity (1st derivative).
        acc_window: Trailing window for volume acceleration (2nd derivative).
        rolling_window: Rolling window to standardize acceleration.
        
    Returns:
        Z-score Series of volume acceleration aligned to volume index.
    """
    # 1. Align weights to volume index
    if isinstance(weights, dict):
        from core.engine import _dict_weights_to_df
        weights_df = _dict_weights_to_df(weights, volume.index)
    else:
        weights_df = weights.reindex(volume.index).ffill().fillna(0.0)
        
    # 2. Portfolio aggregate volume (sum of held stocks)
    held_mask = weights_df > 0
    portfolio_vol = (volume * held_mask).sum(axis=1)
    
    # 3. Compute 1st derivative (velocity) and 2nd derivative (acceleration)
    vol_velocity = portfolio_vol.diff(velocity_window)
    vol_acceleration = vol_velocity.diff(acc_window)
    
    # 4. Standardize by rolling standard deviation, replace 0 with NaN to avoid ZeroDivisionError
    vol_acc_std = vol_acceleration.rolling(rolling_window, min_periods=10).std()
    vol_acc_std = vol_acc_std.replace(0.0, np.nan)
    vol_acc_z = (vol_acceleration / vol_acc_std).fillna(0.0)
    return vol_acc_z

def compute_nav_trend_signal(
    returns: pd.Series,
    window: int = 40
) -> pd.Series:
    """Calculate the 1st-order NAV trend style gating switch.
    
    Args:
        returns: Daily strategy returns series.
        window: Moving average window on cumulative NAV.
        
    Returns:
        Boolean style gating Series (1.0 = NAV > MA, 0.0 = NAV <= MA).
    """
    nav = (1 + returns).cumprod()
    nav_ma = nav.rolling(window, min_periods=5).mean()
    
    style_signal = pd.Series(0.0, index=returns.index)
    style_signal[nav > nav_ma] = 1.0
    return style_signal

def apply_dual_valve_gating(
    baseline_returns: pd.Series,
    volume: pd.DataFrame,
    weights: pd.DataFrame | dict,
    trade_dates: pd.DatetimeIndex,
    style_window: int = 40,
    panic_threshold: float = 2.0,
    panic_leverage: float = 0.2,
    smoothing_window: int = 5
) -> pd.Series:
    """Apply dual-valve (NAV MA trend + volume acceleration) risk control gating.
    
    Args:
        baseline_returns: Strategy returns series without timing.
        volume: Daily trading volume panel.
        weights: Portfolio weights DataFrame or dict.
        trade_dates: Index of all trade dates.
        style_window: NAV trend moving average window.
        panic_threshold: Z-score threshold for volume acceleration panic.
        panic_leverage: Downscaled exposure multiplier when panic triggers.
        smoothing_window: Moving average window to smooth the combined timing signal.
        
    Returns:
        Daily timing signal Series (exposure multiplier) aligned to trade_dates.
    """
    # 1. 1st-Order Style Switch (NAV Trend)
    style_base = compute_nav_trend_signal(baseline_returns, style_window)
    style_signal = style_base.reindex(trade_dates).ffill().fillna(0.0)
    
    # 2. 2nd-Order Panic Switch (Volume Acc)
    vol_acc_z = compute_volume_acceleration(volume, weights)
    panic_signal = pd.Series(1.0, index=trade_dates)
    panic_signal[vol_acc_z > panic_threshold] = panic_leverage
    
    # 3. Combine and Smooth
    timing_combined = style_signal * panic_signal
    timing_smoothed = timing_combined.rolling(smoothing_window).mean().fillna(1.0)
    return timing_smoothed
