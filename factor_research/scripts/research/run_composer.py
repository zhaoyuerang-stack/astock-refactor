"""Composer 实战 — 用已验证的腿做编排优化.

用法:
  cd /Users/kiki/astcok/factor_research
  /opt/homebrew/bin/python3 scripts/research/run_composer.py
"""
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
os.chdir(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, str(Path.cwd()))

import akshare as ak
import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.small_cap import small_cap_factor, small_cap_timing
from factory.regime import RegimeConfig, RegimeEngine
from factory.strategy_composer import LegSpec, StrategyComposer
from strategies.small_cap import build_rebalance_weights, load_price_panels


def main():
    print("=" * 80)
    print("  P4 Strategy Composer — 编排优化")
    print("=" * 80)

    # ── 数据 ──
    print("\n[1/3] 加载数据...", flush=True)
    close, volume, amount = load_price_panels("2010-01-01")
    bond_raw = ak.fund_etf_hist_sina(symbol="sh511010")
    bond_raw["date"] = pd.to_datetime(bond_raw["date"])
    bond_ret = bond_raw.set_index("date").sort_index()["close"].pct_change().dropna()

    illiq = small_cap_factor(amount, window=60).shift(1)
    w_long = build_rebalance_weights(illiq, close, top_n=25, rebalance_days=20)

    prices = PricePanel(close=close, volume=None, amount=amount)
    cost = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065)
    engine = BacktestEngine(prices=prices, config=BacktestConfig(start="2016-01-01", cost=cost, leverage=1.0))

    # 构建多窗口 illiq 变体 + Band timing 变体
    print("[2/3] 构建策略腿池...", flush=True)

    legs_bull = []
    legs_bear = []

    # illiq variants
    for w in [30, 60, 120]:
        f = small_cap_factor(amount, window=w).shift(1)
        ww = build_rebalance_weights(f, close, top_n=25, rebalance_days=20)
        # no timing (always in)
        r = engine.run(Signal(weights=ww, timing=pd.Series(1.0, index=close.index),
                       exposure_cap=1.0, family="x", version="")).returns.loc["2016-01-01":].dropna()
        legs_bull.append(LegSpec(f"illiq_w{w}_full", "bull", r, f"w={w}, no timing"))
        legs_bear.append(LegSpec(f"illiq_w{w}_full", "bear", r, f"w={w}, no timing"))

    # Band timed illiq
    for ma in [12, 16, 20]:
        _, _, dist = small_cap_timing(close, amount, ma_window=ma)
        band = ((1 + dist.shift(1) * 8).clip(0, 1.5) * (dist.shift(1) > 0)).fillna(0.0)
        r = engine.run(Signal(weights=w_long, timing=band, exposure_cap=1.5,
                       family="x", version="")).returns.loc["2016-01-01":].dropna()
        legs_bear.append(LegSpec(f"illiq_BandMA{ma}", "bear", r, f"Band MA={ma}"))

    # 债券排最前面(确保不被 cut)
    legs_bear.insert(0, LegSpec("bond_511010", "bear", bond_ret, "国债ETF"))
    # 现金
    legs_bear.append(LegSpec("cash", "bear", pd.Series(0.0, index=close.loc["2016-01-01":].index), "现金0%"))

    print(f"  Bull: {len(legs_bull)} 条, Bear: {len(legs_bear)} 条")

    # ── Composer ──
    print("\n[3/3] 编排优化...\n", flush=True)
    re = RegimeEngine(close, amount, RegimeConfig(trend_ma=16))
    composer = StrategyComposer(close, amount, regime_engine=re, start="2016-01-01")
    composer.add_legs("bull", legs_bull)
    composer.add_legs("bear", legs_bear)

    best = composer.optimize(top_n_per_regime=10, verbose=True)
    composer.report(top_n=10)

    # 输出策略定义
    print(f"\n{'='*80}")
    print("  最优策略定义")
    print(f"{'='*80}")
    print(best.to_json())

    # 对比基线
    _, _, dist = small_cap_timing(close, amount, ma_window=16)
    band_exp = ((1 + dist.shift(1) * 8).clip(0, 1.5) * (dist.shift(1) > 0)).fillna(0.0)
    r_base = engine.run(Signal(weights=w_long, timing=band_exp, exposure_cap=1.5,
                        family="x", version="")).returns.loc["2016-01-01":].dropna()
    base_ann = float(r_base.mean() * 252)
    base_dd = float(((1+r_base).cumprod()/(1+r_base).cumprod().cummax()-1).min())
    base_nav = (1+r_base).cumprod().iloc[-1] * 100

    print(f"\n  基线: ann={base_ann:+.1%}, mdd={base_dd:.1%}, nav={base_nav:.0f}万")
    print(f"  Composer: ann={best.metrics['annual']:+.1%}, mdd={best.metrics['maxdd']:.1%}, "
          f"nav={best.metrics['nav_100w']:.0f}万")
    print(f"  改善: ann +{best.metrics['annual']-base_ann:+.1%}, "
          f"nav +{best.metrics['nav_100w']-base_nav:.0f}万")

    print()


if __name__ == "__main__":
    main()
