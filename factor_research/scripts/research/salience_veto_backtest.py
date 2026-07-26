"""Research script: Testing Salience Veto Filter on Small-Cap Strategy.

This script:
1. Loads prices, volume, and amount panels.
2. Computes the baseline small-cap factor (-log average amount) and the faded Salience Covariance (-ST_cov).
3. Uses the faded Salience Covariance as a Veto Filter (filtering out the 10% or 20% most salient/bubble stocks).
4. Runs backtests using the standard BacktestEngine (1.25x leverage, MA16 timing, cost model) to see if it improves the baseline strategy.
"""
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
os.chdir(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, str(Path.cwd()))

import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.small_cap import small_cap_factor, small_cap_timing
from strategies.small_cap import build_rebalance_weights, load_price_panels


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
    print("  Salience Veto Filter on Small-Cap Strategy")
    print("=" * 80)
    
    # Load prices
    print("\n[1/3] Loading price panels...")
    close, volume, amount = load_price_panels("2010-01-01")
    prices = PricePanel(close=close, volume=volume, amount=amount)
    
    # Compute timing signal
    timing, _, _ = small_cap_timing(close, amount, ma_window=16)
    
    # Compute factors
    print("\n[2/3] Computing factors...")
    faded_st_cov = compute_salience_covariance(close, W=20, theta=0.1, delta=0.7)
    size_factor = small_cap_factor(amount, window=60)
    
    # Standard configuration
    STATS_START = "2018-01-01"
    cost = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065)
    config = BacktestConfig(start=STATS_START, cost=cost, leverage=1.25)
    
    # Backtest configurations to compare:
    # 1. Baseline Small-Cap (No Veto)
    # 2. Small-Cap + Salience Veto (Veto worst 10% salient stocks)
    # 3. Small-Cap + Salience Veto (Veto worst 20% salient stocks)
    # 4. Small-Cap + Salience Veto (Veto worst 30% salient stocks)
    
    veto_thresholds = [0.0, 0.10, 0.20, 0.30]
    results_summary = []
    
    print("\n[3/3] Running backtests...")
    for v_q in veto_thresholds:
        if v_q == 0.0:
            name = "Baseline_No_Veto"
            print(f"\n--- Backtesting: {name} ---")
            sched = build_rebalance_weights(size_factor, close, top_n=25, rebalance_days=20)
        else:
            name = f"Salience_Veto_{int(v_q*100)}%"
            print(f"\n--- Backtesting: {name} ---")
            # build_rebalance_weights has parameters: veto_factor, veto_q
            # Since build_rebalance_weights filters out stocks where veto_factor < veto_factor.quantile(veto_q),
            # we pass faded_st_cov (where higher means lower salience, and lower means higher salience).
            # So the stocks we want to veto (highest salience) are the ones with lowest faded_st_cov values.
            # Thus, we filter out the bottom v_q quantile of faded_st_cov.
            sched = build_rebalance_weights(
                size_factor, 
                close, 
                top_n=25, 
                rebalance_days=20, 
                veto_factor=faded_st_cov, 
                veto_q=v_q
            )
            
        try:
            engine = BacktestEngine(prices=prices, config=config)
            sig = Signal(weights=sched, timing=timing, family="salience-veto", version=f"v{v_q}")
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
    print("  Backtest Summary: Small-Cap Veto Filter (2018-2026)")
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
