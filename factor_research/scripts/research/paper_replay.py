"""真实盘口径历史重放:用 paper_trade 引擎(T+1 不复权开盘成交 + 停牌/涨跌停约束 + 真实成本)
逐日跑一段历史持仓期,完整走 建仓→调仓→清仓,产出真实盘净值曲线,并与回测口径(收盘撮合)对比,
量化 T+1+摩擦的实际拖累。复用 scripts.ops.paper_trade 的成交函数=与线上模拟盘同一套逻辑。

用法(cwd=factor_research): /usr/bin/python3 -m scripts.research.paper_replay [起始 默认2024-01-01]
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import scripts.ops.paper_trade as pt  # noqa: E402
from lake.load_lake import load_raw_close
from strategies.small_cap import StrategyConfig, run_small_cap_strategy

REPLAY_START = sys.argv[1] if len(sys.argv) > 1 else "2024-01-01"
REPLAY_END = "2025-12-31"
TOP_N, REBAL = 25, 20

print("加载回测内核(算 factor/timing,约 1-2 分钟)...", flush=True)
res = run_small_cap_strategy(StrategyConfig(start="2010-01-01"))
close, factor, timing = res["close"], res["factor"], res["timing"]
names = pt.load_names()
raw_start = (pd.Timestamp(REPLAY_START) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
raw_close = load_raw_close(start=raw_start)       # 盯市用不复权收盘(跟随重放区间,提前预热)

dates = close.index[(close.index >= REPLAY_START) & (close.index <= REPLAY_END)]
print(f"重放区间 {dates[0].date()} ~ {dates[-1].date()},{len(dates)} 交易日", flush=True)


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
                pv += p["shares"] * p["avg_cost"]   # 停牌按成本
    return acc["cash"] + pv


acc = {"init_capital": 1e6, "cash": 1e6, "positions": {}}
pending, position, last_rebal = None, "cash", None
navs, n_trades, n_blocked, n_rebal = [], 0, 0, 0

for t in dates:
    ds = str(t.date())
    # 1. 结算上一交易日的 pending(用今天 t 的开盘价成交)
    if pending is not None:
        tr, bl = [], []
        pt.execute_to_target(acc, ds, pending, TOP_N, names, tr, bl)
        n_trades += len(tr)
        n_blocked += len(bl)
        pending = None
    # 2. 今日收盘后决策(贴 run_daily.decide_action)
    in_mkt = bool(timing.loc[t])
    is_rebal, new_target = False, None
    if not in_mkt:
        if position == "invested":
            new_target, is_rebal = [], True            # 择时转空 → 次日清仓
    else:
        gap = int(((dates > last_rebal) & (dates <= t)).sum()) if last_rebal is not None else 999
        if position != "invested" or gap >= REBAL:
            new_target, is_rebal = topn(t), True       # 建仓 / 调仓
    # 3. 有动作 → 记 pending(次日开盘执行)
    if is_rebal:
        pending = new_target
        n_rebal += 1
        if new_target:
            last_rebal, position = t, "invested"
        else:
            position = "cash"
    # 4. 盯市(今收,不复权)
    navs.append((t, mark_value(acc, t)))

nav = pd.Series(dict(navs))
ret = nav.pct_change().fillna(0)


def stats(n):
    r = n.pct_change().fillna(0)
    ann = (n.iloc[-1] / n.iloc[0]) ** (252 / len(n)) - 1
    dd = (n / n.cummax() - 1).min()
    sh = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
    return ann, dd, sh


# 回测口径对比(同区间收盘撮合)
bret = res["returns"].loc[dates[0]:dates[-1]]
bnav = (1 + bret).cumprod() * 1e6
pa, pd_, ps = stats(nav)
ba, bd, bs = stats(bnav)

print("\n" + "=" * 64)
print(f"  真实盘口径历史重放  {dates[0].date()} ~ {dates[-1].date()}")
print("=" * 64)
print(f"  调仓/清仓动作: {n_rebal} 次 | 成交 {n_trades} 笔 | 受阻(停牌/涨跌停) {n_blocked} 笔")
print(f"  最终净值: {nav.iloc[-1]:,.0f}  (本金 1,000,000)")
print("-" * 64)
print(f"  {'口径':<14}{'年化':>10}{'最大回撤':>12}{'夏普':>10}")
print(f"  {'真实盘 T+1':<14}{pa:>9.1%}{pd_:>12.1%}{ps:>10.2f}")
print(f"  {'回测 收盘撮合':<14}{ba:>9.1%}{bd:>12.1%}{bs:>10.2f}")
print(f"  {'差距(摩擦拖累)':<14}{pa-ba:>9.1%}{pd_-bd:>12.1%}{ps-bs:>10.2f}")
print("=" * 64)

out = pd.DataFrame({"date": nav.index, "paper_nav": nav.values,
                    "backtest_nav": bnav.reindex(nav.index).values})
out.to_csv("reports/paper_replay_nav.csv", index=False)
print("\n净值曲线 → reports/paper_replay_nav.csv")
