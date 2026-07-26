"""Research script: Testing Salience Theory (STR) within the Small-cap Universe.

This script:
1. Loads A-share daily prices and volumes.
2. Filters the active universe to the small-cap segment (bottom 50% of 20-day rolling trading volume).
3. Computes the faded Salience Covariance (-ST_cov) and faded Expected Return (-E^ST[R]).
4. Builds a blended factor: Size Rank + Salience Rank.
5. Runs backtests using the standard BacktestEngine, including cost models and small-cap MA16 timing.
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

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.small_cap import small_cap_timing
from factors.utils import mad_clip, safe_zscore
from strategies.small_cap import build_rebalance_weights, load_price_panels


def compute_salience_factors(close, W=20, theta=0.1, delta=0.7):
    """Computes Salience Theory factors: est_return and st_cov."""
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
    
    return est_return, st_cov

def main():
    print("=" * 80)
    print("  Salience Theory (STR) within Small-Cap Universe")
    print("=" * 80)
    
    # Load prices
    print("\n[1/3] Loading price panels...")
    close, volume, amount = load_price_panels("2010-01-01")
    prices = PricePanel(close=close, volume=volume, amount=amount)
    
    # Compute timing signal (same as small-cap strategy MA16 timing)
    print("  Computing small-cap timing signal...")
    timing, _, _ = small_cap_timing(close, amount, ma_window=16)
    
    # Compute salience factors
    print("\n[2/3] Computing salience factors...")
    est_return, st_cov = compute_salience_factors(close, W=20, theta=0.1, delta=0.7)
    
    # Standardize baseline size factor: low turnover proxy for market-cap (negative avg amount)
    size_factor = -np.log(amount.rolling(60).mean() + 1)
    
    # Define universes: bottom 50% by 20-day rolling trading volume
    liq_rank = amount.rolling(20).mean().rank(axis=1, pct=True)
    small_univ = liq_rank < 0.50
    
    # Let's test three factors within the small-cap universe:
    # 1. Pure Small-Cap size factor (baseline)
    # 2. Faded Salience Covariance (-ST_cov)
    # 3. Faded Expected Return (-E^ST[R])
    # 4. Blend of Size and Faded Salience Covariance (50% size, 50% -ST_cov)
    # 5. Blend of Size and Faded Expected Return (50% size, 50% -E^ST[R])
    
    factor_pool = {
        "Baseline_Size": size_factor,
        "Salience_Covariance": -st_cov,
        "Salience_Expected_Return": -est_return,
        "Blend_Size_ST_cov": (safe_zscore(mad_clip(size_factor)) + safe_zscore(mad_clip(-st_cov))) / 2.0,
        "Blend_Size_E_ST_R": (safe_zscore(mad_clip(size_factor)) + safe_zscore(mad_clip(-est_return))) / 2.0,
    }
    
    # Apply small-cap universe filter
    for name in factor_pool:
        factor_pool[name] = factor_pool[name].where(small_univ)
        
    print("\n[3/3] Running backtests...")
    STATS_START = "2018-01-01"
    cost = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065)
    config = BacktestConfig(start=STATS_START, cost=cost, leverage=1.25)
    
    results_summary = []
    for name, factor_df in factor_pool.items():
        print(f"\n--- Backtesting strategy: {name} ---")
        try:
            # Rebalance weights: pick top 25 stocks in the universe
            z_factor = safe_zscore(mad_clip(factor_df))
            sched = build_rebalance_weights(z_factor, close, top_n=25, rebalance_days=20)
            
            engine = BacktestEngine(prices=prices, config=config)
            sig = Signal(weights=sched, timing=timing, family="salience-sc", version="v1.0")
            res = engine.run(sig)
            
            m = res.metrics
            print(f"  Annual Return: {m['annual']:.2%}")
            print(f"  Volatility:    {m['vol']:.2%}")
            print(f"  Sharpe Ratio:  {m['sharpe']:.2f}")
            print(f"  Max Drawdown:  {m['maxdd']:.2%}")
            print(f"  Calmar Ratio:  {m['calmar']:.2f}")
            
            results_summary.append({
                "Strategy": name,
                "Annual Return": m['annual'],
                "Volatility": m['vol'],
                "Sharpe": m['sharpe'],
                "MaxDD": m['maxdd'],
                "Calmar": m['calmar']
            })
        except Exception as e:
            print(f"  Backtest failed: {str(e)}")
            
    print("\n" + "=" * 80)
    print("  Backtest Summary (2018-2026)")
    print("=" * 80)
    summary_df = pd.DataFrame(results_summary).set_index("Strategy")
    print(summary_df.to_string(formatters={
        "Annual Return": lambda x: f"{x:+.2%}",
        "Volatility": lambda x: f"{x:.2%}",
        "Sharpe": lambda x: f"{x:.2f}",
        "MaxDD": lambda x: f"{x:.2%}",
        "Calmar": lambda x: f"{x:.2f}"
    }))

if __name__ == "__main__":
    main()
