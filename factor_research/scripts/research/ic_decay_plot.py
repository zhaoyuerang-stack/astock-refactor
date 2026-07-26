"""IC 衰减可视化 —— 生成4张子图"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # factor_research/
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)  # data_lake 相对路径与脚本原先位于包根时一致

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import spearmanr

from factors.small_cap import small_cap_factor
from strategies.small_cap import load_price_panels


def calc_ic_decay(factor, close, forward_days_list=(1, 2, 3, 5, 10, 20, 40, 60)):
    results = {}
    for fwd in forward_days_list:
        forward_ret = close.pct_change(fwd).shift(-fwd)
        ics = {}
        dates = factor.index.intersection(forward_ret.index)
        for dt in dates:
            f = factor.loc[dt].dropna()
            r = forward_ret.loc[dt].dropna()
            common = f.index.intersection(r.index)
            if len(common) < 30:
                continue
            ic, _ = spearmanr(f[common].values, r[common].values)
            if not np.isnan(ic):
                ics[dt] = ic
        results[fwd] = pd.Series(ics).sort_index()
    return results


def main():
    print("加载数据...", flush=True)
    close, volume, amount = load_price_panels("2018-01-01")
    print(f"数据: {close.shape[1]}只 x {close.shape[0]}日", flush=True)

    factor = small_cap_factor(amount, window=60)
    print("计算IC衰减...", flush=True)

    forward_days = [1, 2, 3, 5, 10, 20, 40, 60]
    ic_series = calc_ic_decay(factor, close, forward_days)

    stats = {}
    for fwd in forward_days:
        s = ic_series[fwd]
        stats[fwd] = {
            "mean": s.mean(),
            "std": s.std(),
            "icir": s.mean() / s.std() if s.std() > 0 else 0,
            "pos_ratio": (s > 0).mean(),
        }

    ic1 = ic_series[1]
    roll_mean = ic1.rolling(252, min_periods=100).mean()

    print("生成图表...", flush=True)
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("Small-Cap Factor IC Decay Analysis (2018-2026)", fontsize=14, fontweight="bold")

    # 图1: IC mean vs horizon
    ax1 = axes[0, 0]
    x = list(forward_days)
    y_mean = [stats[f]["mean"] for f in forward_days]
    y_std = [stats[f]["std"] for f in forward_days]
    colors = ["#2ecc71" if v > 0 else "#e74c3c" for v in y_mean]
    bars = ax1.bar(x, y_mean, color=colors, edgecolor="black", linewidth=0.5)
    ax1.errorbar(x, y_mean, yerr=y_std, fmt="none", color="black", capsize=3, alpha=0.5)
    ax1.axhline(y=0, color="gray", linestyle="--", linewidth=0.8)
    ax1.axhline(y=0.02, color="orange", linestyle=":", linewidth=0.8, label="|IC|=0.02")
    ax1.axhline(y=-0.02, color="orange", linestyle=":", linewidth=0.8)
    ax1.set_xlabel("Forward Days")
    ax1.set_ylabel("IC Mean")
    ax1.set_title("IC Mean vs Forward Horizon")
    ax1.legend()
    for bar, val in zip(bars, y_mean, strict=True):
        ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.003,
                f"{val:+.3f}", ha="center", va="bottom", fontsize=8)

    # 图2: ICIR
    ax2 = axes[0, 1]
    y_icir = [stats[f]["icir"] for f in forward_days]
    ax2.plot(x, y_icir, marker="o", color="#3498db", linewidth=2, markersize=6)
    ax2.fill_between(x, y_icir, alpha=0.2, color="#3498db")
    ax2.axhline(y=0.5, color="orange", linestyle="--", linewidth=0.8, label="ICIR=0.5")
    ax2.set_xlabel("Forward Days")
    ax2.set_ylabel("ICIR")
    ax2.set_title("ICIR vs Forward Horizon")
    ax2.legend()
    for xi, yi in zip(x, y_icir, strict=True):
        ax2.annotate(f"{yi:.2f}", (xi, yi), textcoords="offset points",
                    xytext=(0, 8), ha="center", fontsize=8)

    # 图3: 1d IC time series
    ax3 = axes[1, 0]
    ax3.plot(ic1.index, ic1.values, color="lightgray", alpha=0.5, linewidth=0.5, label="Daily IC")
    ax3.plot(roll_mean.index, roll_mean.values, color="#e74c3c", linewidth=2, label="252d Rolling Mean")
    ax3.axhline(y=0, color="black", linestyle="-", linewidth=0.8)
    ax3.axhline(y=ic1.mean(), color="blue", linestyle="--", linewidth=0.8,
               label=f"Full-Period Mean ({ic1.mean():+.3f})")
    ax3.set_xlabel("Date")
    ax3.set_ylabel("1-Day Rank IC")
    ax3.set_title("1-Day IC Time Series & Rolling Stability")
    ax3.legend(loc="upper right")
    latest = roll_mean.dropna().iloc[-1]
    ax3.annotate(f"Latest: {latest:+.3f}",
                xy=(roll_mean.dropna().index[-1], latest),
                xytext=(-80, 20), textcoords="offset points",
                arrowprops=dict(arrowstyle="->", color="red"),
                fontsize=9, color="red")

    # 图4: Boxplot
    ax4 = axes[1, 1]
    data_for_box = [ic_series[f].dropna().values for f in forward_days]
    bp = ax4.boxplot(data_for_box, labels=[f"{f}D" for f in forward_days],
                     patch_artist=True, showfliers=False)
    for patch, fwd in zip(bp["boxes"], forward_days, strict=True):
        if stats[fwd]["mean"] > 0:
            patch.set_facecolor("#2ecc71")
        else:
            patch.set_facecolor("#e74c3c")
        patch.set_alpha(0.6)
    ax4.axhline(y=0, color="black", linestyle="-", linewidth=0.8)
    ax4.axhline(y=0.02, color="orange", linestyle=":", linewidth=0.8)
    ax4.axhline(y=-0.02, color="orange", linestyle=":", linewidth=0.8)
    ax4.set_xlabel("Forward Horizon")
    ax4.set_ylabel("IC Distribution")
    ax4.set_title("IC Distribution by Horizon (No Outliers)")

    plt.tight_layout(rect=[0, 0, 1, 0.96])

    out_path = Path("reports/ic_decay/ic_decay_plot.png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {out_path}", flush=True)


if __name__ == "__main__":
    main()
