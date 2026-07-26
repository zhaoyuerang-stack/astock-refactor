"""top_n 敏感性实验: 寻找最优持仓数量.

测试 top_n = 10/15/20/25/30/40/50/60/80/100/120
固定: illiquidity (amount w=60), Band timing, 20日调仓, 真实成本.

用法:
  cd /Users/kiki/astcok/factor_research
  /opt/homebrew/bin/python3 scripts/research/top_n_sensitivity.py
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
from factors.small_cap import small_cap_factor, small_cap_timing
from strategies.small_cap import build_rebalance_weights, load_price_panels

STATS_START = "2018-01-01"
INITIAL_CAPITAL = 1_000_000
TOP_N_LIST = [10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 120]


def run_scenario(close, amount, top_n, leverage, timing_mode="band"):
    """Run a single backtest scenario."""
    factor = small_cap_factor(amount, window=60)
    scheduled = build_rebalance_weights(factor, close, top_n=top_n, rebalance_days=20)

    pt_timing, _, timing_dist = small_cap_timing(close, amount, ma_window=16)
    pt_timing = pt_timing.astype(float)

    if timing_mode == "band":
        dist_shifted = timing_dist.shift(1).reindex(pt_timing.index)
        exposure = ((1 + dist_shifted * 8).clip(0, 1.5) * (dist_shifted > 0).astype(float)).fillna(0.0)
        exp_cap = 1.5
        lev = 1.0  # Band uses exposure as leverage
    else:
        exposure = pt_timing
        exp_cap = 1.0
        lev = leverage

    prices = PricePanel(close=close, volume=None, amount=amount)
    cfg = BacktestConfig(
        start=STATS_START,
        cost=CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065),
        leverage=lev,
    )
    engine = BacktestEngine(prices=prices, config=cfg)
    signal = Signal(
        weights=scheduled, timing=exposure, exposure_cap=exp_cap,
        family="illiquidity", version="v1.0",
    )
    result = engine.run(signal)

    r = result.returns.loc[STATS_START:].dropna()
    if len(r) < 100:
        return None

    annual = float(r.mean() * 252)
    vol = float(r.std() * np.sqrt(252))
    sharpe = (annual - 0.025) / vol if vol > 0 else 0.0
    cum = (1 + r).cumprod()
    maxdd = float((cum / cum.cummax() - 1).min())
    calmar = annual / abs(maxdd) if maxdd < 0 else 0.0
    nav_final = cum.iloc[-1] * INITIAL_CAPITAL
    turnover = result.detail["turnover"].loc[STATS_START:].mean() * 252
    cost_drag = result.detail["cost"].loc[STATS_START:].mean() * 252
    monthly = r.resample("ME").apply(lambda g: (1 + g).prod() - 1)
    monthly_win = float((monthly > 0).mean())

    return {
        "top_n": top_n, "timing": timing_mode, "leverage": lev,
        "annual": annual, "vol": vol, "sharpe": sharpe,
        "maxdd": maxdd, "calmar": calmar, "nav_final": nav_final,
        "turnover": turnover, "cost_drag": cost_drag, "monthly_win": monthly_win,
        "n_days": len(r),
    }


def main():
    print("=" * 90)
    print("  top_n 敏感性实验: 寻找最优持仓数量")
    print("=" * 90)

    print("\n[1/2] 加载数据...", flush=True)
    close, volume, amount = load_price_panels("2010-01-01")
    print(f"  {close.shape[1]}只 x {close.shape[0]}日 [{close.index[0].date()} ~ {close.index[-1].date()}]")

    print(f"\n[2/2] 跑 {len(TOP_N_LIST)} 个场景...", flush=True)
    results = []
    for top_n in TOP_N_LIST:
        for mode in ["band", "binary"]:
            lev = 1.0 if mode == "band" else 1.25
            bt = run_scenario(close, amount, top_n, lev, timing_mode=mode)
            if bt:
                results.append(bt)
                print(f"  top_n={top_n:3d} {mode:6s}  年化={bt['annual']:+.2%} 回撤={bt['maxdd']:.2%} "
                      f"夏普={bt['sharpe']:.2f} 卡玛={bt['calmar']:.2f} "
                      f"终值={bt['nav_final']/1e4:.0f}万 换手={bt['turnover']:.1f}x")

    df = pd.DataFrame(results)

    print(f"\n{'='*90}")
    print("  结果汇总")
    print(f"{'='*90}")

    # Band timing results
    band_df = df[df["timing"] == "band"].sort_values("top_n")
    print("\n  Band timing (动态杠杆 0~1.5):")
    print(f"  {'top_n':>5} {'年化':>8} {'回撤':>8} {'夏普':>6} {'卡玛':>6} {'终值(万)':>9} {'换手':>7} {'成本':>7}")
    print("  " + "─" * 70)
    for _, r in band_df.iterrows():
        print(f"  {r['top_n']:5d} {r['annual']:>+7.1%} {r['maxdd']:>7.1%} {r['sharpe']:>5.2f} {r['calmar']:>5.2f} "
              f"{r['nav_final']/1e4:>8.0f}万 {r['turnover']:>6.1f}x {r['cost_drag']:>6.1%}")

    # Binary timing results
    binary_df = df[df["timing"] == "binary"].sort_values("top_n")
    print("\n  Binary timing (固定 1.25x 杠杆):")
    print(f"  {'top_n':>5} {'年化':>8} {'回撤':>8} {'夏普':>6} {'卡玛':>6} {'终值(万)':>9} {'换手':>7} {'成本':>7}")
    print("  " + "─" * 70)
    for _, r in binary_df.iterrows():
        print(f"  {r['top_n']:5d} {r['annual']:>+7.1%} {r['maxdd']:>7.1%} {r['sharpe']:>5.2f} {r['calmar']:>5.2f} "
              f"{r['nav_final']/1e4:>8.0f}万 {r['turnover']:>6.1f}x {r['cost_drag']:>6.1%}")

    # Best by metric
    print(f"\n{'='*90}")
    print("  最优 top_n (Band timing)")
    print(f"{'='*90}")
    best_sharpe = band_df.loc[band_df["sharpe"].idxmax()]
    best_calmar = band_df.loc[band_df["calmar"].idxmax()]
    best_annual = band_df.loc[band_df["annual"].idxmax()]
    best_nav = band_df.loc[band_df["nav_final"].idxmax()]

    print(f"\n  最高夏普:    top_n={best_sharpe['top_n']}  夏普={best_sharpe['sharpe']:.2f}  "
          f"年化={best_sharpe['annual']:+.1%} 回撤={best_sharpe['maxdd']:.1%}")
    print(f"  最高卡玛:    top_n={best_calmar['top_n']}  卡玛={best_calmar['calmar']:.2f}  "
          f"年化={best_calmar['annual']:+.1%} 回撤={best_calmar['maxdd']:.1%}")
    print(f"  最高年化:    top_n={best_annual['top_n']}  年化={best_annual['annual']:+.1%}  "
          f"夏普={best_annual['sharpe']:.2f} 回撤={best_annual['maxdd']:.1%}")
    print(f"  最高终值:    top_n={best_nav['top_n']}  终值={best_nav['nav_final']/1e4:.0f}万  "
          f"夏普={best_nav['sharpe']:.2f} 卡玛={best_nav['calmar']:.2f}")

    print()


if __name__ == "__main__":
    main()
