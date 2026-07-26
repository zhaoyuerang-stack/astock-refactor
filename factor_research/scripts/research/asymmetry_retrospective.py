"""回顾性审计: 用不对称性评分重审工厂全部候选.

找出被对称指标 (Sharpe/ICIR) 淘汰但不对称性结构好的候选.

用法:
  cd /Users/kiki/astcok/factor_research
  /opt/homebrew/bin/python3 scripts/research/asymmetry_retrospective.py
"""
import importlib
import itertools
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
os.chdir(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, str(Path.cwd()))

import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.small_cap import small_cap_factor, small_cap_timing
from factors.utils import mad_clip, safe_zscore
from factory.analysis.asymmetry_audit import asymmetry_report
from factory.lines.line1_generation.mutate_existing import FACTOR_MUTATION_SPECS
from strategies.small_cap import build_rebalance_weights, load_price_panels


def build_factor(fn_name, params, close, volume, amount):
    """实例化因子, 异常返回 None."""
    fn_short = fn_name.rsplit(".", 1)[-1]
    try:
        if fn_name == "factors.small_cap.small_cap_factor":
            raw = small_cap_factor(amount, **params)
        elif fn_name.startswith("factors.momentum."):
            mod = importlib.import_module("factors.momentum")
            if fn_short == "illiquidity":
                raw = getattr(mod, fn_short)(close, volume, **params)
            else:
                raw = getattr(mod, fn_short)(close, **params)
        elif fn_name.startswith("factors.microstructure."):
            mod = importlib.import_module("factors.microstructure")
            if fn_short == "vol_breakout":
                raw = getattr(mod, fn_short)(volume, **params)
            else:
                raw = getattr(mod, fn_short)(close, **params)
        elif fn_name.startswith("factors.ohlc."):
            mod = importlib.import_module("factors.ohlc")
            raw = getattr(mod, fn_short)(close, **params)
        elif fn_name.startswith("factors.fundamental."):
            mod = importlib.import_module("factors.fundamental")
            raw = getattr(mod, fn_short)(close)
        else:
            return None
        if raw is None or (hasattr(raw, 'empty') and raw.empty):
            return None
        factor = safe_zscore(mad_clip(raw))
        if factor.dropna(how="all").shape[0] < 100:
            return None
        return factor
    except Exception:
        return None


def main():
    print("=" * 80)
    print("  回顾性审计: 不对称性 vs 对称性")
    print("=" * 80)

    print("\n[1/4] 加载数据...", flush=True)
    close, volume, amount = load_price_panels("2010-01-01")
    pt_timing, _, _ = small_cap_timing(close, amount, ma_window=16)
    pt_bin = pt_timing.astype(float)
    mkt = close.loc["2018-01-01":].pct_change().mean(axis=1).fillna(0)
    prices = PricePanel(close=close, volume=None, amount=amount)
    cfg = BacktestConfig(start="2018-01-01", cost=CostModel(), leverage=1.0)
    engine = BacktestEngine(prices=prices, config=cfg)

    print(f"  {close.shape[1]}只 x {close.shape[0]}日")

    # ── 生成候选 ──
    print("\n[2/4] 生成候选因子...", flush=True)
    candidates = []
    for fn_name, spec in FACTOR_MUTATION_SPECS.items():
        param_names = list(spec["param_grid"].keys())
        param_values = [spec["param_grid"][n] for n in param_names]
        fn_short = fn_name.rsplit(".", 1)[-1]
        for combo in itertools.product(*param_values):
            params = dict(zip(param_names, combo, strict=True))
            name = f"{fn_short}__{'_'.join(f'{k}{v}' for k, v in params.items())}"
            candidates.append({
                "name": name, "fn_name": fn_name, "params": params,
                "family": fn_short,
            })

    print(f"  共 {len(candidates)} 个候选")

    # ── 构建因子 + 回测 ──
    print("\n[3/4] 回测 + 不对称性审计...", flush=True)
    results = []
    n_done = 0
    for c in candidates:
        factor = build_factor(c["fn_name"], c["params"], close, volume, amount)
        if factor is None:
            continue

        try:
            sched = build_rebalance_weights(factor, close, top_n=25, rebalance_days=20)
            r = engine.run(Signal(weights=sched, timing=pt_bin, exposure_cap=1.0,
                                  family="x", version="")).returns.loc["2018-01-01":].dropna()
            if len(r) < 100:
                continue

            rep = asymmetry_report(r, mkt, c["name"])
            results.append({
                "name": c["name"], "family": c["family"],
                "annual": rep.annual, "maxdd": rep.maxdd,
                "sharpe": rep.sharpe, "sortino": rep.sortino,
                "gain_pain": rep.gain_pain, "up_down_cap": rep.up_down_capture,
                "pos_neg_var": rep.pos_neg_var, "skew": rep.skew_daily,
                "asym_score": rep.asymmetry_score, "verdict": rep.verdict,
                "regime_gp": rep.regime_gain_pain,
            })
            n_done += 1
            if n_done % 10 == 0:
                print(f"  ... {n_done}", flush=True)
        except Exception:
            continue

    df = pd.DataFrame(results)
    print(f"  完成 {len(df)} 个候选")

    # ── 分析 ──
    print("\n[4/4] 结果分析\n")
    print("=" * 80)

    # 1. 不对称性 Top 10
    print("\n  不对称性 Top 10:")
    print(f"  {'候选':<35} {'年化':>8} {'gain/p':>6} {'up/dn':>6} {'sortino':>7} {'sharpe':>7} {'评分':>6}")
    print("  " + "-" * 80)
    top10 = df.nlargest(10, "asym_score")
    for _, row in top10.iterrows():
        print(f"  {row['name']:<35} {row['annual']:>+7.1%} {row['gain_pain']:>5.2f} {row['up_down_cap']:>5.1f} "
              f"{row['sortino']:>6.2f} {row['sharpe']:>6.2f} {row['asym_score']:>5.0%}")

    # 2. 对比: Sharpe Top 10
    print("\n  Sharpe Top 10 (对照):")
    print(f"  {'候选':<35} {'年化':>8} {'gain/p':>6} {'up/dn':>6} {'sortino':>7} {'sharpe':>7} {'评分':>6}")
    print("  " + "-" * 80)
    top10_sh = df.nlargest(10, "sharpe")
    for _, row in top10_sh.iterrows():
        print(f"  {row['name']:<35} {row['annual']:>+7.1%} {row['gain_pain']:>5.2f} {row['up_down_cap']:>5.1f} "
              f"{row['sortino']:>6.2f} {row['sharpe']:>6.2f} {row['asym_score']:>5.0%}")

    # 3. 关键: 不对称好但 Sharpe 差的候选 (被对称指标误杀)
    df["sharpe_rank"] = df["sharpe"].rank(pct=True)
    df["asym_rank"] = df["asym_score"].rank(pct=True)
    df["rank_diff"] = df["asym_rank"] - df["sharpe_rank"]

    # 不对称排名远高于 Sharpe 排名的
    hidden = df[(df["sharpe"] < 0) & (df["asym_score"] > 0)].sort_values("rank_diff", ascending=False)
    print(f"\n  被对称指标'误杀'的候选 (Sharpe<0 但不对称性>0): {len(hidden)}")
    if len(hidden) > 0:
        for _, row in hidden.head(10).iterrows():
            print(f"  {row['name']:<35} ann={row['annual']:+.1%} sharpe={row['sharpe']:+.2f} "
                  f"asym={row['asym_score']:.0%} g/p={row['gain_pain']:.2f} up/dn={row['up_down_cap']:.1f}")

    # 4. 按家族汇总
    print("\n  按因子家族汇总:")
    family_summary = df.groupby("family").agg(
        n=("name", "count"),
        avg_annual=("annual", "mean"),
        avg_sharpe=("sharpe", "mean"),
        avg_asym=("asym_score", "mean"),
        max_asym=("asym_score", "max"),
    ).sort_values("max_asym", ascending=False)
    for fam, row in family_summary.iterrows():
        print(f"  {fam:<20} n={row['n']:2.0f}  avg_ann={row['avg_annual']:+.1%}  "
              f"avg_sh={row['avg_sharpe']:+.2f}  avg_asym={row['avg_asym']:.0%}  max_asym={row['max_asym']:.0%}")

    # 5. 跨家族的不对称宝石
    # 找出非 small_cap 家族但不对称性 > 0 的
    non_sc = df[(~df["family"].str.contains("small_cap")) & (df["asym_score"] > 0)].sort_values("asym_score", ascending=False)
    print(f"\n  非 illiquidity 家族的不对称候选 (可能是新母策略): {len(non_sc)}")
    for _, row in non_sc.head(15).iterrows():
        print(f"  {row['family']:<20} {row['name']:<35} ann={row['annual']:+.1%} "
              f"sh={row['sharpe']:+.2f} asym={row['asym_score']:.0%} g/p={row['gain_pain']:.2f}")

    print()


if __name__ == "__main__":
    main()
