"""Window walk-forward audit — 验证 AmihudIlliq window=20 是不是 in-sample 过拟合.

设计:
  - window grid: [10, 20, 30, 40, 60]
  - WF: 3 年训练 / 1 年测试, 8 个 OOS 窗口 (2018-2025)
  - 训练期内对 window 选 max Sharpe → best_w
  - 测试期应用 best_w → 记录 OOS ann/sh/mdd
  - 同时记录 in-sample 全选 w=20 的 OOS (作为对照基线)

判定:
  - 训练期选 w 稳定 (e.g. 6/8 选 20) + OOS 接近 in-sample → window 不是过拟合
  - 选 w 飘忽 + OOS 大幅衰减 → v3.0 的 +29% 是 window 选择过拟合
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/kiki/astcok/factor_research").resolve()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.small_cap import small_cap_timing
from factors.utils import mad_clip, safe_zscore
from strategies.small_cap import build_rebalance_weights, load_price_panels


def f_amihud(close, amount, window):
    ret = close.pct_change(fill_method=None).abs()
    illiq = (ret / (amount.replace(0, np.nan) + 1)).rolling(window).mean()
    return safe_zscore(mad_clip(illiq))


def run_full(close, volume, amount, window, start="2014-01-01"):
    """跑 2014-2026 完整回测，返回 daily returns (用于后续切片 train/test)."""
    prices = PricePanel(close=close, volume=volume, amount=amount)
    factor = f_amihud(close, amount, window=window)
    timing, _, _ = small_cap_timing(close, amount, ma_window=16)
    schedule = build_rebalance_weights(factor, close, top_n=25, rebalance_days=20)
    cfg = BacktestConfig(
        start=start,
        cost=CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065),
        leverage=1.0,
    )
    engine = BacktestEngine(prices=prices, config=cfg)
    signal = Signal(weights=schedule, timing=timing, family="amihud", version=f"w{window}")
    return engine.run(signal).returns.dropna()


def slice_metrics(r, start, end):
    r = r.loc[start:end].dropna()
    if len(r) < 30:
        return {"ann": 0, "sh": 0, "mdd": 0, "n": len(r)}
    ann = float(r.mean() * 252)
    vol = float(r.std() * np.sqrt(252))
    sh = ann / (vol + 1e-9)
    cum = (1 + r).cumprod()
    mdd = float((cum / cum.cummax() - 1).min())
    return {"ann": ann, "sh": sh, "mdd": mdd, "n": len(r)}


def main():
    print("=" * 80)
    print("Window walk-forward audit — AmihudIlliq window 是否 in-sample 过拟合")
    print("=" * 80)

    # ── 加载数据 ──
    print("\n[1/3] 加载数据 (start=2010, 含 4 年 warmup)...")
    close, volume, amount = load_price_panels("2010-01-01")
    print(f"  panel: {close.shape}, dates: {close.index.min().date()} → {close.index.max().date()}")

    # ── 跑 5 次完整回测，每个 window 一次 ──
    print("\n[2/3] 跑 5 个 window 的完整回测 (2014-2026)...")
    windows = [10, 20, 30, 40, 60]
    returns_by_w = {}
    for w in windows:
        r = run_full(close, volume, amount, window=w, start="2014-01-01")
        returns_by_w[w] = r
        m = slice_metrics(r, r.index.min(), r.index.max())
        print(f"  w={w:>3d}  全期 ann={m['ann']:+.1%}  sh={m['sh']:+.2f}  mdd={m['mdd']:+.1%}  n={m['n']}")

    # ── WF 验证 ──
    print("\n[3/3] WF 滚动 (3 年训练 / 1 年测试)")
    print("=" * 80)

    oos_years = [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]
    print(f"  {'OOS年':>6s}  {'训练期':>17s}  ", end="")
    for w in windows:
        print(f"{'w'+str(w)+'_train_sh':>11s} ", end="")
    print(f"   {'best_w':>6s}  {'OOS_ann':>8s}  {'OOS_sh':>7s}  {'OOS_mdd':>8s}  {'w20_OOS_ann':>11s}")
    print("  " + "-" * 78)

    rows = []
    for oos_y in oos_years:
        train_start = f"{oos_y-3}-01-01"
        train_end = f"{oos_y-1}-12-31"
        oos_start = f"{oos_y}-01-01"
        oos_end = f"{oos_y}-12-31"

        train_shs = {}
        for w in windows:
            m_train = slice_metrics(returns_by_w[w], train_start, train_end)
            train_shs[w] = m_train["sh"]

        # 选 train 期 best window
        best_w = max(train_shs.keys(), key=lambda k: train_shs[k])

        # OOS 用 best_w
        m_oos = slice_metrics(returns_by_w[best_w], oos_start, oos_end)
        # 对照: in-sample w=20 OOS
        m_oos_w20 = slice_metrics(returns_by_w[20], oos_start, oos_end)

        print(f"  {oos_y:>6d}  {train_start[:7]}~{train_end[:7]}  ", end="")
        for w in windows:
            mark = "*" if w == best_w else " "
            print(f"{mark}{train_shs[w]:>+9.2f} ", end="")
        print(f"   {best_w:>6d}  {m_oos['ann']:>+7.1%}  {m_oos['sh']:>+6.2f}  "
              f"{m_oos['mdd']:>+7.1%}  {m_oos_w20['ann']:>+10.1%}")

        rows.append({
            "year": oos_y,
            "best_w": best_w,
            "oos_ann_best": m_oos["ann"],
            "oos_sh_best": m_oos["sh"],
            "oos_mdd_best": m_oos["mdd"],
            "oos_ann_w20": m_oos_w20["ann"],
            "oos_sh_w20": m_oos_w20["sh"],
        })

    # ── 汇总 ──
    print("\n" + "=" * 80)
    print("汇总")
    print("=" * 80)
    df = pd.DataFrame(rows)
    print("\n  WF 8 年 best_w 分布:")
    for w, n in df["best_w"].value_counts().sort_index().items():
        print(f"    w={w:>3d}: {n}/8 年 ({n/8*100:.0f}%)")

    avg_oos_ann_best = df["oos_ann_best"].mean()
    avg_oos_ann_w20 = df["oos_ann_w20"].mean()
    avg_oos_sh_best = df["oos_sh_best"].mean()
    avg_oos_sh_w20 = df["oos_sh_w20"].mean()
    positive_yrs_best = (df["oos_ann_best"] > 0).sum()
    positive_yrs_w20 = (df["oos_ann_w20"] > 0).sum()

    print(f"\n  WF best_w  → avg OOS ann={avg_oos_ann_best:+.1%}, sh={avg_oos_sh_best:+.2f}, "
          f"positive {positive_yrs_best}/8")
    print(f"  Fixed w=20 → avg OOS ann={avg_oos_ann_w20:+.1%}, sh={avg_oos_sh_w20:+.2f}, "
          f"positive {positive_yrs_w20}/8")
    print(f"\n  Δ (WF - Fixed): ann {avg_oos_ann_best-avg_oos_ann_w20:+.1%}, "
          f"sh {avg_oos_sh_best-avg_oos_sh_w20:+.2f}")

    print("\n判定:")
    if df["best_w"].value_counts().max() >= 6:
        dominant_w = df["best_w"].value_counts().idxmax()
        print(f"  ✓ best_w 稳定 (主导 w={dominant_w}, {df['best_w'].value_counts().max()}/8 年)")
    else:
        print("  ✗ best_w 不稳定 (分散度高) → window 选择对 train sample 敏感")

    if abs(avg_oos_ann_best - avg_oos_ann_w20) < 0.03:
        print(f"  ✓ WF vs Fixed-20 OOS 接近 ({avg_oos_ann_best:+.1%} vs {avg_oos_ann_w20:+.1%})")
        print("     → window=20 不是 in-sample 过拟合，固定 20 实战 OK")
    else:
        delta = avg_oos_ann_best - avg_oos_ann_w20
        if delta > 0:
            print(f"  △ WF > Fixed-20 by {delta:+.1%} → window 真有 OOS 价值，固定 20 损失 alpha")
        else:
            print(f"  ✗ WF < Fixed-20 by {delta:+.1%} → 全样本选 20 利用了未来信息，固定 20 实战衰减")


if __name__ == "__main__":
    main()
