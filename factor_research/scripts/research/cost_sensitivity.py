"""交易成本敏感性 —— v2.0策略在不同换手成本+杠杆融资下的真实年化"""
import warnings; warnings.filterwarnings("ignore")
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
import sys

sys.path.insert(0, str(ROOT))
from core.engine import CostModel
from engine.metrics import metrics
from strategies.small_cap import StrategyConfig, run_small_cap_strategy

print("v2.0 (2018-2026) 交易成本敏感性\n")
print(f"{'买/卖成本':<17}{'年化':>9}{'回撤':>9}{'夏普':>7}{'成本拖累':>10}{'达标':>5}  说明")
print("-" * 70)
for buy, sell, desc in [
    (0.0010, 0.0015, "低冲击/大容量理想"),
    (0.00225, 0.00275, "默认: 佣金+印花税+小盘冲击"),
    (0.0035, 0.0040, "小盘冲击偏保守"),
    (0.0050, 0.0055, "拥挤/容量受限"),
    (0.0070, 0.0075, "极端冲击"),
]:
    config = StrategyConfig(start="2018-01-01", cost=CostModel(buy_cost=buy, sell_cost=sell))
    result = run_small_cap_strategy(config)
    strat = result["returns"]
    detail = result["detail"]
    m = metrics(strat)
    drag = detail["cost"].mean() * 252
    print(f"{buy:>5.2%}/{sell:<5.2%}   {m['annual']:>8.1%}{m['maxdd']:>9.1%}{m['sharpe']:>7.2f}"
          f"{drag:>9.2%}{'✅' if m['hit'] else '❌':>5}  {desc}")

print("\n注: 成本按实际买/卖换手扣除，并含1.25x杠杆融资成本(持仓日扣)")
