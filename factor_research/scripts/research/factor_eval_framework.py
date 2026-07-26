"""因子评价框架探索: 多周期 IC + IC 衰减 + 时间序列视角.

回答三个问题:
  1. 现有因子在 1d/2d/5d/10d/20d forward return 上的 Rank IC 分布如何?
     是否有因子 1d IC 弱但 5d/10d IC 强? (目前 L0 只看 1d, 会漏掉)
  2. illiquidity 因子的 IC 衰减曲线——峰值在哪? 20 天调仓是否合理?
  3. 时间序列视角: 截面 IC 强 ≠ 时序预测强. 每个因子对个股自身的时序预测力如何?

用法:
  cd /Users/kiki/astcok/factor_research
  /opt/homebrew/bin/python3 scripts/research/factor_eval_framework.py
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

from engine.factor_analysis import calc_ic, ic_summary
from factors.momentum import (
    illiquidity as mom_illiquidity,
)
from factors.momentum import (
    mom_n,
    price_to_ma,
    vol_ratio,
    volatility,
)
from factors.small_cap import small_cap_factor
from factors.utils import mad_clip, safe_zscore
from strategies.small_cap import load_price_panels

STATS_START = "2018-01-01"
FORWARD_PERIODS = [1, 2, 3, 5, 10, 20]


def compute_forward_returns(close, periods):
    """Multi-period forward returns. 每个 period 返回一个 date×code DataFrame."""
    results = {}
    for p in periods:
        fwd = close.pct_change(p).shift(-p)  # T 日因子 → T+p 日收益
        fwd = fwd.replace([np.inf, -np.inf], np.nan)
        results[p] = fwd
    return results


def main():
    print("=" * 70)
    print("  因子评价框架探索: 多周期 IC + IC 衰减")
    print("=" * 70)

    # ── 1. 加载数据 ──
    print("\n[1/4] 加载数据...", flush=True)
    close, volume, amount = load_price_panels("2010-01-01")
    print(f"  {close.shape[1]}只 x {close.shape[0]}日 [{close.index[0].date()} ~ {close.index[-1].date()}]")

    # ── 2. 计算多周期 forward returns ──
    print("\n[2/4] 计算多周期 forward returns...", flush=True)
    fwd_rets = compute_forward_returns(close, FORWARD_PERIODS)

    # ── 3. 构建因子池 ──
    print("[3/4] 构建因子...", flush=True)
    factors = {}

    # illiquidity 变体 (我们的主线)
    for w in [20, 60, 120]:
        factors[f"illiq_amt_{w}d"] = small_cap_factor(amount, window=w)

    # 动量变体
    for n in [5, 20, 60, 120, 252]:
        try:
            m = mom_n(close, n=n, skip=0)
            if m is not None and not m.empty:
                factors[f"mom_{n}d"] = safe_zscore(mad_clip(m))
        except Exception:
            pass

    # 波动率
    try:
        v = volatility(close, n=20)
        if v is not None and not v.empty:
            factors["vol_20d"] = safe_zscore(mad_clip(-v))  # 低波→多头
    except Exception:
        pass

    # 价格距均线
    try:
        pma = price_to_ma(close, n=60)
        if pma is not None and not pma.empty:
            factors["price2ma_60d"] = safe_zscore(mad_clip(pma))
    except Exception:
        pass

    # 量比
    try:
        vr = vol_ratio(volume, short=5, long=20)
        if vr is not None and not vr.empty:
            factors["vol_ratio_5_20"] = safe_zscore(mad_clip(vr))
    except Exception:
        pass

    # 另一个 illiquidity 定义 (|ret|/volume)
    try:
        mil = mom_illiquidity(close, volume, n=20)
        if mil is not None and not mil.empty:
            factors["illiq_mom_20d"] = safe_zscore(mad_clip(mil))
    except Exception:
        pass

    print(f"  构建了 {len(factors)} 个因子")

    # ── 4. 多周期 IC 扫描 ──
    print(f"\n[4/4] 多周期 IC 扫描 ({STATS_START} 起)...\n", flush=True)

    # 收集所有结果
    all_results = []
    for name, factor_df in factors.items():
        if factor_df is None or factor_df.empty:
            continue
        factor_clean = factor_df.loc[STATS_START:].replace([np.inf, -np.inf], np.nan)
        if factor_clean.dropna(how="all").shape[0] < 100:
            continue

        row = {"factor": name}
        for p in FORWARD_PERIODS:
            fwd = fwd_rets[p].loc[STATS_START:]
            ic = calc_ic(factor_clean, fwd)
            if len(ic) < 50:
                row[f"IC_{p}d"] = np.nan
                row[f"ICIR_{p}d"] = np.nan
                row[f"pos_{p}d"] = np.nan
                continue
            s = ic_summary(ic)
            row[f"IC_{p}d"] = s["IC_mean"]
            row[f"ICIR_{p}d"] = s["ICIR"]
            row[f"pos_{p}d"] = s["IC>0_ratio"]
        all_results.append(row)

    df = pd.DataFrame(all_results).set_index("factor")

    # ── 输出 ──
    print("=" * 90)
    print("  实验 1: 多周期 Rank IC 均值")
    print("=" * 90)
    ic_cols = [f"IC_{p}d" for p in FORWARD_PERIODS]
    print(df[ic_cols].to_string(float_format=lambda x: f"{x:+.4f}"))

    print("\n" + "=" * 90)
    print("  实验 1b: 多周期 ICIR (= IC_mean / IC_std)")
    print("=" * 90)
    icir_cols = [f"ICIR_{p}d" for p in FORWARD_PERIODS]
    print(df[icir_cols].to_string(float_format=lambda x: f"{x:+.3f}"))

    print("\n" + "=" * 90)
    print("  实验 1c: IC > 0 占比")
    print("=" * 90)
    pos_cols = [f"pos_{p}d" for p in FORWARD_PERIODS]
    print(df[pos_cols].to_string(float_format=lambda x: f"{x:+.1%}"))

    # ── 关键发现 ──
    print("\n" + "=" * 90)
    print("  关键发现")
    print("=" * 90)

    # 找到每个因子的最佳周期
    df["best_period"] = df[ic_cols].abs().idxmax(axis=1)
    df["best_IC"] = df[ic_cols].abs().max(axis=1)
    df["IC_1d"] = df["IC_1d"]

    print("\n  各因子最佳 IC 周期:")
    for name, row in df.iterrows():
        bp = row["best_period"]
        best_ic = row[f"IC_{bp.split('_')[1]}"]
        ic1 = row["IC_1d"]
        flag = " ⚡ 1d弱但长周期强!" if (abs(ic1) < 0.01 and abs(best_ic) > 0.02) else ""
        print(f"  {name:<20}  best={bp.split('_')[1]:>4} IC={best_ic:+.4f}  1d IC={ic1:+.4f}{flag}")

    # IC 衰减曲线: illiquidity 焦点分析
    print("\n" + "=" * 90)
    print("  实验 2: illiquidity IC 衰减曲线")
    print("=" * 90)
    for name in ["illiq_60d", "illiq_20d", "illiq_120d"]:
        if name not in df.index:
            continue
        row = df.loc[name]
        print(f"\n  {name}:")
        for p in FORWARD_PERIODS:
            ic_v = row[f"IC_{p}d"]
            icir_v = row[f"ICIR_{p}d"]
            bar = "█" * max(0, int(abs(ic_v) * 200))
            print(f"    {p:2d}d: IC={ic_v:+.4f}  ICIR={icir_v:+.3f}  {bar}")

    # 时间序列视角
    print("\n" + "=" * 90)
    print("  实验 3: 时间序列预测视角 (illiquidity)")
    print("=" * 90)
    print("  截面 IC 衡量'便宜的股票比贵的股票好吗',")
    print("  时序预测衡量'这个股票便宜时比它自己贵时好吗'.")
    print()

    # 对 illiquidity 因子, 随机抽 20 只股票, 计算各自的时间序列 IC
    factor_ts = factors.get("illiq_amt_60d")
    if factor_ts is not None:
        factor_ts = factor_ts.loc[STATS_START:]
        np.random.seed(42)
        sample_codes = factor_ts.dropna(axis=1, thresh=500).columns
        if len(sample_codes) > 20:
            sample_codes = np.random.choice(sample_codes, 20, replace=False)

        ts_results = []
        for code in sample_codes:
            ts = factor_ts[code].dropna()
            if len(ts) < 200:
                continue
            for p in [1, 5, 10, 20]:
                fwd = close[code].pct_change(p).shift(-p).reindex(ts.index).dropna()
                common = ts.index.intersection(fwd.index)
                if len(common) < 100:
                    continue
                ic = np.corrcoef(ts.loc[common], fwd.loc[common])[0, 1]
                ts_results.append({"code": code, "period": p, "ts_IC": ic})

        ts_df = pd.DataFrame(ts_results)
        if not ts_df.empty:
            summary = ts_df.groupby("period")["ts_IC"].agg(["mean", "std", "count"])
            summary["pct_sig"] = ts_df.groupby("period")["ts_IC"].apply(
                lambda x: (x.abs() > 0.05).mean()
            )
            print("  20 只抽样股票的时序 IC 统计:")
            print(summary.to_string(float_format=lambda x: f"{x:+.4f}"))
            print("\n  注: 时序 IC 远小于截面 IC 是正常的——截面利用了全市场排序信息,")
            print("  时序只利用了单只股票自身的变化. 但对仓位管理/择时, 时序视角更直接.")

    print("\n  输出: 本脚本仅打印结果, 不修改任何系统代码.")


if __name__ == "__main__":
    main()
