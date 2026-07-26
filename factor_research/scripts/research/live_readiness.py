"""v2.0 每日实盘就绪卡:当日操作(latest_signal) + 容量校验 + 失效状态(读 decay_status.json)。
run_daily 出"买哪些",本卡补"能上多少钱 + 策略在不在衰减 + 综合建议"。
失效状态由 decay_monitor 周度更新写入 reports/decay_status.json;无则用上次已知值兜底。
用法(cwd=factor_research): /usr/bin/python3 -m scripts.research.live_readiness
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from strategies.small_cap import StrategyConfig, latest_decision, load_price_panels

sig = latest_decision(StrategyConfig(start="2010-01-01"))
close = sig["result"]["close"]
last = close.index[-1]
in_market = sig["in_market"]
holdings = sig["holdings"]
dist = sig["timing_dist"]

print("=" * 58)
print(f"  v2.0 实盘就绪卡   {last.date()}")
print("=" * 58)
print(f"  择时   : {'🟢 持仓' if in_market else '🔴 空仓观望'}   (小盘指数 vs MA16: {dist:+.2%})")

# ── 容量:当日持仓近 20 日均真实成交额 → 上限 ──
if in_market and holdings:
    # load_price_panels already returns canonical amount (share × raw CNY/share)
    _, _volume, real_amt = load_price_panels("2024-01-01")
    cols = [c for c in holdings if c in real_amt.columns]
    adv = real_amt.loc[real_amt.index <= last, cols].iloc[-20:].mean().dropna()
    cap10, cap5 = adv.median() * 0.10 * len(holdings), adv.median() * 0.05 * len(holdings)
    print(f"  持仓   : {len(holdings)} 只等权 | 持仓股近20日均成交额中位 {adv.median()/1e4:.0f}万")
    print(f"  容量   : 参与率≤10% → ~{cap10/1e4:.0f}万 | ≤5%(稳) → ~{cap5/1e4:.0f}万")
else:
    print("  容量   : (空仓,无需)")

# ── 失效状态(decay_status.json 是 scripts/ops/decay_monitor.py 写的多版本 schema:
#    {"strategies": [{"strategy": "family.version", "decayed", "rolling_3y_sharpe_latest",
#    "reasons", "action"}, ...]},不是旧版单策略 IC schema,按 small-cap-size.v2.0 取一条)──
dp = Path("reports/decay_status.json")
STRATEGY_NAME = "small-cap-size.v2.0"
if dp.exists():
    d = json.loads(dp.read_text())
    entry = next((s for s in d.get("strategies", []) if s.get("strategy") == STRATEGY_NAME), None)
    if entry is not None:
        warn = bool(entry.get("decayed"))
        sh = entry.get("rolling_3y_sharpe_latest")
        print(f"  失效   : {'🔴 衰减' if warn else '🟢 健康'}  (滚动3年夏普 {sh} | {entry.get('action', '')})  "
              f"更新 {d.get('generated_at', '')}")
        if entry.get("reasons"):
            print(f"           触发: {'; '.join(entry['reasons'])}")
    else:
        warn = True
        print(f"  失效   : ⚠️ decay_status.json 里没有 {STRATEGY_NAME},跑 decay_monitor 刷新")
else:
    warn = True
    print("  失效   : ⚠️ decay_status.json 缺失,跑 decay_monitor 刷新")

# ── 综合建议 ──
print("-" * 58)
if not in_market:
    print("  建议   : 空仓观望,不建仓")
elif warn:
    print("  建议   : ⚠️ 失效预警期 + 小盘容量有限 → 若实盘,半仓/小资金,盯紧 IC 与小盘动量,恶化即退")
else:
    print("  建议   : 正常持仓,在容量上限内分批建仓")
print("=" * 58)
