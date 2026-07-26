"""因子 × 择时风格配对实验.

对每个因子裸奔特征, 配不同的择时风格:
  - 激进择时: MA8, exp_cap=2.0
  - 标准择时: MA16, exp_cap=1.5 (Band)
  - 保守择时: MA30, exp_cap=1.0
  - 无择时: 全仓 (裸奔)

找出: 哪种因子-择时配对能产生极端收益/回撤组合.

用法:
  cd /Users/kiki/astcok/factor_research
  /opt/homebrew/bin/python3 scripts/research/experiment_factor_timing_pairing.py
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

import numpy as np
import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.small_cap import small_cap_factor, small_cap_timing
from factors.utils import mad_clip, safe_zscore
from factory.lines.line1_generation.mutate_existing import FACTOR_MUTATION_SPECS
from strategies.small_cap import build_rebalance_weights, load_price_panels


def build_factor(fn_name, params, close, volume, amount):
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
        if raw is None or (hasattr(raw, "empty") and raw.empty):
            return None
        f = safe_zscore(mad_clip(raw))
        if f.dropna(how="all").shape[0] < 100:
            return None
        return f
    except Exception:
        return None


def build_band_timing(close, amount, ma_window, exp_cap):
    """Build Band timing with given MA and exposure cap."""
    _, _, dist = small_cap_timing(close, amount, ma_window=ma_window)
    dist_s = dist.shift(1)
    dist_s = dist_s.reindex(close.index)
    # Band formula with configurable cap
    exposure = ((1 + dist_s * 8).clip(0, exp_cap) * (dist_s > 0)).fillna(0.0)
    return exposure


def main():
    print("=" * 80)
    print("  因子 × 择时风格配对实验")
    print("=" * 80)

    print("\n[1/3] 加载数据...", flush=True)
    close, volume, amount = load_price_panels("2010-01-01")
    prices = PricePanel(close=close, volume=None, amount=amount)
    cost = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065)
    print(f"  {close.shape[1]}只 x {close.shape[0]}日")

    # 择时风格
    timing_styles = [
        ("激进 MA8", 8, 2.0),
        ("标准 MA16", 16, 1.5),
        ("保守 MA30", 30, 1.0),
        ("无择时", None, 1.0),
    ]
    timings = {}
    for label, ma, cap in timing_styles:
        if ma is None:
            timings[label] = pd.Series(1.0, index=close.index)
        else:
            timings[label] = build_band_timing(close, amount, ma, cap)

    # 构建所有候选因子
    print("\n[2/3] 构建因子 + 配对回测...", flush=True)
    all_candidates = []
    for fn_name, spec in FACTOR_MUTATION_SPECS.items():
        param_names = list(spec["param_grid"].keys())
        param_values = [spec["param_grid"][n] for n in param_names]
        fn_short = fn_name.rsplit(".", 1)[-1]
        for combo in itertools.product(*param_values):
            params = dict(zip(param_names, combo, strict=True))
            name = f"{fn_short}__" + "_".join(f"{k}{v}" for k, v in params.items())
            factor = build_factor(fn_name, params, close, volume, amount)
            if factor is not None:
                all_candidates.append({"name": name, "factor": factor, "family": fn_short})

    print(f"  共 {len(all_candidates)} 个因子 × {len(timing_styles)} 种择时")

    results = []
    for c in all_candidates:
        try:
            sched = build_rebalance_weights(c["factor"], close, top_n=25, rebalance_days=20)
            for t_label, timing in timings.items():
                exp_cap = [cap for label, _, cap in timing_styles if label == t_label][0]
                lev = 1.0  # Band uses exposure as leverage
                cfg = BacktestConfig(start="2018-01-01", cost=cost, leverage=lev)
                engine = BacktestEngine(prices=prices, config=cfg)
                r = engine.run(Signal(weights=sched, timing=timing, exposure_cap=exp_cap,
                                      family="x", version="")).returns.loc["2018-01-01":].dropna()
                if len(r) < 100:
                    continue
                ann = float(r.mean() * 252)
                vol = float(r.std() * np.sqrt(252))
                sh = (ann - 0.025) / vol if vol > 0 else 0
                dd = float(((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min())
                cal = ann / abs(dd) if dd < 0 else 0
                results.append({
                    "name": c["name"], "family": c["family"],
                    "timing": t_label, "ann": ann, "mdd": dd,
                    "sh": sh, "cal": cal,
                })
        except Exception:
            continue

    df = pd.DataFrame(results)
    print(f"  完成 {len(df)} 个配对")

    # ── 分析 ──
    print("\n[3/3] 结果分析\n")

    # 极端组合
    print("=" * 90)
    print("  🔥 进攻型配对 (年化 > 25%)")
    print("=" * 90)
    offense = df[df["ann"] > 0.25].nlargest(15, "ann")
    for _, row in offense.iterrows():
        print(f"  {row['name']:<40} {row['timing']:<10} ann={row['ann']:+.1%} mdd={row['mdd']:.1%} sh={row['sh']:.2f}")

    print(f"\n{'='*90}")
    print("  🛡 防御型配对 (回撤 > -10%)")
    print(f"{'='*90}")
    defense = df[df["mdd"] > -0.10].nlargest(15, "ann")
    for _, row in defense.iterrows():
        print(f"  {row['name']:<40} {row['timing']:<10} ann={row['ann']:+.1%} mdd={row['mdd']:.1%} sh={row['sh']:.2f}")

    print(f"\n{'='*90}")
    print("  ⚡ 极端不对称 (ann>30% 不管回撤)")
    print(f"{'='*90}")
    extreme = df[df["ann"] > 0.30].nlargest(10, "ann")
    for _, row in extreme.iterrows():
        print(f"  {row['name']:<40} {row['timing']:<10} ann={row['ann']:+.1%} mdd={row['mdd']:.1%}")

    # 按因子家族 × 择时风格汇总
    print(f"\n{'='*90}")
    print("  因子家族 × 择时风格: 最佳配对")
    print(f"{'='*90}")
    print(f"  {'家族':<20} {'最佳择时':<10} {'年化':>8} {'回撤':>8} {'夏普':>6} {'卡玛':>6}")
    print("  " + "-" * 65)
    for fam in sorted(df["family"].unique()):
        fam_df = df[df["family"] == fam]
        best = fam_df.loc[fam_df["sh"].idxmax()]
        print(f"  {fam:<20} {best['timing']:<10} {best['ann']:>+7.1%} {best['mdd']:>+7.1%} "
              f"{best['sh']:>5.2f} {best['cal']:>5.2f}")

    # 配对收益: 用最适配的择时 vs 无择时
    print(f"\n{'='*90}")
    print("  择时增益: 最适配择时 vs 裸奔 (无择时)")
    print(f"{'='*90}")
    print(f"  {'因子':<35} {'最佳择时':<10} {'改进后':>15} {'裸奔':>15} {'增益':>8}")
    print("  " + "-" * 90)
    for fam in sorted(df["family"].unique()):
        fam_df = df[df["family"] == fam]
        best_row = fam_df.loc[fam_df["sh"].idxmax()]
        raw_row = fam_df[fam_df["timing"] == "无择时"]
        if len(raw_row) > 0:
            raw_row = raw_row.loc[raw_row["sh"].idxmax()]
            gain_ann = best_row["ann"] - raw_row["ann"]
            gain_dd = abs(best_row["mdd"]) - abs(raw_row["mdd"])
            print(f"  {fam:<35} {best_row['timing']:<10} "
                  f"ann={best_row['ann']:+.1%} mdd={best_row['mdd']:.1%}  "
                  f"ann={raw_row['ann']:+.1%} mdd={raw_row['mdd']:.1%}  "
                  f"ann{gain_ann:+.1%} dd{gain_dd:+.1%}")

    print()


if __name__ == "__main__":
    main()
