"""amount 修复后 v2.0 干净口径重验:run_small_cap_strategy 三段 + 年度收益。
对比旧(污染)口径:样本内 20.5%/-17.4%/夏普1.14,压力 23.1%/-33.9%/夏普1.12。
用法(cwd=factor_research): /usr/bin/python3 -m scripts.research.reverify_v2
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from engine.metrics import metrics, yearly_returns
from strategies.small_cap import StrategyConfig, run_small_cap_strategy

print("=== v2.0 干净 amount 口径重验(不复权成交额)===")
for label, start in [("样本内 2018-2026", "2018-01-01"), ("压力 2010-2026", "2010-01-01")]:
    res = run_small_cap_strategy(StrategyConfig(start=start))
    m = metrics(res["returns"])
    print(f"[{label}] 年化 {m['annual']:+.1%} | 回撤 {m['maxdd']:+.1%} | "
          f"夏普 {m['sharpe']:.2f} | 卡玛 {m.get('calmar', 0):.2f}")

print("\n年度收益(2010起,看 2015/2025 极端年是否还在):")
res = run_small_cap_strategy(StrategyConfig(start="2010-01-01"))
for y, r in yearly_returns(res["returns"]).items():
    print(f"  {int(y)}: {r:+7.1%}" + ("   <- 极端" if abs(r) > 0.5 else ""))
