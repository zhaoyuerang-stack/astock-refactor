"""Research script: Size-and-Industry-Neutralized Salience Theory Factor.

This script:
1. Loads A-share daily prices, volumes, and Shenwan industry mappings.
2. Computes the faded Salience Covariance (-ST_cov) factor.
3. Creates daily industry groups and size (amount) deciles.
4. Performs double neutralization (industry-neutral + size-neutral) on the factor.
5. Evaluates the Rank IC of the neutralized factor.
6. Backtests the neutralized factor in the All-Stock universe and the CSI 800 universe.
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
from factors.alpha.transforms import neutralize
from factors.utils import mad_clip, safe_zscore
from strategies.small_cap import build_rebalance_weights, load_price_panels


def load_industry_groups():
    """Load latest industry mapping from fundamental parquet."""
    fund = pd.read_parquet("data_lake/fundamental_batch.parquet", columns=["code", "avail_date", "industry"])
    mapping = fund.dropna(subset=["industry"]).sort_values("avail_date").drop_duplicates("code", keep="last")
    stock_to_ind = dict(zip(mapping["code"], mapping["industry"], strict=True))
    return stock_to_ind

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
    print("  Size-and-Industry-Neutralized Salience Theory Factor")
    print("=" * 80)
    
    # Load prices
    print("\n[1/4] Loading price panels...")
    close, volume, amount = load_price_panels("2010-01-01")
    prices = PricePanel(close=close, volume=volume, amount=amount)
    
    # Compute salience covariance factor (faded)
    print("\n[2/4] Computing faded Salience Covariance (-ST_cov)...")
    _, st_cov = compute_salience_factors(close, W=20, theta=0.1, delta=0.7)
    raw_factor = -st_cov
    
    # Load industry mapping and create industry group DataFrame
    print("  Creating industry group panel...")
    stock_to_ind = load_industry_groups()
    industry_panel = pd.DataFrame("Unknown", index=close.index, columns=close.columns)
    for col in close.columns:
        if col in stock_to_ind:
            industry_panel[col] = stock_to_ind[col]
            
    # Create size deciles DataFrame
    print("  Creating size decile panel (based on 20-day rolling trading amount)...")
    avg_amount = amount.rolling(20).mean()
    # Rank daily and split into 10 deciles (0 to 9)
    size_ranks = avg_amount.rank(axis=1, pct=True)
    size_bins = (size_ranks * 10).fillna(0).astype(int)
    
    # Neutralization
    print("\n[3/4] Neutralizing factor...")
    # First against industry groups
    print("  Neutralizing against Shenwan industries...")
    ind_neut_factor = neutralize(raw_factor, industry_panel)
    # Then against size deciles
    print("  Neutralizing against size deciles...")
    neut_factor = neutralize(ind_neut_factor, size_bins)
    
    # Evaluate IC
    print("\n[4/4] Computing Rank IC of raw vs neutralized factor...")
    FORWARD_PERIODS = [1, 5, 10, 20]
    fwd_rets = {}
    for p in FORWARD_PERIODS:
        fwd_rets[p] = close.pct_change(p).shift(-p).replace([np.inf, -np.inf], np.nan)
        
    STATS_START = "2018-01-01"
    factors_to_eval = {
        "Raw Faded Salience Covariance": raw_factor,
        "Neutralized Faded Salience Covariance": neut_factor
    }
    
    all_ic_results = []
    for name, factor_df in factors_to_eval.items():
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
    print("\nRank IC Summary (2018-2026):")
    print(ic_df[[f"IC_{p}d" for p in FORWARD_PERIODS]].to_string(float_format=lambda x: f"{x:+.4f}"))
    print("\nRank ICIR Summary (2018-2026):")
    print(ic_df[[f"ICIR_{p}d" for p in FORWARD_PERIODS]].to_string(float_format=lambda x: f"{x:+.3f}"))
    
    # Running backtests
    cost = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065)
    config = BacktestConfig(start=STATS_START, cost=cost, leverage=1.0)
    
    # Define universes
    # CSI 800: top 800 by 20-day average amount
    liq_rank = amount.rolling(20).mean().rank(axis=1, pct=False, ascending=False)
    large_univ = liq_rank <= 800
    
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
            
    print("\nRunning backtests for Neutralized factor:")
    
    # 1. All-Stock Universe Backtest
    print("\n--- 1. All-Stock Universe (Long-Only, top 25) ---")
    try:
        z_factor = safe_zscore(mad_clip(neut_factor))
        sched = build_rebalance_weights(z_factor, close, top_n=25, rebalance_days=20)
        
        engine = BacktestEngine(prices=prices, config=config)
        sig = Signal(weights=sched, timing=None, family="salience-neut", version="v1.0")
        res = engine.run(sig)
        
        m = res.metrics
        print(f"  Annual Return: {m['annual']:.2%}")
        print(f"  Volatility:    {m['vol']:.2%}")
        print(f"  Sharpe Ratio:  {m['sharpe']:.2f}")
        print(f"  Max Drawdown:  {m['maxdd']:.2%}")
    except Exception as e:
        print(f"  Backtest failed: {str(e)}")
        
    # 2. CSI 800 Universe Backtest (Hedged)
    print("\n--- 2. CSI 800 Universe (Long top 25 + CSI 800 Hedged) ---")
    try:
        # Restrict neutralized factor to CSI 800
        large_neut_factor = neut_factor.where(large_univ)
        z_factor_large = safe_zscore(mad_clip(large_neut_factor))
        sched_large = build_rebalance_weights(z_factor_large, close, top_n=25, rebalance_days=20)
        
        engine = BacktestEngine(prices=prices, config=config)
        sig = Signal(weights=sched_large, timing=None, family="salience-neut-large", version="v1.0")
        res = engine.run(sig)
        
        m = res.metrics
        
        # Compute hedged performance (long-short)
        common_idx = res.returns.index.intersection(bench_returns.index)
        r_long = res.returns.loc[common_idx]
        r_bench = bench_returns.loc[common_idx]
        r_hedged = r_long - r_bench - (0.015 / 252.0)  # subtract 1.5% annual hedging cost
        
        h_annual = float(r_hedged.mean() * 252)
        h_vol = float(r_hedged.std() * np.sqrt(252))
        h_sharpe = h_annual / h_vol if h_vol > 0 else 0.0
        cum_h = (1 + r_hedged).cumprod()
        h_maxdd = float((cum_h / cum_h.cummax() - 1).min())
        
        print(f"  Long-Only Annual Return: {m['annual']:.2%}, Sharpe: {m['sharpe']:.2f}, MaxDD: {m['maxdd']:.2%}")
        print(f"  Hedged L-S  Annual Return: {h_annual:.2%}, Sharpe: {h_sharpe:.2f}, MaxDD: {h_maxdd:.2%}")
    except Exception as e:
        print(f"  Backtest failed: {str(e)}")

if __name__ == "__main__":
    main()
