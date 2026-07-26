"""生成因子健康报告 — 供 scheduled_daily_update 调用, 供 paper_trade 卡片读取.

输出: reports/factor_health.json

用法:
  cd /Users/kiki/astcok/factor_research
  /opt/homebrew/bin/python3 scripts/ops/generate_factor_health.py
"""
import warnings; warnings.filterwarnings('ignore')
import os; import json; import sys; os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.getcwd())
from pathlib import Path

import numpy as np

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.alpha import transforms  # noqa: F401 —— 副作用注册 DSL 变换(zscore/mad_clip/shift 等)
from factors.alpha.base import FactorData
from factors.alpha.builtins.illiq import AmihudIlliq, SizeProxy
from factors.small_cap import small_cap_timing
from strategies.small_cap import build_rebalance_weights, load_price_panels

close, volume, amount = load_price_panels('2010-01-01')
data = FactorData(close=close, volume=volume, amount=amount)
band = ((1 + small_cap_timing(close, amount, 16)[2].shift(1) * 8).clip(0, 1.5)
        * (small_cap_timing(close, amount, 16)[2].shift(1) > 0)).fillna(0.0)
prices = PricePanel(close=close, volume=None, amount=amount)
cost = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065)

factors = {
    "AmihudIlliq (当前)": AmihudIlliq(window=20).mad_clip(5).zscore().shift(1),
    "Amihud SHORT (大盘)": AmihudIlliq(window=20).mad_clip(5).zscore().shift(1).neg(),
    "SizeProxy (已退役)": SizeProxy(window=60).mad_clip(5).zscore().shift(1),
}

Path("reports").mkdir(exist_ok=True)
report = {"updated": str(close.index[-1].date())}
for name, fexpr in factors.items():
    values = fexpr.compute(data)
    sched = build_rebalance_weights(values, close, top_n=25, rebalance_days=20)
    engine = BacktestEngine(prices=prices, config=BacktestConfig(start='2018-01-01', cost=cost, leverage=1.0))
    r = engine.run(Signal(weights=sched, timing=band, exposure_cap=1.5, family="x", version="")).returns.loc['2018-01-01':].dropna()
    roll = r.rolling(252).mean()*252 / (r.rolling(252).std()*np.sqrt(252))
    vals = roll.dropna()
    cur = float(vals.iloc[-1])
    prev = float(vals.iloc[-126]) if len(vals) >= 126 else cur
    mom = (cur - prev) / abs(prev) if abs(prev) > 0.01 else 0.0
    q_vals = vals.iloc[-63:]
    q_trend = "加速" if len(q_vals)>10 and q_vals.iloc[-1] > q_vals.iloc[0] else "减速"
    report[name] = {"sharpe": round(cur, 2), "momentum_6m": round(mom*100, 1), "trend": q_trend}

Path("reports/factor_health.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
print("factor_health OK")
