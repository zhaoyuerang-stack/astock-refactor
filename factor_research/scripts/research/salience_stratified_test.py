"""Research script: Stratification and Long-Short Analysis of Salience Theory Factor.

This script:
1. Loads price panels and computes raw faded Salience Covariance (-ST_cov).
2. Performs size-and-industry double neutralization.
3. Computes 5-quintile stratified returns for both raw and neutralized factors.
4. Reports the annualized Long-Short (Q5 - Q1) returns, Sharpe ratios, and Max Drawdowns.
"""
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
os.chdir(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, str(Path.cwd()))

import numpy as np
import pandas as pd

from engine.factor_analysis import factor_summary
from factors.alpha.transforms import neutralize
from strategies.small_cap import load_price_panels


def load_industry_groups():
    """Load latest industry mapping from fundamental parquet."""
    fund = pd.read_parquet("data_lake/fundamental_batch.parquet", columns=["code", "avail_date", "industry"])
    mapping = fund.dropna(subset=["industry"]).sort_values("avail_date").drop_duplicates("code", keep="last")
    stock_to_ind = dict(zip(mapping["code"], mapping["industry"], strict=True))
    return stock_to_ind

def compute_salience_covariance(close, W=20, theta=0.1, delta=0.7):
    """Computes faded Salience Covariance (-ST_cov)."""
    returns = close.pct_change(fill_method=None)
    market_returns = returns.mean(axis=1)
    
    # 1. Daily salience
    r_diff = returns.sub(market_returns, axis=0).abs()
    r_sum = returns.abs().add(market_returns.abs(), axis=0) + theta
    salience = r_diff / r_sum
    
    # 2. Vectorized rolling ranks and weights
    ranks = {}
    valid_count = pd.DataFrame(0, index=salience.index, columns=salience.columns)
    for j in range(W):
        valid_count += salience.shift(j).notna().astype(int)
        
    for s in range(W):
        better_count = pd.DataFrame(0, index=salience.index, columns=salience.columns)
        for j in range(W):
            if j == s:
                continue
            better_count += (salience.shift(j) > salience.shift(s)).astype(int)
        ranks[s] = (better_count + 1).where(salience.shift(s).notna())
        
    denom = delta * (1 - delta ** valid_count) / (1 - delta)
    
    est_return = pd.DataFrame(0.0, index=returns.index, columns=returns.columns)
    for s in range(W):
        weight_s = (delta ** ranks[s]) / denom
        r_lag = returns.shift(s)
        est_return += weight_s * r_lag.fillna(0.0)
        
    avg_return = returns.rolling(W).mean()
    st_cov = est_return - avg_return
    return -st_cov

def main():
    print("=" * 80)
    print("  Stratification and Long-Short Analysis of Salience Theory Factor")
    print("=" * 80)
    
    # Load prices
    print("\n[1/3] Loading price panels...")
    close, volume, amount = load_price_panels("2010-01-01")
    
    # Compute raw faded Salience Covariance factor
    print("\n[2/3] Computing raw faded Salience Covariance...")
    raw_factor = compute_salience_covariance(close, W=20, theta=0.1, delta=0.7)
    
    # Neutralize factor
    print("  Creating industry group panel...")
    stock_to_ind = load_industry_groups()
    industry_panel = pd.DataFrame("Unknown", index=close.index, columns=close.columns)
    for col in close.columns:
        if col in stock_to_ind:
            industry_panel[col] = stock_to_ind[col]
            
    print("  Creating size decile panel...")
    avg_amount = amount.rolling(20).mean()
    size_ranks = avg_amount.rank(axis=1, pct=True)
    size_bins = (size_ranks * 10).fillna(0).astype(int)
    
    print("  Neutralizing factor against size & industry...")
    neut_factor = neutralize(neutralize(raw_factor, industry_panel), size_bins)
    
    # Run factor summary (stratification and long-short)
    print("\n[3/3] Running stratification tests (2018-2026)...")
    STATS_START = "2018-01-01"
    
    raw_clean = raw_factor.loc[STATS_START:]
    neut_clean = neut_factor.loc[STATS_START:]
    
    for p in [1, 20]:
        fwd_ret = close.pct_change(p).shift(-p).replace([np.inf, -np.inf], np.nan)
        fwd_clean = fwd_ret.loc[STATS_START:]
        
        raw_sum = factor_summary(raw_clean, fwd_clean, "Raw_Salience_Cov", n_quantile=5)
        neut_sum = factor_summary(neut_clean, fwd_clean, "Neutralized_Salience_Cov", n_quantile=5)
        
        # Adjust annualized return for 20d overlap if p=20
        if p == 20:
            raw_sum["LS_annual"] = raw_sum["LS_annual"] / 20.0
            raw_sum["LS_sharpe"] = raw_sum["LS_sharpe"] / np.sqrt(20.0)
            neut_sum["LS_annual"] = neut_sum["LS_annual"] / 20.0
            neut_sum["LS_sharpe"] = neut_sum["LS_sharpe"] / np.sqrt(20.0)
            
        print("\n" + "=" * 80)
        print(f"  Long-Short Summary ({p}-day Forward Returns, 5 Quintiles)")
        print("=" * 80)
        for s in [raw_sum, neut_sum]:
            print(f"Factor: {s['factor']}")
            print(f"  Rank IC Mean:       {s['IC_mean']:+.4f}")
            print(f"  Rank ICIR:          {s['ICIR']:+.3f}")
            print(f"  Long-Short Annual:  {s['LS_annual']:+.2%}")
            print(f"  Long-Short Sharpe:  {s['LS_sharpe']:.2f}")
            print(f"  Long-Short MaxDD:   {s['LS_maxdd']:.2%}")
            print("-" * 40)


if __name__ == "__main__":
    main()
