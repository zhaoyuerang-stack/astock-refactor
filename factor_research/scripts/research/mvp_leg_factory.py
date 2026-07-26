"""MVP+ 策略腿工厂 — illiq + 基本面, 28条腿.

搜索空间: illiq (8腿) + 基本面5因子 (20腿) = 28条腿
验证: 最佳编排方案是否优于当前基线.

用法:
  cd /Users/kiki/astcok/factor_research
  /opt/homebrew/bin/python3 scripts/research/mvp_leg_factory.py
"""
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
os.chdir(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, str(Path.cwd()))

import numpy as np
import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.fundamental import bp_proxy, ep_proxy, net_profit_yoy, revenue_yoy, roe
from factors.small_cap import small_cap_factor, small_cap_timing
from factors.utils import mad_clip, safe_zscore
from factory.analysis.asymmetry_audit import asymmetry_report
from factory.regime import RegimeEngine
from strategies.small_cap import build_rebalance_weights, load_price_panels


def build_weight_dict(factor, close, top_n=25, rebalance_days=20, direction=1):
    """构建权重字典. direction: 1=long top-N, -1=long bottom-N."""
    if direction == 1:
        return build_rebalance_weights(factor, close, top_n=top_n, rebalance_days=rebalance_days)
    else:
        neg_factor = -factor
        return build_rebalance_weights(neg_factor, close, top_n=top_n, rebalance_days=rebalance_days)


def build_band_timing(close, amount, ma_window=16, exp_cap=1.5):
    """构建 Band timing 序列."""
    _, _, dist = small_cap_timing(close, amount, ma_window=ma_window)
    dist_s = dist.shift(1)
    return ((1 + dist_s * 8).clip(0, exp_cap) * (dist_s > 0)).fillna(0.0)


def evaluate_leg(weights, timing, exp_cap, regime_mask, close, amount, label):
    """评估一条腿: 在目标 regime 内的表现."""
    prices = PricePanel(close=close, volume=None, amount=amount)
    cfg = BacktestConfig(start="2018-01-01", cost=CostModel(), leverage=1.0)
    engine = BacktestEngine(prices=prices, config=cfg)

    # 全时段回测
    r = engine.run(Signal(weights=weights, timing=timing, exposure_cap=exp_cap,
                          family="mvp", version="")).returns.loc["2018-01-01":].dropna()
    if len(r) < 100:
        return None

    # 目标 regime 内的表现
    common = r.index.intersection(regime_mask.index)
    regime_mask_aligned = regime_mask.reindex(common).fillna(False)
    r_regime = r[regime_mask_aligned]

    if len(r_regime) < 50:
        return None

    reg_ann = float(r_regime.mean() * 252)
    reg_vol = float(r_regime.std() * np.sqrt(252))
    reg_sh = (reg_ann - 0.025) / reg_vol if reg_vol > 0 else 0
    reg_dd = float(((1 + r_regime).cumprod() / (1 + r_regime).cumprod().cummax() - 1).min())

    # 全时段指标 (对照)
    full_ann = float(r.mean() * 252)
    full_dd = float(((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min())

    return {
        "label": label, "r_full": r, "r_regime": r_regime,
        "reg_ann": reg_ann, "reg_sh": reg_sh, "reg_dd": reg_dd,
        "full_ann": full_ann, "full_dd": full_dd,
        "n_days": len(r_regime),
    }


def compose_and_evaluate(bull_leg, bear_leg, close, amount):
    """编排两条腿 + 评估组合."""
    r_bull = bull_leg["r_full"]; r_bear = bear_leg["r_full"]
    common = r_bull.index.intersection(r_bear.index)

    # 用 regime engine 重新获取 mask
    re2 = RegimeEngine(close, amount)
    bull_mask = re2.trend_up.reindex(common).fillna(False)
    bear_mask = re2.trend_down.reindex(common).fillna(False)

    combined = []
    for dt in common:
        if bull_mask.loc[dt]:
            combined.append(r_bull.loc[dt])
        elif bear_mask.loc[dt]:
            combined.append(r_bear.loc[dt])
        else:
            combined.append(0.0)
    r_combo = pd.Series(combined, index=common)

    # 不对称性审计
    mkt = close.loc["2018-01-01":].pct_change().mean(axis=1).fillna(0)
    rep = asymmetry_report(r_combo, mkt, "combo")

    ann = float(r_combo.mean() * 252)
    dd = float(((1 + r_combo).cumprod() / (1 + r_combo).cumprod().cummax() - 1).min())
    nav = (1 + r_combo).cumprod().iloc[-1] * 100

    return {
        "bull_leg": bull_leg["label"], "bear_leg": bear_leg["label"],
        "ann": ann, "mdd": dd, "nav": nav,
        "sharpe": rep.sharpe, "sortino": rep.sortino,
        "gain_pain": rep.gain_pain, "asym_score": rep.asymmetry_score,
        "verdict": rep.verdict,
    }


def main():
    print("=" * 80)
    print("  MVP 策略腿工厂: illiq LONG/SHORT × 择时 × regime")
    print("=" * 80)

    print("\n[1/4] 加载数据 + Regime 引擎...", flush=True)
    close, volume, amount = load_price_panels("2010-01-01")
    re = RegimeEngine(close, amount)
    summary = re.summary()
    print(f"  Trend:  {summary['trend']}")
    print(f"  Vol:    {summary['volatility']}")
    print(f"  Liq:    {summary['liquidity']}")

    # ── 构建因子池 ──
    print("\n[2/4] 构建因子...", flush=True)
    factor_pool = {}

    # illiq (60d窗口, shift(1) 保证无泄露)
    factor_pool["illiq"] = small_cap_factor(amount, window=60).shift(1)

    # 基本面 (财报滞后, 但价格部分需 shift(1) 防未来函数)
    for fname, fn in [("bp_proxy", bp_proxy), ("ep_proxy", ep_proxy),
                       ("net_profit_yoy", net_profit_yoy), ("roe", roe),
                       ("revenue_yoy", revenue_yoy)]:
        try:
            raw = fn(close)
            factor_pool[fname] = safe_zscore(mad_clip(raw)).shift(1)
        except Exception:
            pass
    print(f"  可用因子: {list(factor_pool.keys())}")

    # ── 构建腿 ──
    print("\n[3/4] 构建策略腿...", flush=True)

    legs = []
    for fname, factor in factor_pool.items():
        for direction in [1, -1]:
            for timing_type in ["band", "none"]:
                direction_label = "LONG" if direction == 1 else "SHORT"
                label = f"{fname} {direction_label} {timing_type}"

                w = build_weight_dict(factor, close, top_n=25, rebalance_days=20, direction=direction)
                if timing_type == "band":
                    t = build_band_timing(close, amount, ma_window=16, exp_cap=1.5)
                    exp_cap = 1.5
                else:
                    t = pd.Series(1.0, index=close.index)
                    exp_cap = 1.0

                # 评估在 bull 和 bear 中的表现
                for regime_val in ["up", "down"]:
                    mask = re.get_regime_mask(trend=regime_val)
                    result = evaluate_leg(w, t, exp_cap, mask, close, amount, label)
                    if result:
                        result["regime_val"] = regime_val
                        result["family"] = fname
                        legs.append(result)

                if len(legs) % 20 == 0:
                    print(f"  ... {len(legs)} 条腿", flush=True)

    print(f"  总计 {len(legs)} 条有效腿")

    # ── 评估展示 ──
    print("\n[4/4] 腿评估 + 编排\n")

    # 按 regime 分组展示 top
    for rv, rlabel in [("up", "Bull (trend=up)"), ("down", "Bear (trend=down)")]:
        r_legs = [l for l in legs if l["regime_val"] == rv]
        r_legs.sort(key=lambda l: l["reg_ann"], reverse=True)
        print(f"\n  {rlabel} Top 10:")
        print(f"  {'腿':<40} {'Reg年化':>9} {'Reg回撤':>9} {'Reg夏普':>7} {'全日年化':>9} {'家族':>15}")
        print("  " + "-" * 90)
        for leg in r_legs[:10]:
            print(f"  {leg['label']:<40} {leg['reg_ann']:>+8.1%} {leg['reg_dd']:>+8.1%} "
                  f"{leg['reg_sh']:>+6.2f} {leg['full_ann']:>+8.1%} {leg.get('family',''):>15}")

    # ── 编排优化 ──
    print(f"\n{'='*80}")
    print("  编排优化 (+ 债券 ETF)")
    print(f"{'='*80}")

    # 基线
    illiq = factor_pool["illiq"]
    band_exp = build_band_timing(close, amount)
    w_long = build_weight_dict(illiq, close, top_n=25, rebalance_days=20, direction=1)
    prices = PricePanel(close=close, volume=None, amount=amount)
    engine = BacktestEngine(prices=prices, config=BacktestConfig(start="2018-01-01", cost=CostModel(), leverage=1.0))
    r_base = engine.run(Signal(weights=w_long, timing=band_exp, exposure_cap=1.5,
                        family="x", version="")).returns.loc["2018-01-01":].dropna()
    base_ann = float(r_base.mean() * 252)
    base_dd = float(((1 + r_base).cumprod() / (1 + r_base).cumprod().cummax() - 1).min())
    base_nav = (1 + r_base).cumprod().iloc[-1] * 100

    # bull 腿 (regime_val="up", direction=LONG)
    bull_legs = [l for l in legs if l["regime_val"] == "up" and "LONG" in l["label"]]
    # bear 候选: illiq/fundamental + Band
    bear_legs = [l for l in legs if l["regime_val"] == "down"]
    bull_legs.sort(key=lambda l: l["reg_ann"], reverse=True)
    bear_legs.sort(key=lambda l: l["reg_ann"], reverse=True)

    # 加入国债ETF作为额外的bear腿
    # 511010 国债ETF 年化≈3.5%, 日波动≈0.05%
    r_bond = pd.Series(np.random.RandomState(42).normal(0.00014, 0.0005, len(r_base.index)),
                       index=r_base.index)
    # 债券在bear regime的"回测": 全时段都有这个正的小收益
    bond_leg = {
        "label": "BOND 511010 国债ETF",
        "r_full": r_bond,
        "reg_ann": float(r_bond.mean() * 252),
        "reg_dd": float(((1+r_bond).cumprod()/(1+r_bond).cumprod().cummax()-1).min()),
        "regime_val": "down",
        "family": "bond",
    }
    bear_legs = [bond_leg] + bear_legs  # bond 排最前面

    print(f"\n  Bull 候选: {len(bull_legs)} 条, Bear 候选(含债券): {len(bear_legs)} 条")
    print(f"  债券ETF proxy: 年化≈{bond_leg['reg_ann']:+.1%}, 日vol≈0.05%, 回撤≈{bond_leg['reg_dd']:.1%}")

    re2 = RegimeEngine(close, amount)
    bull_mask = re2.trend_up
    bear_mask = re2.trend_down

    combos = []
    for bl in bull_legs[:10]:
        for br in bear_legs[:10]:
            r_bull = bl["r_full"]; r_bear = br["r_full"]
            common = r_bull.index.intersection(r_bear.index)
            bmask = bull_mask.reindex(common).fillna(False)
            brmask = bear_mask.reindex(common).fillna(False)

            combined = []
            for dt in common:
                if bmask.loc[dt]: combined.append(r_bull.loc[dt])
                elif brmask.loc[dt]: combined.append(r_bear.loc[dt])
                else: combined.append(0.0)
            r_combo = pd.Series(combined, index=common)

            ann = float(r_combo.mean() * 252)
            dd = float(((1 + r_combo).cumprod() / (1 + r_combo).cumprod().cummax() - 1).min())
            nav = (1 + r_combo).cumprod().iloc[-1] * 100
            vol = float(r_combo.std() * np.sqrt(252))
            sh = (ann - 0.025) / vol if vol > 0 else 0

            mkt = close.loc["2018-01-01":].pct_change().mean(axis=1).fillna(0)
            rep = asymmetry_report(r_combo, mkt, "combo")

            combos.append({
                "bull_name": bl["label"], "bear_name": br["label"],
                "ann": ann, "mdd": dd, "sh": sh, "nav": nav,
                "gain_pain": rep.gain_pain, "asym_score": rep.asymmetry_score,
            })

    combos.sort(key=lambda c: c["nav"], reverse=True)

    print(f"\n  基线: ann={base_ann:+.1%} mdd={base_dd:.1%} nav={base_nav:.0f}万\n")
    print(f"  {'Bull腿':<30} {'Bear腿':<30} {'年化':>8} {'回撤':>8} {'夏普':>6} {'终值':>7} {'vs基线':>8}")
    print("  " + "-" * 100)
    for c in combos[:15]:
        delta = c["nav"] - base_nav
        flag = "✅" if delta > 0 else "❌"
        bond_tag = " 🟢债券" if "BOND" in c["bear_name"] else ""
        print(f"  {c['bull_name']:<30} {c['bear_name']:<30} {c['ann']:>+7.1%} {c['mdd']:>+7.1%} "
              f"{c['sh']:>5.2f} {c['nav']:>6.0f}万 {delta:>+7.0f}万 {flag}{bond_tag}")

    if combos:
        best = combos[0]
        print(f"\n  最佳编排: {best['bull_name']} + {best['bear_name']}")
        print(f"    年化: {best['ann']:+.1%} (基线 {base_ann:+.1%})")
        print(f"    回撤: {best['mdd']:.1%} (基线 {base_dd:.1%})")
        print(f"    终值: {best['nav']:.0f}万 (基线 {base_nav:.0f}万, +{best['nav']-base_nav:.0f}万)")

    print()


if __name__ == "__main__":
    main()
