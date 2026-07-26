"""High-Quality Momentum Factor Calculation.

Combines price momentum, price path smoothness (Kaufman ER), and fundamental quality (ROE, Gross Margin, and Cash Flow Yield).
"""
from pathlib import Path

import numpy as np
import pandas as pd


def load_quality_fundamentals(trade_dates, data_lake_path=Path("data_lake")):
    """Loads ROE, Gross Margin, and CFO per Share from data lake, aligned to trade calendar."""
    fund = pd.read_parquet(data_lake_path / "fundamental_batch.parquet")
    fund["avail_date"] = pd.to_datetime(fund["avail_date"])
    
    raw_all = pd.read_parquet(data_lake_path / "price/daily_raw_all.parquet")
    raw_all["date"] = pd.to_datetime(raw_all["date"])
    raw_close = raw_all.pivot(index="date", columns="code", values="raw_close").reindex(trade_dates).ffill()
    
    trade_idx = pd.DatetimeIndex(trade_dates)
    panels = {}
    for f in ["roe", "gross_margin", "cfo_ps"]:
        sub = fund[["avail_date", "code", f]].dropna()
        sub = sub.sort_values("avail_date").drop_duplicates(["code", "avail_date"], keep="last")
        pivot = sub.pivot(index="avail_date", columns="code", values=f)
        aligned = pivot.reindex(pivot.index.union(trade_idx)).ffill().reindex(trade_idx)
        aligned = aligned.reindex(columns=raw_close.columns)
        panels[f] = aligned
        
    cfo_yield = panels["cfo_ps"] / raw_close.replace(0, np.nan)
    return panels["roe"], panels["gross_margin"], cfo_yield, raw_close

def build_hq_momentum_factor(panels, universe_size=800, lookback=60, q_filter_threshold=0.60):
    """Computes the High-Quality Momentum factor.
    
    1. Momentum: N-day return lagged by 20 days.
    2. Smoothness: Kaufman's Efficiency Ratio (ER) over N days.
    3. Quality Score: Rank-sum of ROE, Gross Margin, and Cash Flow Yield.
    4. Combines them using a Quality filter (only keeping top Q% quality stocks).
    """
    close = panels["close"]
    amount = panels["amount"]
    raw_close = panels["raw_close"]
    trade_dates = close.index
    
    # Load fundamental quality metrics
    roe, gross_margin, cfo_yield, _ = load_quality_fundamentals(trade_dates)
    
    # 1. Build Universe (Top N by rolling trading volume * price)
    cap = amount.rolling(20).mean() * raw_close
    univ = cap.rank(axis=1, ascending=False, pct=False) <= universe_size
    
    # 2. Momentum
    momentum = close.pct_change(lookback, fill_method=None).shift(20)
    mom_r = momentum.rank(axis=1, pct=True, na_option="bottom")
    
    # 3. Path Smoothness (Kaufman ER)
    price_change = (close - close.shift(lookback)).abs()
    path_len = close.diff().abs().rolling(lookback).sum()
    er = price_change / (path_len + 1e-8)
    er_r = er.rank(axis=1, pct=True, na_option="bottom")
    
    # Smooth Momentum
    smooth_mom = (mom_r * er_r).rank(axis=1, pct=True, na_option="bottom")
    
    # 4. Quality Score
    roe_r = roe.rank(axis=1, pct=True, na_option="bottom")
    margin_r = gross_margin.rank(axis=1, pct=True, na_option="bottom")
    cf_r = cfo_yield.rank(axis=1, pct=True, na_option="bottom")
    quality = (roe_r + margin_r + cf_r) / 3.0
    
    # Filter by quality threshold within the universe
    q_filter = quality.where(univ).rank(axis=1, pct=True) >= q_filter_threshold
    
    # Composite factor
    comp_factor = smooth_mom.where(univ & q_filter)
    
    return comp_factor, univ
