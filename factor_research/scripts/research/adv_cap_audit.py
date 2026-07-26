"""ADV cap audit — 验证 v3.0 (AmihudIlliq w20 + Band) 的真实可投资性.

约束: 单股权重 ≤ 5% × ADV_i / portfolio_value
ADV_i = 过去 20 日不复权成交额均值 (shift(1) 防未来函数)

测试规模: PV = 1000万 / 5000万 / 1亿 / 2亿 / 5亿
报告: 触发 cap 比例 / 实际 alpha 衰减
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


# ───── 因子 (复现 v3.0 AmihudIlliq w20, 与 strategy_runners._f_illiquidity 一致) ─────
def f_amihud(close, amount, n=20):
    ret = close.pct_change(fill_method=None).abs()
    illiq = (ret / (amount.replace(0, np.nan) + 1)).rolling(n).mean()
    return safe_zscore(mad_clip(illiq))


# ───── ADV cap 权重重构 ─────
def apply_adv_cap(weights_schedule, amount, pv_yuan, adv_window=20, adv_pct=0.05):
    """Apply ADV cap to scheduled weights.

    weights_schedule: dict {date: Series(weight by code)}
    amount: panel (不复权 amount, 元)
    pv_yuan: 组合本金, 元
    adv_window: ADV 窗口
    adv_pct: 单股权重 ≤ adv_pct × ADV / PV

    Returns: (capped_weights, stats) where stats lists trigger rate per rebal date.
    """
    # ADV = 过去 N 日 amount 均值 shift(1)
    adv = amount.rolling(adv_window).mean().shift(1)

    capped = {}
    stats = []
    for date, w in weights_schedule.items():
        if date not in adv.index:
            capped[date] = w.copy()
            continue
        adv_row = adv.loc[date]

        codes = w.index
        adv_for_codes = adv_row.reindex(codes)
        max_w = (adv_pct * adv_for_codes) / pv_yuan

        # Cap: each stock's actual weight = min(target, max)
        # Excess weight DROPPED (保守 — 不重新分配到其他股, 模拟现实买不到)
        actual = np.minimum(w.values, max_w.values)
        actual = pd.Series(actual, index=codes)
        actual = actual.where(actual.notna(), 0)

        n_capped = int((actual < w * 0.999).sum())
        total_pre = float(w.sum())
        total_post = float(actual.sum())
        stats.append({
            "date": date,
            "n_total": len(w),
            "n_capped": n_capped,
            "wgt_pre": total_pre,
            "wgt_post": total_post,
            "wgt_dropped_pct": (total_pre - total_post) / total_pre if total_pre > 0 else 0,
        })
        capped[date] = actual

    return capped, pd.DataFrame(stats)


# ───── 跑回测 ─────
def run_v3_with_cap(pv_yuan=None, start="2018-01-01", adv_pct=0.05):
    close, volume, amount = load_price_panels(start)
    prices = PricePanel(close=close, volume=volume, amount=amount)
    factor = f_amihud(close, amount)
    timing, _, _ = small_cap_timing(close, amount, ma_window=16)
    schedule = build_rebalance_weights(factor, close, top_n=25, rebalance_days=20)

    if pv_yuan is not None:
        schedule, stats = apply_adv_cap(schedule, amount, pv_yuan, adv_pct=adv_pct)
    else:
        stats = pd.DataFrame()

    cfg = BacktestConfig(
        start=start,
        cost=CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065),
        leverage=1.0,
    )
    engine = BacktestEngine(prices=prices, config=cfg)
    signal = Signal(weights=schedule, timing=timing, family="amihud", version="v3.0")
    result = engine.run(signal)
    return result.returns.dropna(), stats


def metrics(r):
    r = r.dropna()
    if len(r) < 30:
        return {"ann": 0, "sh": 0, "mdd": 0, "nav": 1.0}
    ann = float(r.mean() * 252)
    vol = float(r.std() * np.sqrt(252))
    sh = ann / (vol + 1e-9)
    cum = (1 + r).cumprod()
    mdd = float((cum / cum.cummax() - 1).min())
    nav = float(cum.iloc[-1])
    return {"ann": ann, "sh": sh, "mdd": mdd, "nav": nav, "vol": vol}


def main():
    print("=" * 80)
    print("ADV cap audit — v3.0 (Amihud w20 + Band) 真实可投资性")
    print("=" * 80)

    # Baseline (no cap)
    print("\n[1/6] Baseline (无 cap, 复现 v3.0 报告)")
    r_base, _ = run_v3_with_cap(pv_yuan=None)
    m_base = metrics(r_base)
    print(f"  ann={m_base['ann']:+.1%}  sh={m_base['sh']:+.2f}  mdd={m_base['mdd']:+.1%}  "
          f"nav={m_base['nav']:.2f}  vol={m_base['vol']:.1%}")

    print("\n报告 v3.0 数字: ann +25%, sh ~1.50, mdd -17.7%, nav ~3.0 (8年)")

    # Cap 各档
    pv_list = [
        (1_000_000, "100万 (个人户)"),
        (10_000_000, "1000万 (小私募)"),
        (50_000_000, "5000万 (中私募 — 报告自称容量)"),
        (100_000_000, "1亿 (中等基金)"),
        (200_000_000, "2亿 (大私募)"),
    ]
    rows = []
    for pv, label in pv_list:
        print(f"\n[ADV cap PV={label}]")
        r_cap, stats = run_v3_with_cap(pv_yuan=pv)
        m = metrics(r_cap)
        avg_cap_pct = stats["n_capped"].sum() / max(stats["n_total"].sum(), 1) * 100
        avg_wgt_drop = stats["wgt_dropped_pct"].mean() * 100
        print(f"  ann={m['ann']:+.1%}  sh={m['sh']:+.2f}  mdd={m['mdd']:+.1%}  nav={m['nav']:.2f}")
        print(f"  cap 触发: {avg_cap_pct:.1f}% 股次; 平均掉权重 {avg_wgt_drop:.1f}%")
        rows.append({
            "PV": label,
            "ann": m['ann'],
            "sh": m['sh'],
            "mdd": m['mdd'],
            "nav": m['nav'],
            "cap_pct": avg_cap_pct,
            "wgt_drop": avg_wgt_drop,
            "ann_loss_pp": (m_base['ann'] - m['ann']) * 100,
        })

    print("\n" + "=" * 80)
    print("Alpha 衰减表 (vs baseline)")
    print("=" * 80)
    print(f"  {'PV':<25s} {'ann':>8s} {'Δann':>8s} {'sh':>6s} {'mdd':>8s} "
          f"{'cap触发%':>10s} {'权重掉%':>9s}")
    print("  " + "-" * 78)
    print(f"  {'baseline (∞)':<25s} {m_base['ann']:>+7.1%} {'':>8s} "
          f"{m_base['sh']:>+5.2f} {m_base['mdd']:>+7.1%}")
    for row in rows:
        print(f"  {row['PV']:<25s} {row['ann']:>+7.1%} -{row['ann_loss_pp']:>5.1f}pp "
              f"{row['sh']:>+5.2f} {row['mdd']:>+7.1%} "
              f"{row['cap_pct']:>9.1f}% {row['wgt_drop']:>8.1f}%")


if __name__ == "__main__":
    main()
