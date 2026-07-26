"""Regime 时间线分析 — 从924行情起, 分阶段追踪各因子Sharpe演化.

用法:
  cd /Users/kiki/astcok/factor_research
  /opt/homebrew/bin/python3 scripts/research/regime_timeline.py
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
from factors.small_cap import small_cap_timing
from strategies.small_cap import build_rebalance_weights, load_price_panels


def main():
    close, volume, amount = load_price_panels('2010-01-01')
    data = FactorData(close=close, volume=volume, amount=amount)
    band = ((1 + small_cap_timing(close, amount, 16)[2].shift(1) * 8).clip(0, 1.5)
            * (small_cap_timing(close, amount, 16)[2].shift(1) > 0)).fillna(0.0)
    prices = PricePanel(close=close, volume=None, amount=amount)
    cost = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065)

    factors = {
        "AmihudIlliq (当前小盘)": AmihudIlliq(window=20).mad_clip(5).zscore().shift(1),
        "Amihud SHORT (大盘)": AmihudIlliq(window=20).mad_clip(5).zscore().shift(1).neg(),
        "SizeProxy (旧小盘)": SizeProxy(window=60).mad_clip(5).zscore().shift(1),
    }

    monthly_data = {}
    for name, fexpr in factors.items():
        values = fexpr.compute(data)
        sched = build_rebalance_weights(values, close, top_n=25, rebalance_days=20)
        engine = BacktestEngine(prices=prices, config=BacktestConfig(start='2023-09-01', cost=cost, leverage=1.0))
        r = engine.run(Signal(weights=sched, timing=band, exposure_cap=1.5, family="x", version="")).returns.loc['2023-09-01':].dropna()
        ms = []
        for dt in pd.date_range('2024-09-30', '2026-06-30', freq='ME'):
            mask = (r.index <= dt) & (r.index > dt - pd.DateOffset(years=1))
            if mask.sum() < 150: continue
            rr = r[mask]; ann = float(rr.mean()*252); vol = float(rr.std()*np.sqrt(252))
            ms.append((dt.strftime('%Y-%m'), (ann-0.025)/vol if vol>0 else 0))
        monthly_data[name] = ms

    # 924之前基线
    for name, fexpr in factors.items():
        values = fexpr.compute(data)
        sched = build_rebalance_weights(values, close, top_n=25, rebalance_days=20)
        engine = BacktestEngine(prices=prices, config=BacktestConfig(start='2023-09-01', cost=cost, leverage=1.0))
        r = engine.run(Signal(weights=sched, timing=band, exposure_cap=1.5, family="x", version="")).returns.loc['2023-09-01':].dropna()
        r_pre = r[:'2024-09-23']
        if len(r_pre) > 100:
            ann = float(r_pre.mean()*252); vol = float(r_pre.std()*np.sqrt(252))
            print(f'{name} 924之前Sharpe: {(ann-0.025)/vol:+.2f}' if vol>0 else f'{name} 924之前: N/A')

    # 月度表格
    months = [m[0] for m in monthly_data[list(monthly_data.keys())[0]]]
    print('\n月度滚动Sharpe (12月窗口):')
    header = f'{"":<28}' + ''.join(f'{m:>8}' for m in months) + ' |  趋势'
    print(header)
    print('-' * len(header))
    for name, ms in monthly_data.items():
        vals = [m[1] for m in ms]
        row = f'{name:<28}' + ''.join(f' {v:>+7.2f}' for v in vals)
        delta = vals[-1] - vals[0]
        trend = '📈' if delta > 0.3 else ('📉' if delta < -0.3 else '→')
        print(f'{row} | {delta:+.2f} {trend}')

    # 分阶段
    stages = [
        ('924行情(9-12月)', '2024-09', '2024-12'),
        ('2025 Q1(1-3月)', '2025-01', '2025-03'),
        ('2025 Q2(4-6月)', '2025-04', '2025-06'),
        ('2025 Q3(7-9月)', '2025-07', '2025-09'),
        ('2025 Q4(10-12月)', '2025-10', '2025-12'),
        ('2026 Q1(1-3月)', '2026-01', '2026-03'),
        ('2026 Q2(4-6月)', '2026-04', '2026-06'),
    ]
    print('\n分阶段平均Sharpe:')
    print(f'{"阶段":<20} {"Amihud小盘":>12} {"大盘SHORT":>12} {"旧Size":>12}')
    for label, start, end in stages:
        print(f'{label:<20}', end='')
        for name in monthly_data:
            vals = [m[1] for m in monthly_data[name] if start <= m[0] <= end]
            print(f' {np.mean(vals):>+11.2f}' if vals else ' {"N/A":>12}', end='')
        print()

    print()


if __name__ == "__main__":
    main()
