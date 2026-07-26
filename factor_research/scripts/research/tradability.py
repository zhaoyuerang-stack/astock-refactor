"""v2.0 实盘可行性:① 容量(下单额 vs 个股成交额参与率) ② 可成交率(涨跌停/停牌买不进卖不出)。

近似口径:涨跌停用不复权收盘 pct ±9.8%(主板;创业板/科创板 ±20% 会被高估为受阻,偏保守),
停牌用 volume==0。换仓只统计相对上期的新进(买)/调出(卖)。
用法(cwd=factor_research): /usr/bin/python3 -m scripts.research.tradability
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from factors.small_cap import small_cap_factor
from lake.load_lake import load_raw_close  # noqa: E402
from strategies.small_cap import build_rebalance_weights, load_price_panels

close, volume, amount = load_price_panels("2018-01-01")
factor = small_cap_factor(amount, 60)
weights = build_rebalance_weights(factor, close, top_n=25, rebalance_days=20)
raw = load_raw_close(start="2018-01-01")
chg = raw.pct_change(fill_method=None)

dates = sorted(weights.keys())
print(f"=== v2.0 实盘可行性(2018-2026, {len(dates)} 个调仓日, top25 等权)===")

# ① 容量:持仓股当日成交额分布 + 不同规模参与率 ──────────────────
adv_vals = []
for d in dates:
    codes = weights[d].index
    if d in amount.index:
        adv_vals.extend(amount.loc[d, codes].dropna().values)
adv = pd.Series(adv_vals)
print(f"\n[① 容量] 持仓股日成交额: 中位 {adv.median()/1e8:.2f}亿 | "
      f"25分位 {adv.quantile(.25)/1e8:.2f}亿 | 5分位 {adv.quantile(.05)/1e8:.2f}亿")
print(f"{'组合规模':>9}{'单股下单':>9}{'参与率_中位':>12}{'参与率_90位':>12}")
for cap in [1e7, 3e7, 5e7, 1e8, 3e8, 1e9]:
    pr = (cap / 25) / adv
    print(f"{cap/1e8:>7.1f}亿{cap/25/1e4:>7.0f}万{pr.median():>12.1%}{pr.quantile(.9):>12.1%}")
print(f"→ 容量上限(持仓股参与率中位 ≤10%): ~{adv.median()*0.10*25/1e8:.2f}亿;"
      f"≤5%: ~{adv.median()*0.05*25/1e8:.2f}亿")

# ② 可成交率:换仓时涨跌停/停牌 ───────────────────────────────
bb = bt = sb = st = 0
prev = None
for d in dates:
    cur = set(weights[d].index)
    if prev is not None and d in chg.index:
        for c in cur - prev:           # 新进 = 要买
            bt += 1
            r = chg.loc[d, c] if c in chg.columns else np.nan
            v = volume.loc[d, c] if (d in volume.index and c in volume.columns) else np.nan
            if pd.isna(v) or v == 0 or (pd.notna(r) and r >= 0.098):
                bb += 1
        for c in prev - cur:           # 调出 = 要卖
            st += 1
            r = chg.loc[d, c] if c in chg.columns else np.nan
            v = volume.loc[d, c] if (d in volume.index and c in volume.columns) else np.nan
            if pd.isna(v) or v == 0 or (pd.notna(r) and r <= -0.098):
                sb += 1
    prev = cur
print("\n[② 可成交率] 近似(涨跌停±9.8% + 停牌volume=0):")
print(f"  买入 {bt} 笔,受阻(涨停/停牌) {bb} = {bb/max(bt,1):.1%} 买不进")
print(f"  卖出 {st} 笔,受阻(跌停/停牌) {sb} = {sb/max(st,1):.1%} 卖不出")
