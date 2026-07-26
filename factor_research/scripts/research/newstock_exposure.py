"""v2.0 持仓的新股暴露:first_valid_index ≈ 上市日,统计持仓里上市头 N 天的新股占比。

新股首日无涨跌停 + 成交额小 → amount 小 → 易被 small_cap_factor 选中;若回测在
新上市不久就买入,可能虚高且实盘买不到。从 2010 起加载以准确定位 2018 后上市股。
用法(cwd=factor_research): /usr/bin/python3 -m scripts.research.newstock_exposure
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from factors.small_cap import small_cap_factor
from strategies.small_cap import build_rebalance_weights, load_price_panels

close, volume, amount = load_price_panels("2010-01-01")
ipo = close.apply(lambda s: s.first_valid_index())          # 每股首个有效日 ≈ 上市日
factor = small_cap_factor(amount, 60)
weights = build_rebalance_weights(factor, close, 25, 20)
dates = [d for d in sorted(weights) if d.year >= 2018]

buckets = {"首月<30d": 0, "首季30-90d": 0, "半年90-180d": 0, "首年180-365d": 0, ">1年": 0}
total = 0
young = []
for d in dates:
    for c in weights[d].index:
        ipo_d = ipo.get(c)
        if ipo_d is None:
            continue
        days = (d - ipo_d).days
        total += 1
        if days < 30:
            buckets["首月<30d"] += 1
        elif days < 90:
            buckets["首季30-90d"] += 1
        elif days < 180:
            buckets["半年90-180d"] += 1
        elif days < 365:
            buckets["首年180-365d"] += 1
        else:
            buckets[">1年"] += 1
        if days < 90:
            young.append((str(d.date()), c, days))

print(f"=== v2.0 持仓新股暴露 (2018-2026, {len(dates)} 调仓 × 25 = {total} 持仓位) ===")
for k, v in buckets.items():
    print(f"  {k:<14}: {v:5d}  {v / total:6.1%}")
young_n = total - buckets[">1年"]
print(f"\n上市<1年 合计: {young_n} = {young_n / total:.1%}")
print(f"上市<90天就被选中的例子(前12): {young[:12]}")
