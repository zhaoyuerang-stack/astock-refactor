"""实验: 多周期 IC 闸门 vs 单周期 IC 闸门 (对照).

从工廠 FACTOR_MUTATION_SPECS 生成候选因子 → 双闸门评分 → 找被旧闸门误杀的候选
→ L1 回测验证.

用法:
  cd /Users/kiki/astcok/factor_research
  /opt/homebrew/bin/python3 scripts/research/experiment_multi_period_ic.py
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
from engine.factor_analysis import calc_ic, ic_summary
from factors.small_cap import small_cap_factor, small_cap_timing
from factors.utils import mad_clip, safe_zscore
from factory.lines.line1_generation.mutate_existing import FACTOR_MUTATION_SPECS
from strategies.small_cap import build_rebalance_weights, load_price_panels

STATS_START = "2018-01-01"
PERIODS = [1, 5, 10, 20]
WEIGHTS = [0.1, 0.2, 0.3, 0.4]  # 长周期权重更高
OLD_GATE_THRESHOLD = 0.03  # |ICIR_1d| > 0.03


def build_factor(factor_fn_name: str, params: dict, close, volume, amount):
    """实例化一个因子为 date×code DataFrame. 异常则返回 None."""
    fn_short = factor_fn_name.rsplit(".", 1)[-1]

    try:
        if factor_fn_name == "factors.small_cap.small_cap_factor":
            raw = small_cap_factor(amount, **params)
        elif factor_fn_name == "factors.momentum.mom_n":
            raw = _import_call("factors.momentum", "mom_n")(close, **params)
        elif factor_fn_name == "factors.momentum.volatility":
            raw = _import_call("factors.momentum", "volatility")(close, **params)
        elif factor_fn_name == "factors.momentum.illiquidity":
            raw = _import_call("factors.momentum", "illiquidity")(close, volume, **params)
        elif factor_fn_name == "factors.momentum.price_to_ma":
            raw = _import_call("factors.momentum", "price_to_ma")(close, **params)
        elif factor_fn_name == "factors.microstructure.short_reversal":
            raw = _import_call("factors.microstructure", "short_reversal")(close, **params)
        elif factor_fn_name == "factors.microstructure.price_position":
            raw = _import_call("factors.microstructure", "price_position")(close, **params)
        elif factor_fn_name == "factors.microstructure.vol_breakout":
            raw = _import_call("factors.microstructure", "vol_breakout")(volume, **params)
        elif factor_fn_name == "factors.microstructure.amplitude_mean":
            raw = _import_call("factors.microstructure", "amplitude_mean")(close, **params)
        elif factor_fn_name == "factors.microstructure.ret_zscore_cross":
            raw = _import_call("factors.microstructure", "ret_zscore_cross")(close, **params)
        elif factor_fn_name.startswith("factors.ohlc."):
            mod = importlib.import_module("factors.ohlc")
            raw = getattr(mod, fn_short)(close, **params)
        elif factor_fn_name.startswith("factors.fundamental."):
            mod = importlib.import_module("factors.fundamental")
            raw = getattr(mod, fn_short)(close)
        else:
            return None, f"未知 factor: {factor_fn_name}"
    except Exception as e:
        return None, f"构建异常: {str(e)[:50]}"

    if raw is None or (hasattr(raw, 'empty') and raw.empty):
        return None, "因子为空"

    # zscore + clip
    try:
        factor = safe_zscore(mad_clip(raw))
        if factor.dropna(how="all").shape[0] < 100:
            return None, "有效数据不足"
        return factor.loc[STATS_START:], None
    except Exception:
        return None, "zscore失败"


def _import_call(module, fn_name):
    mod = importlib.import_module(module)
    return getattr(mod, fn_name)


def quick_backtest(factor, close, amount, label):
    """L1 快速回测: illiquidity-style top-25, PT MA16 timing.

    注意: 此函数在子进程中调用，factor/close/amount 需可 pickle.
    """

    try:
        scheduled = build_rebalance_weights(factor, close, top_n=25, rebalance_days=20)
        if len(scheduled) < 10:
            return None
        pt_timing, _, _ = small_cap_timing(close, amount, ma_window=16)
        pt_timing = pt_timing.astype(float).reindex(factor.index).fillna(0.0)

        prices = PricePanel(close=close, volume=None, amount=amount)
        cfg = BacktestConfig(
            start=STATS_START,
            cost=CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065),
            leverage=1.0,
        )
        engine = BacktestEngine(prices=prices, config=cfg)
        signal = Signal(weights=scheduled, timing=pt_timing,
                        exposure_cap=1.0, family="experiment", version="ic_gate_test")
        result = engine.run(signal)
        r = result.returns.loc[STATS_START:].dropna()
        if len(r) < 100:
            return None
        annual = float(r.mean() * 252)
        maxdd = float(((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min())
        vol = float(r.std() * np.sqrt(252))
        sharpe = (annual - 0.025) / vol if vol > 0 else 0.0
        return {"label": label, "annual": annual, "maxdd": maxdd,
                "sharpe": sharpe, "n_days": len(r)}
    except Exception as e:
        return {"label": label, "error": str(e)[:60]}


def _bt_worker(args):
    """Picklable L1 worker for ProcessPoolExecutor."""
    factor, close, amount, label = args
    return quick_backtest(factor, close, amount, label)


def _ic_worker(args):
    """Picklable IC worker: (factor, fwd_rets_dict, periods) -> score row."""
    factor, fwd_rets_dict, periods, name, family = args
    if factor is None or factor.dropna(how="all").shape[0] < 100:
        return None
    row = {"name": name, "family": family[:30]}
    icir_vals = []
    for p in periods:
        fwd = fwd_rets_dict[p]
        ic = calc_ic(factor, fwd)
        if len(ic) < 50:
            row[f"ICIR_{p}d"] = np.nan
            icir_vals.append(0.0)
            continue
        s = ic_summary(ic)
        row[f"ICIR_{p}d"] = s["ICIR"]
        icir_vals.append(abs(s["ICIR"]))
    weighted_score = sum(w * v for w, v in zip(WEIGHTS, icir_vals, strict=True))
    old_pass = abs(row.get("ICIR_1d", 0)) > OLD_GATE_THRESHOLD
    row["weighted_score"] = weighted_score
    row["old_pass"] = old_pass
    row["factor"] = factor
    return row


def main():
    print("=" * 70)
    print("  实验: 多周期 IC 闸门 vs 单周期 IC 闸门")
    print("=" * 70)

    # ── 1. 加载数据 ──
    print("\n[1/6] 加载 data_lake...", flush=True)
    close, volume, amount = load_price_panels("2010-01-01")
    print(f"  {close.shape[1]}只 x {close.shape[0]}日 [{close.index[0].date()} ~ {close.index[-1].date()}]")

    # ── 2. 计算 multi-period forward returns ──
    print("[2/6] 计算多周期 forward returns...", flush=True)
    fwd_rets = {}
    for p in PERIODS:
        fwd = close.pct_change(p).shift(-p).replace([np.inf, -np.inf], np.nan)
        fwd_rets[p] = fwd.loc[STATS_START:]

    # ── 3. 生成候选 + 构建因子 ──
    print("[3/6] 生成候选因子...", flush=True)
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
                "fn_short": fn_short, "family": spec["thesis"].mechanism[:40],
            })

    print(f"  共 {len(candidates)} 个候选")
    n_built = 0
    n_failed = 0
    for c in candidates:
        factor, err = build_factor(c["fn_name"], c["params"], close, volume, amount)
        c["factor"] = factor
        c["error"] = err
        if factor is not None:
            n_built += 1
        else:
            n_failed += 1
    print(f"  成功: {n_built}, 失败: {n_failed}")

    # ── 4. 双闸门评分 (并行 IC 计算) ──
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor, as_completed

    n_workers = min(multiprocessing.cpu_count(), 8)
    print(f"[4/6] 并行计算多周期 IC ({len(candidates)} 候选, {n_workers} workers)...", flush=True)

    ic_args = []
    for c in candidates:
        if c["factor"] is not None:
            ic_args.append((c["factor"], fwd_rets, PERIODS, c["name"], c["family"]))

    results = []
    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = {executor.submit(_ic_worker, args): args[2] for args in ic_args}
        for future in as_completed(futures):
            row = future.result()
            if row is not None:
                results.append(row)

    df = pd.DataFrame(results)
    if df.empty:
        print("  无有效结果"); return

    # 新闸门阈值: 用分布的中位数 (数据驱动)
    new_threshold = df["weighted_score"].median()
    df["new_pass"] = df["weighted_score"] > new_threshold

    both_pass = df[df["old_pass"] & df["new_pass"]]
    both_fail = df[(~df["old_pass"]) & (~df["new_pass"])]
    missed = df[(~df["old_pass"]) & df["new_pass"]]
    impossible = df[df["old_pass"] & (~df["new_pass"])]

    print(f"\n  新闸门阈值 (中位数): {new_threshold:.4f}")
    print(f"\n  {'分类':<20} {'数量':>5}")
    print(f"  {'─'*25}")
    print(f"  {'✅ 双通过':<20} {len(both_pass):>5}")
    print(f"  {'❌ 双失败':<20} {len(both_fail):>5}")
    print(f"  {'⚡ 旧❌新✅ (误杀)':<20} {len(missed):>5}")
    print(f"  {'— 旧✅新❌':<20} {len(impossible):>5}")

    # ── 6. 全量并行 L1 回测 ──
    all_candidates = []
    for _, row in df.iterrows():
        all_candidates.append((row["factor"], close, amount, row["name"]))

    print(f"\n[5/6] 并行 L1 回测 ({len(all_candidates)} 候选, {n_workers} workers)...", flush=True)
    bt_results = {}
    completed = 0
    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = {executor.submit(_bt_worker, args): args[3] for args in all_candidates}
        for future in as_completed(futures):
            label = futures[future]
            try:
                bt_results[label] = future.result()
            except Exception as e:
                bt_results[label] = {"label": label, "error": str(e)[:60]}
            completed += 1
            if completed % 10 == 0:
                print(f"  ... {completed}/{len(all_candidates)}", flush=True)
    print(f"  完成 {len(bt_results)} 个 L1 回测", flush=True)

    # 合并回测结果到 df
    df["bt_annual"] = np.nan
    df["bt_maxdd"] = np.nan
    df["bt_sharpe"] = np.nan
    df["bt_error"] = ""
    for i, row in df.iterrows():
        bt = bt_results.get(row["name"])
        if bt and "error" not in bt:
            df.at[i, "bt_annual"] = bt["annual"]
            df.at[i, "bt_maxdd"] = bt["maxdd"]
            df.at[i, "bt_sharpe"] = bt["sharpe"]
        elif bt and "error" in bt:
            df.at[i, "bt_error"] = bt["error"]

    df["bt_pass"] = (df["bt_annual"] > 0.05) & (df["bt_maxdd"] > -0.40)

    # ── 7. 分析 ──
    print("\n[6/6] 分析结果...\n", flush=True)

    both_pass = df[df["old_pass"] & df["new_pass"]]
    both_fail = df[df["old_pass"] == False][df["new_pass"] == False]
    missed = df[df["old_pass"] == False][df["new_pass"]]

    # L1 精度: 各象限的 L1 通过率
    m_l1 = missed["bt_pass"].sum() if len(missed) > 0 else 0

    print(f"  {'象限':<25} {'数量':>5} {'L1通过':>7} {'精度':>7} {'平均年化':>9} {'平均回撤':>9}")
    print(f"  {'─'*65}")
    for name, grp in [("✅ 双通过 (旧✅新✅)", both_pass),
                       ("❌ 双失败 (旧❌新❌)", both_fail),
                       ("⚡ 旧❌新✅ (误杀)", missed)]:
        n_bt = (~grp["bt_annual"].isna()).sum() if len(grp) > 0 else 0
        n_pass = int(grp["bt_pass"].sum()) if n_bt > 0 else 0
        prec = n_pass / max(n_bt, 1)
        avg_a = grp["bt_annual"].mean() if n_bt > 0 else np.nan
        avg_d = grp["bt_maxdd"].mean() if n_bt > 0 else np.nan
        print(f"  {name:<25} {len(grp):>5} {n_pass:>5}  {prec:>6.0%} {avg_a:>+8.1%} {avg_d:>8.1%}")

    # L1 top 10 (按 weighted_score 排序)
    print("\n  L1 表现 Top 10 (按多周期 Score):")
    print(f"  {'候选':<35} {'Score':>8} {'1dICIR':>8} {'年化':>8} {'回撤':>8} {'夏普':>6} {'旧':>4} {'新':>4}")
    print(f"  {'─'*85}")
    top10 = df.dropna(subset=["bt_annual"]).nlargest(10, "weighted_score")
    for _, row in top10.iterrows():
        print(f"  {row['name']:<35} {row['weighted_score']:>7.4f} {row['ICIR_1d']:>+7.3f} "
              f"{row['bt_annual']:>+7.1%} {row['bt_maxdd']:>7.1%} {row['bt_sharpe']:>5.2f} "
              f"{'✅' if row['old_pass'] else '❌':>4} {'✅' if row['new_pass'] else '❌':>4}")

    # L1 best by actual performance
    print("\n  L1 表现 Top 10 (按年化收益):")
    print(f"  {'候选':<35} {'Score':>8} {'1dICIR':>8} {'年化':>8} {'回撤':>8} {'夏普':>6}")
    print(f"  {'─'*75}")
    top10_bt = df.dropna(subset=["bt_annual"]).nlargest(10, "bt_annual")
    for _, row in top10_bt.iterrows():
        print(f"  {row['name']:<35} {row['weighted_score']:>7.4f} {row['ICIR_1d']:>+7.3f} "
              f"{row['bt_annual']:>+7.1%} {row['bt_maxdd']:>7.1%} {row['bt_sharpe']:>5.2f}")

    # ── 结论 ──
    print(f"\n{'='*70}")
    print("  结论")
    print(f"{'='*70}")
    old_prec = df[df["old_pass"]]["bt_pass"].mean() if df["old_pass"].sum() > 0 else 0
    new_prec = df[df["new_pass"]]["bt_pass"].mean() if df["new_pass"].sum() > 0 else 0
    print(f"  旧闸门: 通过 {df['old_pass'].sum()}/{len(df)}, L1精度 {old_prec:.0%}")
    print(f"  新闸门: 通过 {df['new_pass'].sum()}/{len(df)}, L1精度 {new_prec:.0%}")
    print(f"  误杀候选: {len(missed)}, 其中 L1 通过: {int(m_l1)}")

    if new_prec > old_prec + 0.05:
        print(f"\n  ✅ 新闸门 L1 精度更高 ({new_prec:.0%} vs {old_prec:.0%}) — 过滤噪音更有效")
    elif len(missed) > 0 and m_l1 > 0:
        print(f"\n  ✅ 新闸门有价值 — 找到了 {int(m_l1)} 个被旧闸门误杀但 L1 通过的候选")
    else:
        print(f"\n  ⚠️ 新闸门无显著增量: 无误杀, 精度接近 ({new_prec:.0%} vs {old_prec:.0%})")
        print("  根本原因: 旧闸门阈值 0.03 太宽松 (99%通过), 几乎是个 no-op")
        print("  真正的改进方向: 提高旧闸门阈值, 用多周期 IC 增强区分力")

    print()


if __name__ == "__main__":
    main()
