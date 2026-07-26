"""Phase 2.2 — 5 ETF 趋势策略 + 组合层验证.

判定:
  · 每 ETF 测 MA60 / MA120 趋势, 选最佳 Sharpe
  · Sharpe ≥ 0.95 (= portfolio 1.89 × 50% 数学约束) 入候选池
  · 候选池逐个加入 A 股 ACTIVE 组合, 测 risk_parity Sharpe/Calmar Δ
  · 同 2018 起算 (与 A 股 baseline 同口径)
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

ETF_DIR = ROOT / "data_lake" / "cross_asset" / "etf"
ETFS = {
    "511010": "国债 ETF",
    "518880": "黄金 ETF",
    "159920": "恒生 ETF",
    "510880": "红利 ETF",
    "513100": "纳指 ETF",
}
START = "2018-01-01"   # 与 A 股 baseline 同口径


def load_etf(code):
    df = pd.read_parquet(ETF_DIR / f"{code}.parquet")
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df.loc[START:]


def trend_strategy(close, ma_window, leverage=1.0):
    """简单 MA 趋势: close > MA_n → 持仓 (1x), 否则空仓 (0).
    leverage 用 1.0 (跨资产 ETF 不加杠杆, 避免单点风险)."""
    ma = close.rolling(ma_window).mean()
    in_market = (close > ma).shift(1, fill_value=False).astype(float)
    daily_ret = close.pct_change(fill_method=None).fillna(0)
    return daily_ret * in_market * leverage


def metrics(r):
    r = r.dropna()
    if len(r) < 100:
        return None
    ann = float(r.mean() * 252)
    vol = float(r.std() * np.sqrt(252))
    sh = ann / (vol + 1e-9)
    cum = (1 + r).cumprod()
    mdd = float((cum / cum.cummax() - 1).min())
    cal = ann / abs(mdd) if mdd < 0 else 0.0
    return {"ann": ann, "vol": vol, "sh": sh, "mdd": mdd, "cal": cal}


# ─── Step 1: ETF 趋势策略 grid ───
print(f"{'='*70}")
print("  Phase 2.2 — Cross-Asset ETF 趋势策略 (MA60 / MA120, 1.0x lev)")
print(f"{'='*70}")
print(f"  {'ETF':<18s} {'MA':>3s} {'ann':>8s} {'sh':>5s} {'mdd':>7s} {'cal':>5s}  pass(≥0.95)?")
print("  " + "-" * 65)

best_etf = {}   # code → (best_ma, best_returns, best_metrics)
for code, name in ETFS.items():
    df = load_etf(code)
    best_sh = -99
    best = None
    for ma in [60, 120]:
        r = trend_strategy(df["close"], ma)
        m = metrics(r)
        if m is None:
            continue
        pass_thr = "⭐" if m["sh"] >= 0.95 else ""
        print(f"  {code} {name:<14s} {ma:>3d}  {m['ann']:+7.1%} {m['sh']:+5.2f} {m['mdd']:+7.1%} {m['cal']:+5.2f}  {pass_thr}")
        if m["sh"] > best_sh:
            best_sh = m["sh"]
            best = (ma, r, m)
    best_etf[code] = best

# ─── Step 2: 加入 A 股 ACTIVE 组合验证 ───
print(f"\n{'='*70}")
print("  组合层: A 股 ACTIVE + ETF 候选 (risk_parity)")
print(f"{'='*70}")
from portfolio.composer import compose
from portfolio.composer import metrics as pm
from portfolio.strategy_runners import run_active

print("  loading A 股 ACTIVE strategies...")
a_ret = run_active(start=START)

# Baseline
base_rp, _ = compose(a_ret, method="risk_parity")
mb = pm(base_rp)
print(f"\n  A only risk_parity: ann={mb['annual']:+.1%} sh={mb['sharpe']:+.2f} cal={mb['calmar']:+.2f} mdd={mb['maxdd']:+.1%}")

# 逐个 ETF 加入测试
print(f"\n  {'+ ETF':<25s} {'sh':>5s} {'cal':>5s} {'mdd':>7s}  Δsh    Δcal   corr_avg")
print("  " + "-" * 75)
for code, name in ETFS.items():
    best = best_etf[code]
    if best is None:
        continue
    ma, r_etf, m_etf = best
    # corr to A 股
    common = list(a_ret.values())[0].index
    for ar in a_ret.values():
        common = common.intersection(ar.index)
    common = common.intersection(r_etf.index)
    corrs = []
    for ar in a_ret.values():
        c = ar.loc[common].corr(r_etf.loc[common])
        if not np.isnan(c): corrs.append(c)
    avg_corr = float(np.mean(corrs)) if corrs else 1.0

    combo = {**a_ret, f"ETF_{code}": r_etf}
    cr, _ = compose(combo, method="risk_parity")
    mc = pm(cr)
    d_sh = mc["sharpe"] - mb["sharpe"]
    d_cal = mc["calmar"] - mb["calmar"]
    mark = ""
    if d_sh > 0.02 or d_cal > 0.1:
        mark = "  ⭐"
    elif d_sh < -0.05:
        mark = "  ❌"
    print(f"  + {code} {name:<16s} {mc['sharpe']:+5.2f} {mc['calmar']:+5.2f} {mc['maxdd']:+7.1%}  "
          f"{d_sh:+5.2f} {d_cal:+5.2f}  {avg_corr:+5.2f}{mark}")

# ─── Step 3: 全部 ETF 一起加 ───
print("\n  --- 全部 ETF 一起加 (5 个 ETF + 2 A 股) ---")
all_combo = {**a_ret}
for code, _ in ETFS.items():
    best = best_etf[code]
    if best is None:
        continue
    all_combo[f"ETF_{code}"] = best[1]
cr_all, _ = compose(all_combo, method="risk_parity")
m_all = pm(cr_all)
print(f"  全部加: sh={m_all['sharpe']:+.2f} cal={m_all['calmar']:+.2f} mdd={m_all['maxdd']:+.1%} ann={m_all['annual']:+.1%}")
print(f"  Δ vs baseline: sh={m_all['sharpe']-mb['sharpe']:+.2f} cal={m_all['calmar']-mb['calmar']:+.2f}")

# ─── Step 4: 仅加 Sharpe ≥ 0.95 候选 ───
print("\n  --- 仅加 Sharpe ≥ 0.95 候选 ---")
pass_combo = {**a_ret}
n_pass = 0
for code, _ in ETFS.items():
    best = best_etf[code]
    if best is not None and best[2]["sh"] >= 0.95:
        pass_combo[f"ETF_{code}"] = best[1]
        n_pass += 1

if n_pass > 0:
    cr_pass, _ = compose(pass_combo, method="risk_parity")
    m_pass = pm(cr_pass)
    print(f"  {n_pass} ETF 入候选, 组合: sh={m_pass['sharpe']:+.2f} cal={m_pass['calmar']:+.2f} mdd={m_pass['maxdd']:+.1%}")
    print(f"  Δ vs baseline: sh={m_pass['sharpe']-mb['sharpe']:+.2f} cal={m_pass['calmar']-mb['calmar']:+.2f}")
else:
    print("  无 ETF 单 Sharpe ≥ 0.95, 候选池空")
