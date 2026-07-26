"""小盘策略因子 IC 衰减分析

计算因子与不同预测周期收益的相关性，评估因子预测能力的持久性。
"""
import warnings

warnings.filterwarnings("ignore")
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # factor_research/
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # data_lake 相对路径与脚本原先位于包根时一致

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from factors.small_cap import small_cap_factor
from strategies.small_cap import load_price_panels


def calc_ic_decay(factor, close, forward_days_list=(1, 2, 3, 5, 10, 20, 40, 60)):
    """计算因子与不同预测周期收益的 IC 序列和统计量。"""
    results = {}

    for fwd in forward_days_list:
        # 未来 fwd 日收益
        forward_ret = close.pct_change(fwd).shift(-fwd)

        # 逐日计算 rank IC
        ics = {}
        for dt in factor.index.intersection(forward_ret.index):
            f = factor.loc[dt].dropna()
            r = forward_ret.loc[dt].dropna()
            common = f.index.intersection(r.index)
            if len(common) < 30:
                continue
            ic, _ = spearmanr(f[common].values, r[common].values)
            if not np.isnan(ic):
                ics[dt] = ic

        ic_series = pd.Series(ics).sort_index()

        # 滚动 60 日 IC 均值（看稳定性）
        ic_rolling = ic_series.rolling(60, min_periods=30).mean()

        results[fwd] = {
            "ic_series": ic_series,
            "ic_mean": ic_series.mean(),
            "ic_std": ic_series.std(),
            "icir": ic_series.mean() / ic_series.std() if ic_series.std() > 0 else np.nan,
            "ic_pos_ratio": (ic_series > 0).mean(),
            "ic_abs_gt_002": (ic_series.abs() > 0.02).mean(),
            "count": len(ic_series),
            "ic_rolling": ic_rolling,
        }

    return results


def rolling_ic_stability(ic_series, window=252):
    """计算滚动 IC 的稳定性：IC 均值、标准差、正比例随时间的变化。"""
    roll_mean = ic_series.rolling(window, min_periods=100).mean()
    roll_std = ic_series.rolling(window, min_periods=100).std()
    roll_pos = ic_series.rolling(window, min_periods=100).apply(lambda x: (x > 0).mean(), raw=True)
    return roll_mean, roll_std, roll_pos


def main():
    print("=" * 70)
    print("小盘策略因子 IC 衰减分析")
    print("=" * 70)

    # 加载数据
    close, volume, amount = load_price_panels("2018-01-01")
    print(f"\n数据: {close.shape[1]}只 × {close.shape[0]}日 [{close.index[0].date()} ~ {close.index[-1].date()}]")

    # 计算因子
    factor = small_cap_factor(amount, window=60)
    print("因子: 成交额60日均值的负对数 (小盘 = 低成交额)")

    # IC 衰减
    forward_days = [1, 2, 3, 5, 10, 20, 40, 60]
    results = calc_ic_decay(factor, close, forward_days)

    print("\n" + "-" * 70)
    print("IC 衰减表")
    print("-" * 70)
    print(f"{'预测周期':>8} | {'IC均值':>8} | {'IC标准差':>8} | {'ICIR':>8} | {'IC>0比例':>8} | {'|IC|>0.02':>8} | {'样本数':>8}")
    print("-" * 70)
    for fwd in forward_days:
        r = results[fwd]
        print(f"{fwd:>6}日 | {r['ic_mean']:>+8.4f} | {r['ic_std']:>8.4f} | {r['icir']:>8.3f} | "
              f"{r['ic_pos_ratio']:>8.2%} | {r['ic_abs_gt_002']:>8.2%} | {r['count']:>8}")

    # 1日 IC 的滚动稳定性
    ic_1d = results[1]["ic_series"]
    roll_mean, roll_std, roll_pos = rolling_ic_stability(ic_1d, window=252)

    print("\n" + "-" * 70)
    print("1日 IC 滚动稳定性 (252交易日窗口)")
    print("-" * 70)
    print(f"最新滚动 IC 均值:  {roll_mean.iloc[-1]:+.4f}")
    print(f"最新滚动 IC 标准差: {roll_std.iloc[-1]:.4f}")
    print(f"最新滚动 IC>0 比例: {roll_pos.iloc[-1]:.1%}")
    print(f"IC 均值范围: [{roll_mean.min():+.4f}, {roll_mean.max():+.4f}]")
    print(f"IC 标准差范围: [{roll_std.min():.4f}, {roll_std.max():.4f}]")
    print(f"IC>0 比例范围: [{roll_pos.min():.1%}, {roll_pos.max():.1%}]")

    # IC 衰减可视化提示
    print("\n" + "-" * 70)
    print("IC 衰减判断")
    print("-" * 70)
    ic_mean_1d = results[1]["ic_mean"]
    ic_mean_20d = results[20]["ic_mean"]
    ic_mean_60d = results[60]["ic_mean"]

    decay_ratio_20 = ic_mean_20d / ic_mean_1d if ic_mean_1d != 0 else 0
    decay_ratio_60 = ic_mean_60d / ic_mean_1d if ic_mean_1d != 0 else 0

    print(f"1日 IC → 20日 IC 衰减: {decay_ratio_20:.1%} ({ic_mean_1d:+.4f} → {ic_mean_20d:+.4f})")
    print(f"1日 IC → 60日 IC 衰减: {decay_ratio_60:.1%} ({ic_mean_1d:+.4f} → {ic_mean_60d:+.4f})")

    if decay_ratio_20 < 0.3:
        print("⚠️ 20日衰减 < 30%：因子预测能力衰减很快，建议缩短调仓周期")
    elif decay_ratio_20 > 0.7:
        print("✅ 20日衰减 > 70%：因子预测能力持久，当前调仓周期合理")
    else:
        print("ℹ️ 20日衰减中等，可根据成本容忍度调整调仓频率")

    # 保存结果
    out_dir = Path("reports/ic_decay")
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = []
    for fwd in forward_days:
        r = results[fwd]
        summary.append({
            "forward_days": fwd,
            "ic_mean": round(r["ic_mean"], 4),
            "ic_std": round(r["ic_std"], 4),
            "icir": round(r["icir"], 3),
            "ic_pos_ratio": round(r["ic_pos_ratio"], 4),
            "ic_abs_gt_002": round(r["ic_abs_gt_002"], 4),
            "count": r["count"],
        })

    import json
    (out_dir / "small_cap_ic_decay.json").write_text(
        json.dumps({
            "factor": "small_cap_factor (neg_log_amount_60d_mean)",
            "period": f"{close.index[0].date()} to {close.index[-1].date()}",
            "universe": close.shape[1],
            "decay": summary,
            "rolling_stability": {
                "latest_ic_mean": round(roll_mean.iloc[-1], 4),
                "latest_ic_std": round(roll_std.iloc[-1], 4),
                "latest_ic_pos_ratio": round(roll_pos.iloc[-1], 4),
            }
        }, ensure_ascii=False, indent=2)
    )
    print(f"\n结果已保存: {out_dir / 'small_cap_ic_decay.json'}")

    # 保存 IC 序列用于后续可视化
    ic_df = pd.DataFrame({f"IC_{fwd}d": results[fwd]["ic_series"] for fwd in forward_days})
    ic_df.to_parquet(out_dir / "ic_series.parquet")
    print(f"IC 序列已保存: {out_dir / 'ic_series.parquet'}")


if __name__ == "__main__":
    main()
