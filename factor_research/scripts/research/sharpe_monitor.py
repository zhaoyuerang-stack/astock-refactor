"""Sharpe 动量监控 — 检测因子夏普加速/减速, 预警策略切换时机.

对每个因子计算:
  1. 滚动12月Sharpe序列
  2. Sharpe变化率(6月 vs 前6月)
  3. Sharpe加速度(变化率的变化)

触发条件:
  - 当前策略Sharpe减速>20% → ⚠️ 关注
  - 其他因子Sharpe加速>30% → 📈 候选

用法:
  cd /Users/kiki/astcok/factor_research
  /opt/homebrew/bin/python3 scripts/research/sharpe_monitor.py
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
from factors.alpha.builtins.illiq import AmihudIlliq, SizeProxy
from factors.alpha.builtins.momentum import PriceMomentum
from factors.alpha.builtins.volatility import Volatility
from factors.small_cap import small_cap_timing
from strategies.small_cap import build_rebalance_weights, load_price_panels


def rolling_sharpe(returns, window=252):
    """滚动年化夏普."""
    roll_m = returns.rolling(window).mean() * 252
    roll_s = returns.rolling(window).std() * np.sqrt(252)
    return (roll_m - 0.025) / roll_s.replace(0, np.nan)


def sharpe_momentum(sharpe_series, lookback=126):
    """Sharpe 动量: 近 lookback 天 vs 前 lookback 天."""
    recent = sharpe_series.dropna().iloc[-lookback:].mean()
    prior = sharpe_series.dropna().iloc[-(lookback*2):-lookback].mean()
    if pd.isna(prior) or prior == 0:
        return 0.0
    return float((recent - prior) / abs(prior))


def main():
    print("=" * 80)
    print("  Sharpe 动量监控 — 因子加速/减速预警")
    print("=" * 80)

    print("\n[1/3] 加载数据...", flush=True)
    close, volume, amount = load_price_panels("2010-01-01")
    data = FactorData(close=close, volume=volume, amount=amount)
    band = ((1 + small_cap_timing(close, amount, 16)[2].shift(1) * 8).clip(0, 1.5)
            * (small_cap_timing(close, amount, 16)[2].shift(1) > 0)).fillna(0.0)
    prices = PricePanel(close=close, volume=None, amount=amount)
    cost = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065)

    # 候选因子池
    candidates = {
        "AmihudIlliq (当前v3.0)": AmihudIlliq(window=20).mad_clip(5).zscore().shift(1),
        "SizeProxy (旧v2.1)": SizeProxy(window=60).mad_clip(5).zscore().shift(1),
        "AmihudIlliq SHORT (大盘)": AmihudIlliq(window=20).mad_clip(5).zscore().shift(1).neg(),
        "PriceMomentum SHORT (反转)": PriceMomentum(window=120).mad_clip(5).zscore().shift(1).neg(),
        "Volatility SHORT (低波)": Volatility(window=20).mad_clip(5).zscore().shift(1).neg(),
    }

    print("[2/3] 计算滚动Sharpe...", flush=True)
    sharpe_data = {}
    for name, factor_expr in candidates.items():
        try:
            values = factor_expr.compute(data)
            sched = build_rebalance_weights(values, close, top_n=25, rebalance_days=20)
            engine = BacktestEngine(prices=prices, config=BacktestConfig(start="2018-01-01", cost=cost, leverage=1.0))
            r = engine.run(Signal(weights=sched, timing=band, exposure_cap=1.5, family="x", version="")).returns.loc["2018-01-01":].dropna()
            sh = rolling_sharpe(r)
            sharpe_data[name] = sh
            print(f"  {name:<35} 当前Sharpe={sh.dropna().iloc[-1]:+.2f}", flush=True)
        except Exception as e:
            print(f"  {name:<35} ERROR: {str(e)[:40]}", flush=True)

    print("\n[3/3] Sharpe动量分析\n")
    print(f"  {'因子':<35} {'当前Sharpe':>10} {'6月前':>8} {'变化率':>8} {'加速度':>8} {'信号':<15}")
    print("  " + "-" * 90)

    current_name = "AmihudIlliq (当前v3.0)"
    results = []

    for name, sh in sharpe_data.items():
        vals = sh.dropna()
        if len(vals) < 252:
            continue

        cur = float(vals.iloc[-1])
        prev_6m = float(vals.iloc[-126]) if len(vals) >= 126 else cur

        # 变化率: 当前 vs 6月前
        if prev_6m != 0:
            mom_6m = (cur - prev_6m) / abs(prev_6m)
        else:
            mom_6m = 0.0

        # 加速度: 近6月变化 vs 前6月变化
        recent_6m_mean = float(vals.iloc[-126:].mean())
        prior_6m_mean = float(vals.iloc[-252:-126].mean()) if len(vals) >= 252 else recent_6m_mean
        if abs(prior_6m_mean) > 0.01:
            accel = (recent_6m_mean - prior_6m_mean) / abs(prior_6m_mean)
        else:
            accel = 0.0

        # 信号判定
        if name == current_name:
            if mom_6m < -0.2:
                signal = "🔴 减速! 关注切换"
            elif mom_6m < -0.1:
                signal = "🟡 轻微减速"
            elif mom_6m > 0.1:
                signal = "🟢 加速中"
            else:
                signal = "→ 稳定"
        else:
            if mom_6m > 0.3:
                signal = "📈 候选!"
            elif mom_6m > 0.1:
                signal = "🟢 改善中"
            else:
                signal = "—"

        results.append({"name": name, "cur": cur, "prev": prev_6m, "mom": mom_6m, "accel": accel, "signal": signal})

        print(f"  {name:<35} {cur:>+9.2f} {prev_6m:>+8.2f} {mom_6m:>+7.0%} {accel:>+7.0%} {signal:<15}")

    # 总结
    print(f"\n{'='*80}")
    print("  总结")
    print(f"{'='*80}")

    cur_strategy = [r for r in results if "当前" in r["name"]]
    if cur_strategy:
        c = cur_strategy[0]
        print(f"  当前策略: {c['name']}")
        print(f"    Sharpe={c['cur']:+.2f}, 6月变化={c['mom']:+.0%}")
        if c['mom'] < -0.1:
            print("    ⚠️ Sharpe 在减速, 关注是否有替代因子在加速")

    accelerating = [r for r in results if "📈" in r["signal"]]
    if accelerating:
        print(f"\n  加速中的候选因子 ({len(accelerating)}个):")
        for a in accelerating:
            print(f"    📈 {a['name']}: Sharpe={a['cur']:+.2f}, 变化率={a['mom']:+.0%}")
    else:
        print("\n  暂无加速候选 — 当前策略仍是最优")

    print()


if __name__ == "__main__":
    main()
