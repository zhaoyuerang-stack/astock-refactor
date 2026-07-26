"""Phase 2.2b — ETF 权重 cap 优化 (找 ann 守住 + Sharpe 改善的甜点).

risk_parity 给国债 vol 3% 过多权重 (>60%) → ann 7.2% 太低.
加权重 cap, 找 ann ≥ 20% 且 sh > 1.89 的最优配置.

Grid: (illiq, small_cap, gold, gov_bond) 权重组合
"""
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import pandas as pd

from portfolio.composer import metrics as pm
from portfolio.strategy_runners import run_active

ETF_DIR = ROOT / "data_lake" / "cross_asset" / "etf"
START = "2018-01-01"


def load_etf_trend(code, ma=60):
    df = pd.read_parquet(ETF_DIR / f"{code}.parquet")
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index().loc[START:]
    close = df["close"]
    ma_line = close.rolling(ma).mean()
    in_mkt = (close > ma_line).shift(1, fill_value=False).astype(float)
    return close.pct_change(fill_method=None).fillna(0) * in_mkt


print("Loading A 股 ACTIVE + ETF returns...")
a_ret = run_active(start=START)
gov = load_etf_trend("511010")
gold = load_etf_trend("518880")

# 对齐
common = None
for r in list(a_ret.values()) + [gov, gold]:
    common = r.index if common is None else common.intersection(r.index)

illiq = a_ret["illiquidity.v1.0"].loc[common]
small = a_ret["small-cap-size.v2.0"].loc[common]
gov_a = gov.loc[common]
gold_a = gold.loc[common]


def portfolio(w_illiq, w_small, w_gold, w_gov):
    total = w_illiq + w_small + w_gold + w_gov
    assert abs(total - 1.0) < 1e-6, f"weights must sum to 1.0, got {total}"
    return (w_illiq * illiq + w_small * small + w_gold * gold_a + w_gov * gov_a)


# Baseline (A only risk_parity ≈ 50/50)
base = portfolio(0.5, 0.5, 0, 0)
mb = pm(base)
print("\nBaseline (50% illiq + 50% small-cap):")
print(f"  ann={mb['annual']:+.1%}  sh={mb['sharpe']:+.2f}  cal={mb['calmar']:+.2f}  mdd={mb['maxdd']:+.1%}")

# Weight grid
print(f"\n{'='*70}")
print("  Weight Grid Search (illiq/small/gold/gov_bond)")
print(f"{'='*70}")
print(f"  {'illiq':>6s} {'small':>6s} {'gold':>5s} {'gov':>5s}  {'ann':>7s} {'sh':>5s} {'cal':>5s} {'mdd':>7s}  Δsh    Δcal")
print("  " + "-" * 78)

configs = [
    # (illiq, small, gold, gov)
    (0.50, 0.50, 0.00, 0.00),   # baseline
    (0.45, 0.45, 0.05, 0.05),
    (0.40, 0.40, 0.10, 0.10),
    (0.35, 0.35, 0.15, 0.15),
    (0.35, 0.35, 0.10, 0.20),
    (0.30, 0.30, 0.15, 0.25),
    (0.30, 0.30, 0.10, 0.30),
    (0.25, 0.25, 0.15, 0.35),
    (0.25, 0.25, 0.10, 0.40),
    # gov 主导
    (0.20, 0.20, 0.10, 0.50),
    # 仅国债 (无黄金)
    (0.40, 0.40, 0.00, 0.20),
    (0.35, 0.35, 0.00, 0.30),
    (0.30, 0.30, 0.00, 0.40),
]

best = None
for wi, ws, wg, wb in configs:
    r = portfolio(wi, ws, wg, wb)
    m = pm(r)
    d_sh = m["sharpe"] - mb["sharpe"]
    d_cal = m["calmar"] - mb["calmar"]
    mark = ""
    if m["annual"] >= 0.20 and m["sharpe"] > mb["sharpe"] + 0.05:
        mark = "  ⭐"
        if best is None or m["sharpe"] > best[1]["sharpe"]:
            best = ((wi, ws, wg, wb), m)
    print(f"  {wi:>6.0%} {ws:>6.0%} {wg:>5.0%} {wb:>5.0%}  "
          f"{m['annual']:+7.1%} {m['sharpe']:+5.2f} {m['calmar']:+5.2f} {m['maxdd']:+7.1%}  "
          f"{d_sh:+5.2f} {d_cal:+5.2f}{mark}")

if best:
    wi, ws, wg, wb = best[0]
    m = best[1]
    print("\n  ⭐ 最优 (ann ≥ 20% 且 sh > baseline + 0.05):")
    print(f"     权重: illiq {wi:.0%} / small {ws:.0%} / gold {wg:.0%} / gov_bond {wb:.0%}")
    print(f"     ann={m['annual']:+.1%}  sh={m['sharpe']:+.2f}  cal={m['calmar']:+.2f}  mdd={m['maxdd']:+.1%}")
    print(f"     vs baseline: Δsh={m['sharpe']-mb['sharpe']:+.2f}  Δcal={m['calmar']-mb['calmar']:+.2f}  "
          f"Δmdd={m['maxdd']-mb['maxdd']:+.1%}")
else:
    print("\n  ❌ 无配置同时满足 ann ≥ 20% 且 sh 改善 > 0.05")
