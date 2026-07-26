"""Research script: Testing Salience Veto Filter on HQ Momentum (M4) and Large-Cap (M2) Strategies.

This script:
1. Loads data using load_clean_panels_with_growth().
2. Computes the faded Salience Covariance (-ST_cov).
3. Backtests the M4 (High-Quality Momentum) and M2 (Large-cap Growth) strategies with different Salience Veto levels.
4. Outputs comparison metrics for both strategies.
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
from factors.hq_momentum import build_hq_momentum_factor
from factors.large_cap import (
    build_large_cap_premium_factor,
    large_cap_timing_hysteresis,
    load_clean_panels_with_growth,
)
from strategies.small_cap import build_rebalance_weights


def compute_salience_covariance(close, W=20, theta=0.1, delta=0.7):
    """Computes faded Salience Covariance (-ST_cov)."""
    returns = close.pct_change(fill_method=None)
    market_returns = returns.mean(axis=1)
    
    r_diff = returns.sub(market_returns, axis=0).abs()
    r_sum = returns.abs().add(market_returns.abs(), axis=0) + theta
    salience = r_diff / r_sum
    
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

def run_hq_momentum_with_veto(panels, faded_st_cov, veto_q=0.0):
    """M4 Strategy with Salience Veto."""
    close = panels["close"]
    volume = panels["amount"] * 0.0
    amount = panels["amount"]
    raw_close = panels["raw_close"]
    prices = PricePanel(close=close, volume=volume, amount=amount, raw_close=raw_close)

    # Build factor
    comp_factor, univ = build_hq_momentum_factor(
        panels, universe_size=800, lookback=60, q_filter_threshold=0.60
    )

    # Scheduled weights with veto filter
    if veto_q == 0.0:
        scheduled = build_rebalance_weights(comp_factor, close, top_n=25, rebalance_days=20)
    else:
        scheduled = build_rebalance_weights(
            comp_factor, close, top_n=25, rebalance_days=20, veto_factor=faded_st_cov, veto_q=veto_q
        )
    
    # Engine backtest
    engine_config = BacktestConfig(
        start="2010-01-01",
        cost=CostModel(buy_cost=0.0, sell_cost=0.0, financing_rate=0.0),
        leverage=1.0,
    )
    engine = BacktestEngine(prices=prices, config=engine_config)
    res_long = engine.run(Signal(weights=scheduled))

    # Compute Universe Benchmark (CSI 800 equal-weighted)
    daily_ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    bench_returns = pd.Series(0.0, index=daily_ret.index)
    univ_shifted = univ.shift(1)
    for dt in daily_ret.index:
        active = univ_shifted.loc[dt] if dt in univ_shifted.index else pd.Series(False, index=univ.columns)
        active = active.fillna(False).astype(bool)
        active_codes = active[active].index
        if len(active_codes) > 0:
            bench_returns.loc[dt] = daily_ret.loc[dt, active_codes].mean()

    common_idx = res_long.returns.index.intersection(bench_returns.index)
    r_long = res_long.returns.loc[common_idx]
    r_bench = bench_returns.loc[common_idx]
    
    # Hedged long-short return
    r_neutral = r_long - r_bench - (0.015 / 252.0)
    
    # Slice to start date
    start_dt = pd.Timestamp("2012-01-01")
    r_neutral_sliced = r_neutral.loc[start_dt:]
    
    # Calculate metrics
    annual = float(r_neutral_sliced.mean() * 252)
    vol = float(r_neutral_sliced.std() * np.sqrt(252))
    sharpe = annual / vol if vol > 0 else 0.0
    cum = (1 + r_neutral_sliced).cumprod()
    maxdd = float((cum / cum.cummax() - 1).min())
    
    return {"annual": annual, "vol": vol, "sharpe": sharpe, "maxdd": maxdd}

def run_large_cap_with_veto(panels, faded_st_cov, veto_q=0.0):
    """M2 Strategy with Salience Veto."""
    close = panels["close"]
    volume = panels["amount"] * 0.0
    amount = panels["amount"]
    raw_close = panels["raw_close"]
    prices = PricePanel(close=close, volume=volume, amount=amount, raw_close=raw_close)

    # Build factor
    comp_premium, univ = build_large_cap_premium_factor(
        panels, universe_size=200, w_cpv_max=0.0
    )

    # Scheduled weights with veto filter
    if veto_q == 0.0:
        scheduled = build_rebalance_weights(comp_premium, close, top_n=25, rebalance_days=40)
    else:
        scheduled = build_rebalance_weights(
            comp_premium, close, top_n=25, rebalance_days=40, veto_factor=faded_st_cov, veto_q=veto_q
        )
    
    # Engine backtest
    engine_config = BacktestConfig(
        start="2010-01-01",
        cost=CostModel(buy_cost=0.0, sell_cost=0.0, financing_rate=0.0),
        leverage=1.0,
    )
    engine = BacktestEngine(prices=prices, config=engine_config)
    res_long = engine.run(Signal(weights=scheduled))

    # Compute Universe Benchmark (Top 200 equal-weighted)
    daily_ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    bench_returns = pd.Series(0.0, index=daily_ret.index)
    univ_shifted = univ.shift(1)
    for dt in daily_ret.index:
        active = univ_shifted.loc[dt] if dt in univ_shifted.index else pd.Series(False, index=univ.columns)
        active = active.fillna(False).astype(bool)
        active_codes = active[active].index
        if len(active_codes) > 0:
            bench_returns.loc[dt] = daily_ret.loc[dt, active_codes].mean()

    common_idx = res_long.returns.index.intersection(bench_returns.index)
    r_long = res_long.returns.loc[common_idx]
    r_bench = bench_returns.loc[common_idx]
    
    # Hedged long-short return
    r_neutral = r_long - r_bench - (0.015 / 252.0)
    
    # Hysteresis timing on neutral NAV
    nav_neutral = (1 + r_neutral).cumprod()
    timing_signal = large_cap_timing_hysteresis(nav_neutral, window=120, buffer=0.01)

    # Timed returns (including switch friction)
    transitions = timing_signal.diff().fillna(0.0) != 0.0
    r_timed = r_neutral * timing_signal - 0.0025 * transitions
    
    # Slice to start date
    start_dt = pd.Timestamp("2012-01-01")
    r_timed_sliced = r_timed.loc[start_dt:]
    
    # Calculate metrics
    annual = float(r_timed_sliced.mean() * 252)
    vol = float(r_timed_sliced.std() * np.sqrt(252))
    sharpe = annual / vol if vol > 0 else 0.0
    cum = (1 + r_timed_sliced).cumprod()
    maxdd = float((cum / cum.cummax() - 1).min())
    
    return {"annual": annual, "vol": vol, "sharpe": sharpe, "maxdd": maxdd}

def main():
    print("=" * 80)
    print("  Salience Veto Filter on HQ Momentum (M4) and Large-Cap (M2)")
    print("=" * 80)
    
    # Load data
    print("\n[1/3] Loading large-cap growth data panels...")
    panels = load_clean_panels_with_growth()
    close = panels["close"]
    
    # Compute salience covariance factor
    print("\n[2/3] Computing faded Salience Covariance (-ST_cov)...")
    faded_st_cov = compute_salience_covariance(close, W=20, theta=0.1, delta=0.7)
    
    veto_thresholds = [0.0, 0.10, 0.20, 0.30]
    
    # 1. Backtest M4 (High-Quality Momentum)
    print("\n[3/3] Backtesting M4 (High-Quality Momentum Hedged) 2012-2026...")
    m4_results = []
    for v_q in veto_thresholds:
        print(f"  Running M4 with veto_q = {v_q:.2f}...")
        res = run_hq_momentum_with_veto(panels, faded_st_cov, veto_q=v_q)
        m4_results.append({
            "Veto": "Baseline (No Veto)" if v_q == 0.0 else f"Veto {int(v_q*100)}%",
            "Annual Return": res["annual"],
            "Volatility": res["vol"],
            "Sharpe": res["sharpe"],
            "MaxDD": res["maxdd"]
        })
        
    # 2. Backtest M2 (Large-Cap Growth)
    print("\n  Backtesting M2 (Large-cap Growth Hedged + Timing) 2012-2026...")
    m2_results = []
    for v_q in veto_thresholds:
        print(f"  Running M2 with veto_q = {v_q:.2f}...")
        res = run_large_cap_with_veto(panels, faded_st_cov, veto_q=v_q)
        m2_results.append({
            "Veto": "Baseline (No Veto)" if v_q == 0.0 else f"Veto {int(v_q*100)}%",
            "Annual Return": res["annual"],
            "Volatility": res["vol"],
            "Sharpe": res["sharpe"],
            "MaxDD": res["maxdd"]
        })
        
    print("\n" + "=" * 80)
    print("  M4: High-Quality Momentum Hedged Summary (2012-2026)")
    print("=" * 80)
    m4_df = pd.DataFrame(m4_results).set_index("Veto")
    print(m4_df.to_string(formatters={
        "Annual Return": lambda x: f"{x:+.2%}",
        "Volatility": lambda x: f"{x:.2%}",
        "Sharpe": lambda x: f"{x:.2f}",
        "MaxDD": lambda x: f"{x:.2%}",
    }))
    
    print("\n" + "=" * 80)
    print("  M2: Large-cap Growth Hedged + Timing Summary (2012-2026)")
    print("=" * 80)
    m2_df = pd.DataFrame(m2_results).set_index("Veto")
    print(m2_df.to_string(formatters={
        "Annual Return": lambda x: f"{x:+.2%}",
        "Volatility": lambda x: f"{x:.2%}",
        "Sharpe": lambda x: f"{x:.2f}",
        "MaxDD": lambda x: f"{x:.2%}",
    }))

if __name__ == "__main__":
    main()
