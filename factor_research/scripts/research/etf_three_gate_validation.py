"""ETF 配置 OOS 三关验证 (LESSONS 验证纪律).

  1. Walk-Forward MA 稳健性 — 国债/黄金 MA grid [10/30/60/120/240], 看 OOS 选择稳定吗
  2. 分年稳定性 — 35/35/15/15 配置每年独立 Sharpe/ann/mdd
  3. 极端事件 — A 股已知大跌期 (2018/2020-Q1/2022/2024-Q3) ETF 是否真避险
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

from portfolio.composer import metrics as pm
from portfolio.strategy_runners import run_active

ETF_DIR = ROOT / "data_lake" / "cross_asset" / "etf"
START = "2018-01-01"
END = "2026-06-08"


def load_etf_close(code):
    df = pd.read_parquet(ETF_DIR / f"{code}.parquet")
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index().loc[START:END]["close"]


def trend_returns(close, ma):
    ma_line = close.rolling(ma).mean()
    in_mkt = (close > ma_line).shift(1, fill_value=False).astype(float)
    return close.pct_change(fill_method=None).fillna(0) * in_mkt


def sh(r):
    r = r.dropna()
    if len(r) < 30: return np.nan
    return float(r.mean() / (r.std() + 1e-9) * np.sqrt(252))


def metrics_period(r):
    r = r.dropna()
    if len(r) < 30:
        return {"ann": 0, "sh": 0, "mdd": 0}
    ann = float(r.mean() * 252)
    cum = (1 + r).cumprod()
    mdd = float((cum / cum.cummax() - 1).min())
    return {"ann": ann, "sh": sh(r), "mdd": mdd}


# ─── Gate 1: Walk-Forward MA grid ───
print(f"{'='*70}")
print("  Gate 1: Walk-Forward MA grid (年度滚动选最优 MA)")
print(f"{'='*70}")

ma_grid = [10, 30, 60, 120, 240]
for code, name in [("511010", "国债"), ("518880", "黄金")]:
    close = load_etf_close(code)
    print(f"\n  [{code} {name}] MA in-sample 全期 sh:")
    full_sh = {}
    for ma in ma_grid:
        r = trend_returns(close, ma)
        full_sh[ma] = sh(r)
        m = metrics_period(r)
        print(f"    MA{ma:>4d}: ann={m['ann']:+6.1%} sh={m['sh']:+5.2f} mdd={m['mdd']:+6.1%}")

    # Walk-forward: 每年用前 3 年选最优 MA, 测下一年 OOS
    years = sorted(set(close.index.year))
    print("\n  WF 年度选最优 MA (前 3 年训练 → 下年 OOS):")
    print(f"    {'OOS year':>9s}  {'best MA (IS)':>13s}  {'OOS sh':>7s}  {'OOS ann':>8s}")
    wf_results = []
    for i, oos_y in enumerate(years[3:]):
        train_start = pd.Timestamp(f"{years[i]}-01-01")
        train_end = pd.Timestamp(f"{oos_y-1}-12-31")
        train_close = close.loc[train_start:train_end]
        # 选 best MA on train
        best_ma, best_sh = None, -99
        for ma in ma_grid:
            r_train = trend_returns(train_close, ma)
            s = sh(r_train)
            if s > best_sh:
                best_sh, best_ma = s, ma
        # OOS test
        oos_close = close.loc[f"{oos_y}-01-01":f"{oos_y}-12-31"]
        # 用 全期 (含 train tail) 算 MA, 然后只取 oos 段 returns
        r_full = trend_returns(close, best_ma)
        r_oos = r_full.loc[f"{oos_y}-01-01":f"{oos_y}-12-31"]
        m_oos = metrics_period(r_oos)
        wf_results.append((oos_y, best_ma, m_oos["sh"], m_oos["ann"]))
        print(f"    {oos_y:>9d}  {best_ma:>13d}  {m_oos['sh']:+7.2f}  {m_oos['ann']:+8.1%}")
    # WF aggregate
    wf_anns = [x[3] for x in wf_results]
    wf_shs = [x[2] for x in wf_results if not np.isnan(x[2])]
    print(f"    {'AGGREGATE':>9s}  "
          f"avg_sh={np.mean(wf_shs):+.2f}  positive_yrs={sum(1 for x in wf_anns if x > 0)}/{len(wf_anns)}")


# ─── Gate 2: 35/35/15/15 配置分年稳定性 ───
print(f"\n\n{'='*70}")
print("  Gate 2: 35/35/15/15 配置分年独立测试")
print(f"{'='*70}")

a_ret = run_active(start=START)
gov = trend_returns(load_etf_close("511010"), 60)
gold = trend_returns(load_etf_close("518880"), 60)

common = None
for r in list(a_ret.values()) + [gov, gold]:
    common = r.index if common is None else common.intersection(r.index)

illiq = a_ret["illiquidity.v1.0"].loc[common]
small = a_ret["small-cap-size.v2.0"].loc[common]
gov_a, gold_a = gov.loc[common], gold.loc[common]

W = (0.35, 0.35, 0.15, 0.15)
portfolio = W[0]*illiq + W[1]*small + W[2]*gold_a + W[3]*gov_a
baseline = 0.5 * illiq + 0.5 * small

print("\n  按年看 baseline (50/50) vs 推荐 (35/35/15/15):")
print(f"  {'year':>5s}  {'base_ann':>9s} {'base_sh':>8s} {'base_mdd':>9s}  "
      f"{'new_ann':>9s} {'new_sh':>7s} {'new_mdd':>8s}  Δsh   Δmdd")
print("  " + "-" * 90)

years = sorted(set(common.year))
for y in years:
    period = (common.year == y)
    base_y = baseline[period]
    new_y = portfolio[period]
    if len(base_y) < 50: continue
    mb_ = metrics_period(base_y)
    mn_ = metrics_period(new_y)
    flag = ""
    if mb_["ann"] < 0:  # baseline 亏年
        if mn_["ann"] > mb_["ann"]:
            flag = "  ✓ 防御生效"
        else:
            flag = "  ✗ 防御失败"
    print(f"  {y:>5d}  {mb_['ann']:>+9.1%} {mb_['sh']:>+8.2f} {mb_['mdd']:>+9.1%}  "
          f"{mn_['ann']:>+9.1%} {mn_['sh']:>+7.2f} {mn_['mdd']:>+8.1%}  "
          f"{mn_['sh']-mb_['sh']:+.2f}  {mn_['mdd']-mb_['mdd']:+.1%}{flag}")


# ─── Gate 3: 极端事件 ───
print(f"\n\n{'='*70}")
print("  Gate 3: A 股已知大跌期, ETF 是否真避险")
print(f"{'='*70}")

# 找 baseline drawdown 最大的 5 个区间
cum_b = (1 + baseline).cumprod()
peak_b = cum_b.cummax()
dd_b = cum_b / peak_b - 1

# 简单识别: 滚动 60d return baseline 最低 5 个 60d 窗口
roll_60 = baseline.rolling(60).sum()
worst_60d = roll_60.nsmallest(10)   # 取 10 个候选去重

stress_periods = []
seen_months = set()
for end_date, ret_60 in worst_60d.items():
    month_key = (end_date.year, end_date.month)
    if month_key in seen_months: continue
    seen_months.add(month_key)
    start_d = end_date - pd.Timedelta(days=60)
    stress_periods.append((start_d, end_date, ret_60))
    if len(stress_periods) >= 5: break

print("\n  baseline 历史 5 个最差 60 日 (A 股最痛苦时刻):")
print(f"  {'period':>22s}  {'A股 60d ret':>11s}  {'gold 60d':>10s}  {'gov 60d':>9s}  {'组合 60d':>9s}  防御?")
print("  " + "-" * 88)
for s, e, _ in stress_periods:
    base_r = baseline.loc[s:e].sum()
    gold_r = gold_a.loc[s:e].sum()
    gov_r = gov_a.loc[s:e].sum()
    new_r = portfolio.loc[s:e].sum()
    flag = "  ✓" if (gold_r > 0 or gov_r > 0) else "  ✗"
    print(f"  {s.date()} ~ {e.date()}  {base_r:+11.1%}  {gold_r:+10.1%}  {gov_r:+9.1%}  {new_r:+9.1%}{flag}")


# ─── 总结 ───
print(f"\n\n{'='*70}")
print("  三关总结")
print(f"{'='*70}")
m_base = pm(baseline)
m_port = pm(portfolio)
print("\n  全期 (2018-2026) baseline vs 推荐:")
print(f"    baseline: ann {m_base['annual']:+.1%} sh {m_base['sharpe']:+.2f} cal {m_base['calmar']:+.2f} mdd {m_base['maxdd']:+.1%}")
print(f"    35/35/15/15: ann {m_port['annual']:+.1%} sh {m_port['sharpe']:+.2f} cal {m_port['calmar']:+.2f} mdd {m_port['maxdd']:+.1%}")
print(f"    Δ: sh {m_port['sharpe']-m_base['sharpe']:+.2f}  cal {m_port['calmar']-m_base['calmar']:+.2f}  mdd {m_port['maxdd']-m_base['maxdd']:+.1%}")
