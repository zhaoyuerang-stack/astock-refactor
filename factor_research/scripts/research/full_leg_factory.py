"""完整策略腿工厂 — 74 候选 × 方向 × 择时 × regime.

搜索所有因子, 找每个 regime 的最佳腿, 编排最优组合.

用法:
  cd /Users/kiki/astcok/factor_research
  /opt/homebrew/bin/python3 scripts/research/full_leg_factory.py
"""
import importlib
import itertools
import multiprocessing
import os
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

warnings.filterwarnings("ignore")
os.chdir(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, str(Path.cwd()))

import numpy as np
import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.small_cap import small_cap_factor, small_cap_timing
from factors.utils import mad_clip, safe_zscore
from factory.analysis.asymmetry_audit import asymmetry_report
from factory.lines.line1_generation.mutate_existing import FACTOR_MUTATION_SPECS
from factory.regime import RegimeEngine
from strategies.small_cap import build_rebalance_weights, load_price_panels

# ── 全局数据 (模块级, 供子进程 pickle) ──
_close = _volume = _amount = None
_regime_engine = None


def _init_worker():
    """子进程初始化: 加载全局数据."""
    global _close, _volume, _amount, _regime_engine
    c, v, a = load_price_panels("2010-01-01")
    _close, _volume, _amount = c, v, a
    _regime_engine = RegimeEngine(c, a)


def _build_factor(fn_name, params):
    """实例化因子, 并 shift(1) 防未来函数.

    所有因子在回测中必须 shift(1): T日因子只能用到 T-1 的行情数据。
    少数因子的滚动窗口够长 (60天+) 偏差可忽略，但统一 shift 最安全。
    """
    fn_short = fn_name.rsplit(".", 1)[-1]
    try:
        if fn_name == "factors.small_cap.small_cap_factor":
            raw = small_cap_factor(_amount, **params)
        elif fn_name.startswith("factors.momentum."):
            mod = importlib.import_module("factors.momentum")
            if fn_short == "illiquidity":
                raw = getattr(mod, fn_short)(_close, _volume, **params)
            else:
                raw = getattr(mod, fn_short)(_close, **params)
        elif fn_name.startswith("factors.microstructure."):
            mod = importlib.import_module("factors.microstructure")
            if fn_short == "vol_breakout":
                raw = getattr(mod, fn_short)(_volume, **params)
            else:
                raw = getattr(mod, fn_short)(_close, **params)
        elif fn_name.startswith("factors.ohlc."):
            mod = importlib.import_module("factors.ohlc")
            raw = getattr(mod, fn_short)(_close, **params)
        elif fn_name.startswith("factors.fundamental."):
            mod = importlib.import_module("factors.fundamental")
            raw = getattr(mod, fn_short)(_close)
        else:
            return None
        if raw is None or (hasattr(raw, 'empty') and raw.empty):
            return None
        factor = safe_zscore(mad_clip(raw))
        if factor.dropna(how="all").shape[0] < 100:
            return None
        # ⚠️ 防未来函数: 所有因子 shift(1)
        return factor.shift(1)
    except Exception:
        return None


def _compute_leg(args):
    """子进程: 计算单条腿的 regime 表现. 返回 dict."""
    fn_name, params, fn_short, direction, timing_type = args
    try:
        factor = _build_factor(fn_name, params)
        if factor is None:
            return None

        name = f"{fn_short}_{direction:+d}_{timing_type}"

        if direction == 1:
            sched = build_rebalance_weights(factor, _close, top_n=25, rebalance_days=20)
        else:
            sched = build_rebalance_weights(-factor, _close, top_n=25, rebalance_days=20)
        if len(sched) < 10:
            return None

        if timing_type == "band":
            _, _, dist = small_cap_timing(_close, _amount, ma_window=16)
            dist_s = dist.shift(1)
            timing = ((1 + dist_s * 8).clip(0, 1.5) * (dist_s > 0)).fillna(0.0)
            exp_cap = 1.5
        else:
            timing = pd.Series(1.0, index=_close.index)
            exp_cap = 1.0

        prices = PricePanel(close=_close, volume=None, amount=_amount)
        cfg = BacktestConfig(start="2018-01-01", cost=CostModel(), leverage=1.0)
        engine = BacktestEngine(prices=prices, config=cfg)
        r = engine.run(Signal(weights=sched, timing=timing, exposure_cap=exp_cap,
                              family="x", version="")).returns.loc["2018-01-01":].dropna()
        if len(r) < 100:
            return None

        # Regime 分割
        bull_mask = _regime_engine.trend_up.reindex(r.index).fillna(False)
        bear_mask = _regime_engine.trend_down.reindex(r.index).fillna(False)

        r_bull = r[bull_mask]; r_bear = r[bear_mask]

        def regime_stats(rr):
            if len(rr) < 50: return None
            ann = float(rr.mean() * 252)
            dd = float(((1 + rr).cumprod() / (1 + rr).cumprod().cummax() - 1).min())
            return {"ann": ann, "dd": dd, "n": len(rr)}

        bs = regime_stats(r_bull)
        be = regime_stats(r_bear)

        return {
            "name": name, "fn_short": fn_short,
            "direction": direction, "timing": timing_type,
            "bull": bs, "bear": be,
            "r_series": r,  # for composer
        }
    except Exception:
        return None


def main():
    print("=" * 80)
    print("  完整策略腿工厂 — 74候选 × 方向 × 择时 × regime")
    print("=" * 80)

    # ── 生成所有候选 ──
    all_candidates = []
    for fn_name, spec in FACTOR_MUTATION_SPECS.items():
        param_names = list(spec["param_grid"].keys())
        param_values = [spec["param_grid"][n] for n in param_names]
        fn_short = fn_name.rsplit(".", 1)[-1]
        for combo in itertools.product(*param_values):
            params = dict(zip(param_names, combo, strict=True))
            all_candidates.append((fn_name, params, fn_short))

    print(f"\n  候选: {len(all_candidates)} 个因子参数组合")
    print("  每条候选 × 2方向 × 2择时 = 4条腿")
    print(f"  总腿数: {len(all_candidates) * 4}")

    # ── 并行计算 ──
    n_workers = min(multiprocessing.cpu_count(), 8)
    print(f"\n  并行计算 ({n_workers} workers)...\n")

    tasks = []
    for fn_name, params, fn_short in all_candidates:
        for direction in [1, -1]:
            for timing_type in ["band", "none"]:
                tasks.append((fn_name, params, fn_short, direction, timing_type))

    legs = []
    n_done = 0
    with ProcessPoolExecutor(max_workers=n_workers, initializer=_init_worker) as executor:
        futures = {executor.submit(_compute_leg, t): t for t in tasks}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                legs.append(result)
            n_done += 1
            if n_done % 100 == 0:
                print(f"  ... {n_done}/{len(tasks)} ({len(legs)} valid)", flush=True)

    print(f"\n  完成: {len(legs)} 条有效腿")

    # ── 找最佳 bull/bear 腿 ──
    # Bull: 在 trend=up 中年化最高的腿
    bull_candidates = [l for l in legs if l["bull"] is not None]
    bull_candidates.sort(key=lambda l: l["bull"]["ann"], reverse=True)

    # Bear: 在 trend=down 中年化最高的腿
    bear_candidates = [l for l in legs if l["bear"] is not None]
    bear_candidates.sort(key=lambda l: l["bear"]["ann"], reverse=True)

    print(f"\n  有 bull 数据的腿: {len(bull_candidates)}")
    print(f"  有 bear 数据的腿: {len(bear_candidates)}")

    print("\n  Bull regime Top 10 (trend=up):")
    print(f"  {'腿':<50} {'Bull年化':>9} {'Bull回撤':>9} {'全日年化':>9}")
    print("  " + "-" * 80)
    for l in bull_candidates[:10]:
        full_ann = float(l["r_series"].mean() * 252)
        print(f"  {l['name']:<50} {l['bull']['ann']:>+8.1%} {l['bull']['dd']:>+8.1%} {full_ann:>+8.1%}")

    print("\n  Bear regime Top 10 (trend=down):")
    print(f"  {'腿':<50} {'Bear年化':>9} {'Bear回撤':>9} {'全日年化':>9}")
    print("  " + "-" * 80)
    for l in bear_candidates[:10]:
        full_ann = float(l["r_series"].mean() * 252)
        print(f"  {l['name']:<50} {l['bear']['ann']:>+8.1%} {l['bear']['dd']:>+8.1%} {full_ann:>+8.1%}")

    # ── 编排 ──
    # 用 top-10 bull × top-10 bear 编排
    print(f"\n{'='*80}")
    print("  编排优化 (top-10 × top-10)")
    print(f"{'='*80}")

    # 基线
    close, volume, amount = load_price_panels("2010-01-01")
    illiq = small_cap_factor(amount, window=60)
    w_long = build_rebalance_weights(illiq, close, top_n=25, rebalance_days=20)
    _, _, dist = small_cap_timing(close, amount, ma_window=16)
    band_exp = ((1 + dist.shift(1) * 8).clip(0, 1.5) * (dist.shift(1) > 0)).fillna(0.0)
    prices = PricePanel(close=close, volume=None, amount=amount)
    engine = BacktestEngine(prices=prices, config=BacktestConfig(start="2018-01-01", cost=CostModel(), leverage=1.0))
    r_base = engine.run(Signal(weights=w_long, timing=band_exp, exposure_cap=1.5,
                        family="x", version="")).returns.loc["2018-01-01":].dropna()
    base_ann = float(r_base.mean() * 252)
    base_dd = float(((1 + r_base).cumprod() / (1 + r_base).cumprod().cummax() - 1).min())
    base_nav = (1 + r_base).cumprod().iloc[-1] * 100
    base_sh = (base_ann - 0.025) / (float(r_base.std() * np.sqrt(252)))

    re = RegimeEngine(close, amount)
    bull_mask = re.trend_up
    bear_mask = re.trend_down

    top_bull = bull_candidates[:10]
    top_bear = bear_candidates[:10]

    combos = []
    for bl in top_bull:
        for br in top_bear:
            r_bull = bl["r_series"]; r_bear = br["r_series"]
            common = r_bull.index.intersection(r_bear.index)
            bmask = bull_mask.reindex(common).fillna(False)
            brmask = bear_mask.reindex(common).fillna(False)

            combined = []
            for dt in common:
                if bmask.loc[dt]:
                    combined.append(r_bull.loc[dt])
                elif brmask.loc[dt]:
                    combined.append(r_bear.loc[dt])
                else:
                    combined.append(0.0)
            r_combo = pd.Series(combined, index=common)

            ann = float(r_combo.mean() * 252)
            dd = float(((1 + r_combo).cumprod() / (1 + r_combo).cumprod().cummax() - 1).min())
            nav = (1 + r_combo).cumprod().iloc[-1] * 100
            vol = float(r_combo.std() * np.sqrt(252))
            sh = (ann - 0.025) / vol if vol > 0 else 0
            cal = ann / abs(dd) if dd < 0 else 0

            mkt = close.loc["2018-01-01":].pct_change().mean(axis=1).fillna(0)
            rep = asymmetry_report(r_combo, mkt, "combo")

            combos.append({
                "bull_name": bl["name"], "bear_name": br["name"],
                "bull_ann": bl["bull"]["ann"], "bear_ann": br["bear"]["ann"],
                "ann": ann, "mdd": dd, "sh": sh, "cal": cal, "nav": nav,
                "gain_pain": rep.gain_pain, "asym_score": rep.asymmetry_score,
            })

    combos.sort(key=lambda c: c["nav"], reverse=True)

    print(f"\n  基线: ann={base_ann:+.1%} mdd={base_dd:.1%} sh={base_sh:.2f} nav={base_nav:.0f}万\n")
    print(f"  {'Bull腿':<45} {'Bear腿':<45} {'年化':>8} {'回撤':>8} {'夏普':>6} {'终值':>7} {'vs基线':>8}")
    print("  " + "-" * 120)
    for c in combos[:15]:
        delta = c["nav"] - base_nav
        flag = "✅" if delta > 0 else "❌"
        print(f"  {c['bull_name']:<45} {c['bear_name']:<45} {c['ann']:>+7.1%} {c['mdd']:>+7.1%} "
              f"{c['sh']:>5.2f} {c['nav']:>6.0f}万 {delta:>+7.0f}万 {flag}")

    if combos:
        best = combos[0]
        print(f"\n  最佳编排: {best['bull_name']} + {best['bear_name']}")
        print(f"    年化: {best['ann']:+.1%} (基线 {base_ann:+.1%})")
        print(f"    回撤: {best['mdd']:.1%} (基线 {base_dd:.1%})")
        print(f"    终值: {best['nav']:.0f}万 (基线 {base_nav:.0f}万, +{best['nav']-base_nav:.0f}万)")

    print()


if __name__ == "__main__":
    main()
