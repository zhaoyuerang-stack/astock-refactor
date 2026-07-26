"""Rotation structure OOS audit — 2013-2017 设计盲区验证.

指控 #4: v3.0 的"bull→illiq, bear→国债"轮动结构是 2026 年才设计,
但用 2018-2025 数据回测. 设计者可能潜意识知道 2018/2022/2024 三年小盘崩盘.
轮动结构本身可能是 backward-looking 过拟合.

验证: 用 2013-2017 (511010 上市起到结构设计前) 重跑同一轮动.
判定:
  - 轮动 > 现金 → 结构 robust (债券真的对冲熊市)
  - 轮动 ≤ 现金 → 结构是 2018+ data-fitting (不能进 LIVE)

注意:
  - 2013-2017 含 2014-2015 牛市 + 2015-Q3 股灾 + 2016-Q1 熔断
  - 2013-2014 熊市 (上证 -28%) — 债券应该真有用
  - 如果债券在 2013-2017 不胜出现金, 结构就是骗局
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


# ─── 因子 (v3.0 AmihudIlliq w20) ───
def f_amihud(close, amount, window=20):
    ret = close.pct_change(fill_method=None).abs()
    illiq = (ret / (amount.replace(0, np.nan) + 1)).rolling(window).mean()
    return safe_zscore(mad_clip(illiq))


def run_illiq_band(close, volume, amount, start):
    """跑 illiq + Band, 返回 daily returns."""
    prices = PricePanel(close=close, volume=volume, amount=amount)
    factor = f_amihud(close, amount, window=20)
    timing, _, _ = small_cap_timing(close, amount, ma_window=16)
    schedule = build_rebalance_weights(factor, close, top_n=25, rebalance_days=20)
    cfg = BacktestConfig(
        start=start,
        cost=CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065),
        leverage=1.0,
    )
    engine = BacktestEngine(prices=prices, config=cfg)
    signal = Signal(weights=schedule, timing=timing, family="amihud", version="w20")
    return engine.run(signal).returns.dropna()


def load_bond_returns(code="511010"):
    """加载国债 ETF daily returns."""
    df = pd.read_parquet(f"data_lake/cross_asset/etf/{code}.parquet")
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date")
    return df["close"].pct_change(fill_method=None).dropna()


def build_rotation(illiq_ret, bond_ret, close, amount):
    """轮动: bull→illiq, bear→bond.

    dist > 0 (bull) → illiq_ret
    dist ≤ 0 (bear) → bond_ret
    没数据日 → 0 (现金)

    所有 shift(1) 防未来函数已经在 small_cap_timing 内做了.
    """
    _, _, dist = small_cap_timing(close, amount, ma_window=16)
    dist_lagged = dist.shift(1)

    # 三个 series 共同索引
    common = illiq_ret.index.intersection(bond_ret.index).intersection(dist_lagged.dropna().index)
    illiq_a = illiq_ret.reindex(common).fillna(0)
    bond_a = bond_ret.reindex(common).fillna(0)
    bull_mask = (dist_lagged.reindex(common) > 0)

    # bull → illiq, bear → bond
    rotation = pd.Series(np.where(bull_mask, illiq_a, bond_a), index=common)
    return rotation


def metrics(r, label=""):
    r = r.dropna()
    if len(r) < 30:
        return {"label": label, "n": len(r), "ann": 0, "sh": 0, "mdd": 0, "nav": 1.0}
    ann = float(r.mean() * 252)
    vol = float(r.std() * np.sqrt(252))
    sh = ann / (vol + 1e-9)
    cum = (1 + r).cumprod()
    mdd = float((cum / cum.cummax() - 1).min())
    nav = float(cum.iloc[-1])
    return {"label": label, "n": len(r), "ann": ann, "sh": sh, "mdd": mdd, "nav": nav, "vol": vol}


def main():
    print("=" * 80)
    print("Rotation structure OOS audit — 2013-2017 设计盲区验证")
    print("=" * 80)
    print("\n指控 #4: bull→illiq, bear→国债 是 2026 设计的结构,")
    print("用 2018-2025 验证 = 后视偏差. 2013-2017 是设计盲区, 真 OOS.")

    # ─── 跑 illiq w20 + Band (2013 起, 含 warmup) ───
    print("\n[1/3] 跑 illiq w20 + Band 全期 (从 2010 warmup, 2013+ 输出)")
    close, volume, amount = load_price_panels("2010-01-01")
    illiq_ret = run_illiq_band(close, volume, amount, start="2013-01-01")
    m_full = metrics(illiq_ret, "illiq+Band 全期 2013-2026")
    print(f"  {m_full['label']}: ann={m_full['ann']:+.1%} sh={m_full['sh']:+.2f} mdd={m_full['mdd']:+.1%}")

    # ─── 加载国债 ETF ───
    print("\n[2/3] 加载国债 ETF 511010")
    bond_ret = load_bond_returns("511010")
    print(f"  511010 区间: {bond_ret.index.min().date()} → {bond_ret.index.max().date()}")

    # ─── 构造轮动 ───
    print("\n[3/3] 构造 illiq + Band + bond 轮动")
    rotation_ret = build_rotation(illiq_ret, bond_ret, close, amount)
    print(f"  轮动区间: {rotation_ret.index.min().date()} → {rotation_ret.index.max().date()}")

    # ─── 切片对比 3 段 ───
    print("\n" + "=" * 80)
    print("三段对比: 2013-2017 (设计盲区 OOS) vs 2018-2025 (设计期 IS) vs 全期")
    print("=" * 80)

    segments = [
        ("2013-04-01", "2017-12-31", "2013-2017 设计盲区 OOS"),
        ("2018-01-01", "2025-12-31", "2018-2025 设计期 IS"),
        ("2013-04-01", "2026-06-05", "全期 2013-2026"),
    ]

    print(f"\n  {'区间':<28s}  {'策略':<15s}  {'ann':>8s}  {'sh':>6s}  {'mdd':>8s}  "
          f"{'nav':>6s}  {'胜出?':>6s}")
    print("  " + "-" * 78)

    judgements = []
    for s, e, label in segments:
        # illiq only (现金版: bear 时 0 收益)
        ill_seg = illiq_ret.loc[s:e]
        m_ill = metrics(ill_seg, "illiq+Band 现金")

        # 轮动版
        rot_seg = rotation_ret.loc[s:e]
        m_rot = metrics(rot_seg, "illiq+Band+bond")

        # 单独国债
        bond_seg = bond_ret.loc[s:e]
        m_bond = metrics(bond_seg, "国债 ETF only")

        wins = "✓" if m_rot["ann"] > m_ill["ann"] else "✗"
        print(f"  {label:<28s}  {'illiq+Band 现金':<15s}  {m_ill['ann']:>+7.1%}  "
              f"{m_ill['sh']:>+5.2f}  {m_ill['mdd']:>+7.1%}  {m_ill['nav']:>5.2f}")
        print(f"  {'':<28s}  {'illiq+Band+bond':<15s}  {m_rot['ann']:>+7.1%}  "
              f"{m_rot['sh']:>+5.2f}  {m_rot['mdd']:>+7.1%}  {m_rot['nav']:>5.2f}  "
              f"{wins:>6s} Δ{m_rot['ann']-m_ill['ann']:+.1%}")
        print(f"  {'':<28s}  {'bond only':<15s}  {m_bond['ann']:>+7.1%}  "
              f"{m_bond['sh']:>+5.2f}  {m_bond['mdd']:>+7.1%}  {m_bond['nav']:>5.2f}")
        print()
        judgements.append({
            "segment": label,
            "ill_ann": m_ill["ann"], "rot_ann": m_rot["ann"],
            "delta": m_rot["ann"] - m_ill["ann"],
        })

    # ─── 分年看 2013-2017 ───
    print("=" * 80)
    print("2013-2017 设计盲区 — 分年细节")
    print("=" * 80)
    print(f"  {'年':>4s}  {'illiq+Band 现金':>15s}  {'illiq+Band+bond':>15s}  "
          f"{'国债 only':>10s}  {'轮动胜出?':>10s}")
    print("  " + "-" * 70)
    for y in [2013, 2014, 2015, 2016, 2017]:
        s = f"{y}-01-01"
        e = f"{y}-12-31"
        ill_y = illiq_ret.loc[s:e]
        rot_y = rotation_ret.loc[s:e]
        bond_y = bond_ret.loc[s:e]
        ill_ann = float(ill_y.mean() * 252) if len(ill_y) > 30 else 0
        rot_ann = float(rot_y.mean() * 252) if len(rot_y) > 30 else 0
        bond_ann = float(bond_y.mean() * 252) if len(bond_y) > 30 else 0
        wins = "✓" if rot_ann > ill_ann else "✗"
        print(f"  {y:>4d}  {ill_ann:>+14.1%}  {rot_ann:>+14.1%}  {bond_ann:>+9.1%}  {wins:>10s}")

    # ─── 最终判定 ───
    print("\n" + "=" * 80)
    print("最终判定 (指控 #4)")
    print("=" * 80)
    oos_delta = judgements[0]["delta"]
    is_delta = judgements[1]["delta"]
    print(f"\n  2013-2017 设计盲区 OOS: Δ(轮动-现金) = {oos_delta:+.1%}")
    print(f"  2018-2025 设计期 IS  : Δ(轮动-现金) = {is_delta:+.1%}")

    if oos_delta > 0.02:
        print(f"\n  ✓ 设计盲区轮动胜出 +{oos_delta:.1%} → 结构 robust, 不是 backward-looking 过拟合")
        print("     v3.0 轮动结构可信, 进 LIVE 关键剩余风险 = T+1 摩擦 + STATUS 数字对齐")
    elif oos_delta > 0:
        print(f"\n  △ 设计盲区轮动微弱胜出 {oos_delta:+.1%} → 结构边际有效")
        print(f"     注意 OOS Δ ({oos_delta:+.1%}) << IS Δ ({is_delta:+.1%}), 部分 backward-looking")
    else:
        print(f"\n  ✗ 设计盲区轮动不胜出 ({oos_delta:+.1%}) → 结构是 2018+ data-fitting")
        print("     v3.0 轮动结构不能进 LIVE, 退回纯 illiq+Band 基线")


if __name__ == "__main__":
    main()
