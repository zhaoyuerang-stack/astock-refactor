"""Salience Theory (STR) Factor Research and Backtesting.

This script implements two versions of the Salience Theory factor:
1. Expected Return under Salience Theory (est_return)
2. Covariance between salience weights and daily returns (st_cov)

It computes Rank IC for various forward horizons and backtests the long-only portfolio
formed by picking stocks with the lowest salience (salient downside, undervalued).
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
from engine.factor_analysis import calc_ic, ic_summary
from factors.utils import mad_clip, safe_zscore
from strategies.small_cap import build_rebalance_weights, load_price_panels


def compute_salience_factors(close, W=20, theta=0.1, delta=0.7):
    """Computes Salience Theory factors: est_return and st_cov.
    
    Parameters
    ----------
    close : pd.DataFrame
        Daily adjusted close price.
    W : int
        Rolling window size.
    theta : float
        Diminishing sensitivity parameter.
    delta : float
        Subjective probability distortion parameter.
    """
    print(f"Computing Salience Theory factors (W={W}, theta={theta}, delta={delta})...")
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
        
    # Denominator of subjective probability
    denom = delta * (1 - delta ** valid_count) / (1 - delta)
    
    # Subjective weights and expected returns
    est_return = pd.DataFrame(0.0, index=returns.index, columns=returns.columns)
    for s in range(W):
        weight_s = (delta ** ranks[s]) / denom
        r_lag = returns.shift(s)
        est_return += weight_s * r_lag.fillna(0.0)
        
    # Covariance version: ST_i = E^ST[R_i] - mean(R_i)
    avg_return = returns.rolling(W).mean()
    st_cov = est_return - avg_return
    
    # Filter/clean outputs
    est_return = est_return.where(returns.notna())
    st_cov = st_cov.where(returns.notna())
    
    return est_return, st_cov

def main():
    print("=" * 80)
    print("  Salience Theory (STR) Factor Research")
    print("=" * 80)
    
    # Load prices
    print("\n[1/4] Loading price panels...")
    close, volume, amount = load_price_panels("2010-01-01")
    print(f"  Loaded {close.shape[1]} stocks across {close.shape[0]} dates.")
    
    # Compute factors
    print("\n[2/4] Calculating Salience Theory factors...")
    est_return, st_cov = compute_salience_factors(close, W=20, theta=0.1, delta=0.7)
    
    # Define factors dictionary
    # Since we hypothesize that high salience stocks perform poorly,
    # we fade the factor (multiply by -1 or select direction=-1).
    factors = {
        "Faded_Expected_Return (-E^ST[R])": -est_return,
        "Faded_Salience_Covariance (-ST_cov)": -st_cov,
    }
    
    # Compute multi-period forward returns
    print("\n[3/4] Computing forward returns and Rank IC...")
    FORWARD_PERIODS = [1, 5, 10, 20]
    fwd_rets = {}
    for p in FORWARD_PERIODS:
        fwd_rets[p] = close.pct_change(p).shift(-p).replace([np.inf, -np.inf], np.nan)
        
    STATS_START = "2018-01-01"
    all_ic_results = []
    
    for name, factor_df in factors.items():
        factor_clean = factor_df.loc[STATS_START:].replace([np.inf, -np.inf], np.nan)
        row = {"factor": name}
        for p in FORWARD_PERIODS:
            fwd = fwd_rets[p].loc[STATS_START:]
            ic = calc_ic(factor_clean, fwd)
            if len(ic) < 100:
                row[f"IC_{p}d"] = np.nan
                row[f"ICIR_{p}d"] = np.nan
                continue
            s = ic_summary(ic)
            row[f"IC_{p}d"] = s["IC_mean"]
            row[f"ICIR_{p}d"] = s["ICIR"]
        all_ic_results.append(row)
        
    ic_df = pd.DataFrame(all_ic_results).set_index("factor")
    print("\nRank IC Summary:")
    print(ic_df[[f"IC_{p}d" for p in FORWARD_PERIODS]].to_string(float_format=lambda x: f"{x:+.4f}"))
    print("\nRank ICIR Summary:")
    print(ic_df[[f"ICIR_{p}d" for p in FORWARD_PERIODS]].to_string(float_format=lambda x: f"{x:+.3f}"))
    
    # Backtesting
    print("\n[4/4] Running backtests on standard engine...")
    prices = PricePanel(close=close, volume=volume, amount=amount)
    cost = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065)
    config = BacktestConfig(start=STATS_START, cost=cost, leverage=1.0)
    
    # We choose small-cap universe for the backtest, which is the baseline in astok.
    # In astok, universe is often defined by bottom amount rank (small-cap).
    # Let's see how the factor behaves on all stocks (top 25) vs small-cap.
    # Let's backtest both factors without any filters first.
    for name, factor_df in factors.items():
        print(f"\n--- Backtesting {name} ---")
        try:
            # We standardize the factor
            z_factor = safe_zscore(mad_clip(factor_df))
            
            # Target weights (Top 25 stocks with highest factor values, i.e., lowest salience)
            sched = build_rebalance_weights(z_factor, close, top_n=25, rebalance_days=20)
            
            engine = BacktestEngine(prices=prices, config=config)
            # Signal with family metadata
            sig = Signal(weights=sched, family="salience", version="v1.0")
            res = engine.run(sig)
            
            m = res.metrics
            print(f"  Annual Return: {m['annual']:.2%}")
            print(f"  Volatility:    {m['vol']:.2%}")
            print(f"  Sharpe Ratio:  {m['sharpe']:.2f}")
            print(f"  Max Drawdown:  {m['maxdd']:.2%}")
            print(f"  Calmar Ratio:  {m['calmar']:.2f}")
            
        except Exception as e:
            print(f"  Backtest failed: {str(e)}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    main()
