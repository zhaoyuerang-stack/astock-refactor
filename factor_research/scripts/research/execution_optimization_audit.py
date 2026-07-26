"""Execution Optimization Audit (Task 1.2) — T+1 不同入场价对照.

背景: LESSONS 已实证 2024-2025 回测 45.1% → 真实 23.7%, 差 21.4pp 年化纯隔夜跳空摩擦.
今日 plan #2 验证: 把 T+1 09:30 开盘价 换成其他 fill mode 能否找回部分?

5 个 fill mode (复用 paper_replay 引擎, monkey-patch 成交价):
  A. open         T+1 09:30 开盘价 (现状 baseline, paper_replay 23.7%/1.29)
  B. ohlc_mid     (open + close)/2  → 10:30 近似 (避开开盘高点)
  C. vwap_4       (O+H+L+C)/4       → VWAP 粗略近似
  D. close        T+1 收盘 (14:55)  → 摩擦上限参考
  E. lo_close_mid (low + close)/2   → 保守入场 (避高位)

判定:
  · 任一 mode sharpe ≥ baseline + 0.10 → 推荐切换 paper_trade 默认
  · 任一 mode ann ≥ baseline + 5pp     → 立即采纳
  · 否则 → 关闭这条分支 (节省后续 5min K 线工程约 1 周)
"""
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

import scripts.ops.paper_trade as pt
from lake.load_lake import load_raw_close
from strategies.small_cap import StrategyConfig, run_small_cap_strategy

REPLAY_START = sys.argv[1] if len(sys.argv) > 1 else "2024-01-01"
REPLAY_END = "2025-12-31"
TOP_N, REBAL = 25, 20

print("加载内核 (factor/timing 计算)...", flush=True)
res = run_small_cap_strategy(StrategyConfig(start="2010-01-01"))
close, factor, timing = res["close"], res["factor"], res["timing"]
names = pt.load_names()
raw_start = (pd.Timestamp(REPLAY_START) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
raw_close = load_raw_close(start=raw_start)

print("加载 OHLC for fill modes...")
raw_all = pd.read_parquet(
    "data_lake/price/daily_raw_all.parquet",
    columns=["date", "code", "raw_open", "raw_high", "raw_low", "raw_close"],
)
raw_all["date"] = pd.to_datetime(raw_all["date"])
raw_all["code"] = raw_all["code"].astype(str).str.zfill(6)
raw_all = raw_all[raw_all["date"] >= pd.Timestamp(raw_start)]

PANELS = {col: raw_all.pivot(index="date", columns="code", values=col)
          for col in ["raw_open", "raw_high", "raw_low", "raw_close"]}


def _safe_get(panel, d, c):
    if d not in panel.index or c not in panel.columns:
        return None
    v = panel.loc[d, c]
    return float(v) if pd.notna(v) else None


FILL_MODES = {
    "open": lambda d, c: _safe_get(PANELS["raw_open"], d, c),
    "ohlc_mid": lambda d, c: (
        lambda o, cl: (o + cl) / 2 if (o is not None and cl is not None) else None
    )(_safe_get(PANELS["raw_open"], d, c), _safe_get(PANELS["raw_close"], d, c)),
    "vwap_4": lambda d, c: (
        lambda o, h, l, cl: (o + h + l + cl) / 4 if all(x is not None for x in [o, h, l, cl]) else None
    )(_safe_get(PANELS["raw_open"], d, c),
      _safe_get(PANELS["raw_high"], d, c),
      _safe_get(PANELS["raw_low"], d, c),
      _safe_get(PANELS["raw_close"], d, c)),
    "close": lambda d, c: _safe_get(PANELS["raw_close"], d, c),
    "lo_close_mid": lambda d, c: (
        lambda l, cl: (l + cl) / 2 if (l is not None and cl is not None) else None
    )(_safe_get(PANELS["raw_low"], d, c), _safe_get(PANELS["raw_close"], d, c)),
}


def topn(t):
    active = close.loc[t].dropna().index
    return factor.loc[t].reindex(active).dropna().nlargest(TOP_N).index.tolist()


def mark_value(acc, t):
    pv = 0.0
    for c, p in acc["positions"].items():
        if c in raw_close.columns:
            px = raw_close.loc[t, c] if t in raw_close.index else None
            if px is not None and pd.notna(px):
                pv += p["shares"] * px
            else:
                pv += p["shares"] * p["avg_cost"]
    return acc["cash"] + pv


def run_with_fill(fill_fn):
    """复用 paper_replay 循环, monkey-patch paper_trade fill price."""
    orig_buyable, orig_sellable = pt.buyable_open, pt.sellable_open

    def custom_buy(code, date, name):
        d = pd.Timestamp(date)
        return fill_fn(d, code)

    def custom_sell(code, date, name):
        d = pd.Timestamp(date)
        return fill_fn(d, code)

    pt.buyable_open = custom_buy
    pt.sellable_open = custom_sell

    try:
        dates = close.index[(close.index >= REPLAY_START) & (close.index <= REPLAY_END)]
        acc = {"init_capital": 1e6, "cash": 1e6, "positions": {}}
        pending, position, last_rebal = None, "cash", None
        navs, n_tr, n_bl, n_reb = [], 0, 0, 0

        for t in dates:
            ds = str(t.date())
            if pending is not None:
                tr, bl = [], []
                pt.execute_to_target(acc, ds, pending, TOP_N, names, tr, bl)
                n_tr += len(tr); n_bl += len(bl)
                pending = None
            in_mkt = bool(timing.loc[t])
            is_rebal, new_t = False, None
            if not in_mkt:
                if position == "invested":
                    new_t, is_rebal = [], True
            else:
                gap = int(((dates > last_rebal) & (dates <= t)).sum()) if last_rebal is not None else 999
                if position != "invested" or gap >= REBAL:
                    new_t, is_rebal = topn(t), True
            if is_rebal:
                pending = new_t
                n_reb += 1
                if new_t:
                    last_rebal, position = t, "invested"
                else:
                    position = "cash"
            navs.append((t, mark_value(acc, t)))

        nav = pd.Series(dict(navs))
        return nav, n_tr, n_bl, n_reb
    finally:
        pt.buyable_open, pt.sellable_open = orig_buyable, orig_sellable


def stats(n):
    r = n.pct_change().fillna(0)
    ann = (n.iloc[-1] / n.iloc[0]) ** (252 / len(n)) - 1
    dd = (n / n.cummax() - 1).min()
    sh = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0
    return float(ann), float(dd), float(sh)


# ── 跑 5 mode ──
dates_run = close.index[(close.index >= REPLAY_START) & (close.index <= REPLAY_END)]
print(f"\n回测期 {dates_run[0].date()} ~ {dates_run[-1].date()}, {len(dates_run)} 交易日\n")

print(f"{'='*70}")
print("  EXECUTION OPTIMIZATION AUDIT")
print(f"{'='*70}")
print(f"  {'mode':<14s} {'ann':>8s} {'mdd':>8s} {'sh':>6s}  n_trades  vs baseline")
print(f"  {'-'*68}")

results = {}
baseline_sh = baseline_ann = None
for mode in ["open", "ohlc_mid", "vwap_4", "close", "lo_close_mid"]:
    nav, ntr, nbl, nreb = run_with_fill(FILL_MODES[mode])
    a, d, sh = stats(nav)
    results[mode] = (a, d, sh, ntr)
    if mode == "open":
        baseline_sh, baseline_ann = sh, a

    if mode == "open":
        tag = "  (baseline)"
    else:
        d_sh, d_ann = sh - baseline_sh, a - baseline_ann
        tag = f"  Δsh={d_sh:+.2f}  Δann={d_ann:+.1%}"
        if d_sh >= 0.10:
            tag += "  ⭐ 推荐切换"
        elif d_ann >= 0.05:
            tag += "  ⭐ +5pp ann"
    print(f"  {mode:<14s} {a:>+8.1%} {d:>+8.1%} {sh:>+6.2f}   {ntr:>6d}{tag}")

# ── 判定 ──
print(f"\n{'='*70}")
print("  判定")
print(f"{'='*70}")
best_mode = max(results.items(), key=lambda kv: kv[1][2])
best_sh_delta = best_mode[1][2] - baseline_sh
if best_mode[0] != "open" and best_sh_delta >= 0.10:
    print(f"  ⭐ 推荐切换 paper_trade 默认 fill_mode = '{best_mode[0]}'")
    print(f"     Sharpe 改善 +{best_sh_delta:.2f} (从 {baseline_sh:.2f} 到 {best_mode[1][2]:.2f})")
    print(f"     ann 改善 +{(best_mode[1][0] - baseline_ann)*100:+.1f}pp")
elif best_mode[0] != "open" and (best_mode[1][0] - baseline_ann) >= 0.05:
    print(f"  ⭐ 推荐切换 fill_mode = '{best_mode[0]}' (ann +{(best_mode[1][0] - baseline_ann)*100:.1f}pp)")
else:
    print("  ❌ 无任一 mode 显著改善")
    print(f"     最佳 '{best_mode[0]}' Δsh={best_sh_delta:+.2f}, Δann={(best_mode[1][0] - baseline_ann)*100:+.1f}pp")
    print("  → 关闭这条分支: 节省 5min K 线工程约 1 周 (开盘成交是 OHLC 近似下最优)")

# ── 隔夜跳空诊断 ──
print(f"\n{'='*70}")
print("  隔夜跳空诊断 (open - prev_close) / prev_close")
print(f"{'='*70}")
gaps = (PANELS["raw_open"] - PANELS["raw_close"].shift(1)) / PANELS["raw_close"].shift(1)
gaps = gaps.loc[REPLAY_START:REPLAY_END]
flat = gaps.values.flatten()
flat = flat[~np.isnan(flat)]
for q in [5, 25, 50, 75, 95]:
    print(f"  pctile {q:>3d}: {np.percentile(flat, q):+.2%}")
print(f"  mean: {np.mean(flat):+.3%}, std: {np.std(flat):.3%}")
print(f"  正跳空 (高开) 比例: {(flat > 0).mean():.0%}")
print(f"  显著高开 (>+1%) 比例: {(flat > 0.01).mean():.0%}")
