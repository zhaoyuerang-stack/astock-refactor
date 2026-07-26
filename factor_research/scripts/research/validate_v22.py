"""
v2.2 策略回测验证
==================
运行方式：
  cd /Users/kiki/astcok/factor_research
  /usr/bin/python3 scripts/research/validate_v22.py

输出：
  - 终端：逐年对比表 + 三段汇总
  - 文件：reports/research/validate_v22.json
"""
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

from app_config.settings import get_settings
from factors.small_cap import small_cap_factor, small_cap_timing
from strategies.small_cap import (
    StrategyConfig,
    backtest_weights,
    build_rebalance_weights,
    load_price_panels,
)

# ── 参数 ──────────────────────────────────────────────────────────────
WARMUP_START = "2010-01-01"
SEGMENTS = [
    ("IS  2018-2026", "2018-01-01", "2026-12-31"),
    ("OOS 2023-2026", "2023-01-01", "2026-12-31"),
    ("压力 2010-2026", "2010-01-01", "2026-12-31"),
]

# ── 数据加载 ──────────────────────────────────────────────────────────
_cfg = get_settings().strategy
print("加载数据...", flush=True)
close, volume, amount = load_price_panels(WARMUP_START)
factor   = small_cap_factor(amount, _cfg.size_window)
timing, _, _ = small_cap_timing(close, amount, _cfg.timing_ma)
scheduled = build_rebalance_weights(factor, close, _cfg.top_n, _cfg.rebalance_days)
cfg = StrategyConfig(start=WARMUP_START)

# ── 回测 ──────────────────────────────────────────────────────────────
print("回测 v2.0 (MA16)...", flush=True)
ret_v20, _ = backtest_weights(close, scheduled, timing, cfg)

print("回测 v2.2 (MA16 × PureTrend tw=2)...", flush=True)
mkt = (close.pct_change(fill_method=None)
       .replace([float("inf"), float("-inf")], float("nan"))
       .mean(axis=1).fillna(0.0))
exposure = (mkt.rolling(2).sum() >= 0).shift(1, fill_value=True).astype(float)  # shift(1): T日仓位用T-1日信号
ret_v22, _ = backtest_weights(close, scheduled, timing.astype(float) * exposure, cfg)

# ── 指标函数 ──────────────────────────────────────────────────────────
def metrics(ret, start=None, end=None):
    r = ret.copy()
    if start: r = r[r.index >= start]
    if end:   r = r[r.index <= end]
    r = r.fillna(0)
    if len(r) == 0:
        return {}
    nav    = (1 + r).cumprod()
    n_yr   = len(r) / 252
    ann    = nav.iloc[-1] ** (1 / n_yr) - 1
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0
    maxdd  = float((nav / nav.cummax() - 1).min())
    calmar = ann / abs(maxdd) if maxdd != 0 else 0.0
    return {
        "annual":  round(float(ann),    4),
        "sharpe":  round(float(sharpe), 4),
        "maxdd":   round(float(maxdd),  4),
        "calmar":  round(float(calmar), 4),
    }

# ── 逐年表 ────────────────────────────────────────────────────────────
years = sorted(set(ret_v20.index.year) | set(ret_v22.index.year))
yearly = []
for y in years:
    s, e = f"{y}-01-01", f"{y}-12-31"
    b = metrics(ret_v20, s, e)
    p = metrics(ret_v22, s, e)
    if not b or not p:
        continue
    yearly.append({"year": y, "v20": b, "v22": p, "pt2_beats": p["annual"] > b["annual"]})

# ── 三段汇总 ──────────────────────────────────────────────────────────
segments = []
for label, s, e in SEGMENTS:
    b = metrics(ret_v20, s, e)
    p = metrics(ret_v22, s, e)
    segments.append({"segment": label, "v20": b, "v22": p})

# ── 仓位统计 ──────────────────────────────────────────────────────────
mask18 = close.index >= "2018-01-01"
exposure_v20 = round(float(timing.astype(float)[mask18].mean()), 4)
exposure_v22 = round(float((timing.astype(float) * exposure)[mask18].mean()), 4)

# ── 终端输出 ──────────────────────────────────────────────────────────
W = 76
print()
print("=" * W)
print("  v2.0 vs v2.2 逐年对比")
print("=" * W)
hdr = f"  {'年份':<6}  {'v2.0 年化':>9} {'夏普':>7} {'回撤':>8}    {'v2.2 年化':>9} {'夏普':>7} {'回撤':>8}  {'':>4}"
print(hdr)
print("  " + "-" * (W - 2))
for row in yearly:
    b, p = row["v20"], row["v22"]
    flag = "  ←" if not row["pt2_beats"] else ""
    print(f"  {row['year']:<6}  {b['annual']:>+8.1%}  {b['sharpe']:>6.2f}  {b['maxdd']:>7.1%}"
          f"    {p['annual']:>+8.1%}  {p['sharpe']:>6.2f}  {p['maxdd']:>7.1%}  {flag}")

win_rate = sum(r["pt2_beats"] for r in yearly) / len(yearly)
print(f"\n  v2.2 胜率: {win_rate:.0%}  ({sum(r['pt2_beats'] for r in yearly)}/{len(yearly)} 年)")

print()
print("=" * W)
print("  三段汇总")
print("=" * W)
for seg in segments:
    b, p = seg["v20"], seg["v22"]
    print(f"\n  [{seg['segment']}]")
    print(f"  {'版本':<24} {'年化':>8} {'夏普':>7} {'回撤':>8} {'卡玛':>7}")
    print(f"  {'v2.0 (MA16)':24} {b['annual']:>+7.1%}  {b['sharpe']:>6.2f}  {b['maxdd']:>7.1%}  {b['calmar']:>6.2f}")
    print(f"  {'v2.2 (MA16×PT2)':24} {p['annual']:>+7.1%}  {p['sharpe']:>6.2f}  {p['maxdd']:>7.1%}  {p['calmar']:>6.2f}")
    d_ann = p['annual'] - b['annual']
    d_sh  = p['sharpe'] - b['sharpe']
    d_dd  = p['maxdd']  - b['maxdd']
    print(f"  {'  Δ':24} {d_ann:>+7.1%}  {d_sh:>+6.2f}  {d_dd:>+7.1%}")

print()
print(f"  [仓位占比 2018-2026]  v2.0: {exposure_v20:.1%}  v2.2: {exposure_v22:.1%}")
print("=" * W)

# ── 保存 JSON ─────────────────────────────────────────────────────────
out_dir = os.path.join(ROOT, "reports", "research")
os.makedirs(out_dir, exist_ok=True)
out_path = os.path.join(out_dir, "validate_v22.json")
result = {
    "generated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
    "config": {
        "warmup_start": WARMUP_START,
        "top_n": _cfg.top_n,
        "timing_ma": _cfg.timing_ma,
        "rebal_days": _cfg.rebalance_days,
        "pt2_window": 2,
    },
    "exposure_2018_2026": {"v20": exposure_v20, "v22": exposure_v22},
    "yearly": yearly,
    "segments": segments,
    "win_rate": round(win_rate, 4),
}
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f"\n  结果已保存: {out_path}")
