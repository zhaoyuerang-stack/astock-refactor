"""Research script: Testing Salience Theory (STR) within the Mid-and-Large-Cap (CSI 800) Universe.

This script:
1. Loads A-share daily prices and volumes.
2. Filters the active universe to the mid-and-large-cap segment (top 800 of 20-day rolling trading volume).
3. Computes the faded Salience Covariance (-ST_cov) and faded Expected Return (-E^ST[R]).
4. Runs backtests using the standard BacktestEngine, including cost models (leverage=1.0, no timing, long-only and long-short hedged).
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
    print("  Salience Theory (STR) in Mid-and-Large-Cap (CSI 800) Universe")
    print("=" * 80)
    
    # Load prices
    print("\n[1/3] Loading price panels...")
    close, volume, amount = load_price_panels("2010-01-01")
    prices = PricePanel(close=close, volume=volume, amount=amount)
    
    # Compute salience factors
    print("\n[2/3] Computing salience factors...")
    est_return, st_cov = compute_salience_factors(close, W=20, theta=0.1, delta=0.7)
    
    # Define universe: top 800 by 20-day rolling trading volume
    liq_rank = amount.rolling(20).mean().rank(axis=1, pct=False, ascending=False)
    large_univ = liq_rank <= 800
    
    factor_pool = {
        "Salience_Covariance": -st_cov,
        "Salience_Expected_Return": -est_return,
    }
    
    # Apply CSI 800 universe filter
    for name in factor_pool:
        factor_pool[name] = factor_pool[name].where(large_univ)
        
    print("\n[3/3] Running backtests...")
    STATS_START = "2018-01-01"
    cost = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.0)
    config = BacktestConfig(start=STATS_START, cost=cost, leverage=1.0)
    
    # Benchmark return: equal-weighted index of the CSI 800 universe
    daily_ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    bench_returns = pd.Series(0.0, index=daily_ret.index)
    univ_shifted = large_univ.shift(1)
    for dt in daily_ret.index:
        active = univ_shifted.loc[dt] if dt in univ_shifted.index else pd.Series(False, index=large_univ.columns)
        active = active.fillna(False).astype(bool)
        active_codes = active[active].index
        if len(active_codes) > 0:
            bench_returns.loc[dt] = daily_ret.loc[dt, active_codes].mean()
            
    results_summary = []
    for name, factor_df in factor_pool.items():
        print(f"\n--- Backtesting strategy: {name} ---")
        try:
            # Rebalance weights: pick top 25 stocks in the universe (lowest salience)
            z_factor = safe_zscore(mad_clip(factor_df))
            sched = build_rebalance_weights(z_factor, close, top_n=25, rebalance_days=20)
            
            engine = BacktestEngine(prices=prices, config=config)
            sig = Signal(weights=sched, timing=None, family="salience-large", version="v1.0")
            res = engine.run(sig)
            
            m = res.metrics
            
            # Compute hedged long-short performance
            common_idx = res.returns.index.intersection(bench_returns.index)
            r_long = res.returns.loc[common_idx]
            r_bench = bench_returns.loc[common_idx]
            r_hedged = r_long - r_bench - (0.015 / 252.0)  # subtract 1.5% annual hedging cost
            
            # Compute metrics for hedged returns
            h_annual = float(r_hedged.mean() * 252)
            h_vol = float(r_hedged.std() * np.sqrt(252))
            h_sharpe = h_annual / h_vol if h_vol > 0 else 0.0
            cum_h = (1 + r_hedged).cumprod()
            h_maxdd = float((cum_h / cum_h.cummax() - 1).min())
            
            print(f"  Long-Only Annual Return: {m['annual']:.2%}, Sharpe: {m['sharpe']:.2f}, MaxDD: {m['maxdd']:.2%}")
            print(f"  Hedged L-S  Annual Return: {h_annual:.2%}, Sharpe: {h_sharpe:.2f}, MaxDD: {h_maxdd:.2%}")
            
            results_summary.append({
                "Strategy": name,
                "Long Annual Return": m['annual'],
                "Long Sharpe": m['sharpe'],
                "Long MaxDD": m['maxdd'],
                "Hedged Annual Return": h_annual,
                "Hedged Sharpe": h_sharpe,
                "Hedged MaxDD": h_maxdd,
            })
        except Exception as e:
            print(f"  Backtest failed: {str(e)}")
            
    print("\n" + "=" * 80)
    print("  Backtest Summary (2018-2026)")
    print("=" * 80)
    summary_df = pd.DataFrame(results_summary).set_index("Strategy")
    print(summary_df.to_string(formatters={
        "Long Annual Return": lambda x: f"{x:+.2%}",
        "Long Sharpe": lambda x: f"{x:.2f}",
        "Long MaxDD": lambda x: f"{x:.2%}",
        "Hedged Annual Return": lambda x: f"{x:+.2%}",
        "Hedged Sharpe": lambda x: f"{x:.2f}",
        "Hedged MaxDD": lambda x: f"{x:.2%}",
    }))

if __name__ == "__main__":
    main()
