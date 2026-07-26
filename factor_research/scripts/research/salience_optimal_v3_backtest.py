"""Research script: Testing Salience Veto Filter on the Optimal Production Strategy (AmihudIlliq v3.0).

This strategy combines:
- AmihudIlliq w20 stock selection (Top 25).
- PureTrend MA16 timing (Binary 1.25x leverage or Band dynamic leverage 0-1.5x).
- 511010 国债ETF bond rotation (allocating cash to bond ETF in bear regimes).

We evaluate the impact of Salience Veto Filter (No Veto, 10% Veto, 20% Veto, 30% Veto) on both Binary and Band versions.
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
from factors.alpha import transforms  # noqa: F401 —— 副作用注册 DSL 变换(zscore/mad_clip/shift 等)
from factors.alpha.base import FactorData
from factors.alpha.builtins.illiq import AmihudIlliq
from factors.small_cap import small_cap_timing
from strategies.small_cap import build_rebalance_weights, load_price_panels


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

def load_bond_returns(code="511010"):
    """Loads Gov Bond ETF daily returns."""
    df = pd.read_parquet(f"data_lake/cross_asset/etf/{code}.parquet")
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")
    return df["close"].pct_change(fill_method=None).dropna()

def run_v3_backtest(close, volume, amount, prices, bond_ret, factor, faded_st_cov, timing_raw, timing_dist, veto_q=0.0, mode="binary"):
    """Runs AmihudIlliq v3.0 backtest with Veto Filter and Bond Rotation."""
    # Build weights
    if veto_q == 0.0:
        scheduled = build_rebalance_weights(factor, close, top_n=25, rebalance_days=20)
    else:
        scheduled = build_rebalance_weights(
            factor, close, top_n=25, rebalance_days=20, veto_factor=faded_st_cov, veto_q=veto_q
        )
        
    # Cost and Backtest configuration
    STATS_START = "2018-01-01"
    cost = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065)
    
    if mode == "binary":
        config = BacktestConfig(start=STATS_START, cost=cost, leverage=1.25)
        engine = BacktestEngine(prices=prices, config=config)
        res_stock = engine.run(Signal(weights=scheduled, timing=timing_raw))
    else:
        # Band timing: dynamic exposure 0~1.5x based on timing_dist
        band_exposure = ((1 + timing_dist.shift(1) * 8).clip(0, 1.5) * (timing_dist.shift(1) > 0)).fillna(0.0)
        config = BacktestConfig(start=STATS_START, cost=cost, leverage=1.0) # Leverage is handled by exposure
        engine = BacktestEngine(prices=prices, config=config)
        res_stock = engine.run(Signal(weights=scheduled, timing=band_exposure, exposure_cap=1.5))
        
    r_stock = res_stock.returns
    
    # 511010 Bond Rotation: bull -> stock, bear -> bond
    # bear is defined as timing_dist.shift(1) <= 0
    dist_lagged = timing_dist.shift(1)
    common = r_stock.index.intersection(bond_ret.index).intersection(dist_lagged.dropna().index)
    
    r_stock_aligned = r_stock.reindex(common).fillna(0.0)
    bond_ret_aligned = bond_ret.reindex(common).fillna(0.0)
    bull_mask = dist_lagged.reindex(common) > 0
    
    r_rotation = pd.Series(np.where(bull_mask, r_stock_aligned, bond_ret_aligned), index=common)
    
    # Slice to Stats Start
    start_dt = pd.Timestamp(STATS_START)
    r_sliced = r_rotation.loc[start_dt:]
    
    # Metrics
    annual = float(r_sliced.mean() * 252)
    vol = float(r_sliced.std() * np.sqrt(252))
    sharpe = annual / vol if vol > 0 else 0.0
    cum = (1 + r_sliced).cumprod()
    maxdd = float((cum / cum.cummax() - 1).min())
    
    return {"annual": annual, "vol": vol, "sharpe": sharpe, "maxdd": maxdd}

def main():
    print("=" * 80)
    print("  Salience Veto Filter on Optimal Strategy (AmihudIlliq v3.0)")
    print("=" * 80)
    
    # Load prices and bonds
    print("\n[1/3] Loading price panels and Gov Bond ETF returns...")
    close, volume, amount = load_price_panels("2010-01-01")
    prices = PricePanel(close=close, volume=volume, amount=amount)
    bond_ret = load_bond_returns("511010")
    
    # Compute timing
    timing_raw, _, timing_dist = small_cap_timing(close, amount, ma_window=16)
    
    # Compute factors
    print("\n[2/3] Computing factors...")
    factor = AmihudIlliq(window=20).mad_clip(5).zscore().compute(FactorData(close=close, volume=volume, amount=amount))
    faded_st_cov = compute_salience_covariance(close, W=20, theta=0.1, delta=0.7)
    
    veto_thresholds = [0.0, 0.10, 0.20, 0.30]
    
    print("\n[3/3] Running backtests...")
    
    # 1. Binary timing + Bond Rotation
    print("\n--- 1. AmihudIlliq v3.0 (Binary Timing 1.25x + Bond Rotation) ---")
    binary_results = []
    for v_q in veto_thresholds:
        res = run_v3_backtest(close, volume, amount, prices, bond_ret, factor, faded_st_cov, timing_raw, timing_dist, veto_q=v_q, mode="binary")
        binary_results.append({
            "Veto": "Baseline (No Veto)" if v_q == 0.0 else f"Veto {int(v_q*100)}%",
            "Annual Return": res["annual"],
            "Volatility": res["vol"],
            "Sharpe": res["sharpe"],
            "MaxDD": res["maxdd"]
        })
        
    # 2. Band timing + Bond Rotation
    print("\n--- 2. AmihudIlliq v3.0 (Band Timing Dynamic + Bond Rotation) ---")
    band_results = []
    for v_q in veto_thresholds:
        res = run_v3_backtest(close, volume, amount, prices, bond_ret, factor, faded_st_cov, timing_raw, timing_dist, veto_q=v_q, mode="band")
        band_results.append({
            "Veto": "Baseline (No Veto)" if v_q == 0.0 else f"Veto {int(v_q*100)}%",
            "Annual Return": res["annual"],
            "Volatility": res["vol"],
            "Sharpe": res["sharpe"],
            "MaxDD": res["maxdd"]
        })
        
    print("\n" + "=" * 80)
    print("  AmihudIlliq v3.0 (Binary Timing + Bond Rotation) Summary (2018-2026)")
    print("=" * 80)
    bin_df = pd.DataFrame(binary_results).set_index("Veto")
    print(bin_df.to_string(formatters={
        "Annual Return": lambda x: f"{x:+.2%}",
        "Volatility": lambda x: f"{x:.2%}",
        "Sharpe": lambda x: f"{x:.2f}",
        "MaxDD": lambda x: f"{x:.2%}",
    }))
    
    print("\n" + "=" * 80)
    print("  AmihudIlliq v3.0 (Band Timing + Bond Rotation) Summary (2018-2026)")
    print("=" * 80)
    band_df = pd.DataFrame(band_results).set_index("Veto")
    print(band_df.to_string(formatters={
        "Annual Return": lambda x: f"{x:+.2%}",
        "Volatility": lambda x: f"{x:.2%}",
        "Sharpe": lambda x: f"{x:.2f}",
        "MaxDD": lambda x: f"{x:.2%}",
    }))

if __name__ == "__main__":
    main()
