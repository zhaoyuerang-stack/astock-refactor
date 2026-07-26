"""中性化多因子 diversifier blend 体检(L0,非 alpha)。

把手里证明过的正交 diversifier 信号——北向 / 股东户数 / 主力吸筹背离——各自截面中性化掉
size+流动性+动量,逐截面 z-score 后**等权平均**(等权=零拟合,不抬 n_trials),量:
  ① blend 的残差 IC(去 size/流动性/动量)是否优于最佳单因子(=分散收益);
  ② 三者中性化残差的两两相关(低=真分散,blend 才划算);
  ③ IS/OOS 稳定性。

复用 signal_source_probe 内部(同口径:月频调仓、forward 月收益、lstsq 残差)。
诚实边界:仅 L0 证据,非 alpha(无成本/DSR/容量/9-Gate)。入册走 diversifier 轨 workflow。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from factors.capital_flow import smart_money_divergence  # noqa: E402
from factors.northbound import northbound_accumulation  # noqa: E402
from factors.shareholder import holder_count_chg  # noqa: E402
from factors.utils import mad_clip, safe_zscore  # noqa: E402
from scripts.research import signal_source_probe as P  # noqa: E402

START, CUTOFF, END = "2018-01-01", "2022-12-31", "2024-12-31"

SIGNALS = {
    "northbound": lambda c: northbound_accumulation(c, window=20),
    "holder":     lambda c: holder_count_chg(c, window=60),
    "smart_div":  lambda c: smart_money_divergence(c, window=20),
}


def _zrow(df: pd.DataFrame) -> pd.DataFrame:
    """逐截面(行)z-score,使等权平均公平。"""
    return safe_zscore(mad_clip(df))


def _ic(fac, fwd, lo, hi):
    d = P._seg_ic(fac, fwd, lo, hi)
    return (round(d["ic"], 4), round(d["icir"], 2)) if d else None


def main():
    close = P._load_close("all")
    rb = P._monthly_rebalance(close, START, END)
    fwd = P._forward_returns(close, rb)
    rb2 = [t for t in rb if t in fwd.index]

    ctl = {k: v.reindex(rb2) for k, v in P._load_controls(close).items()}
    nctrls = [ctl["size"], ctl["liquidity"], ctl["momentum"]]

    # 各信号:reindex → 三重中性化残差 → 逐截面 z-score
    neut = {}
    for name, fn in SIGNALS.items():
        fac = fn(close).reindex(rb2)
        resid = P._neutralize(fac, nctrls)
        neut[name] = _zrow(resid)

    # 等权 blend(skipna:某股某日缺某信号则用可得信号均值,北向仅 Connect universe)
    stack = np.stack([neut[n].values for n in SIGNALS], axis=0)
    blend_vals = np.nanmean(stack, axis=0)
    blend = pd.DataFrame(blend_vals, index=neut["northbound"].index, columns=neut["northbound"].columns)
    # blend 自身也对残差(已去风格)无需再中性化;直接量 IC

    print("=" * 74)
    print("中性化多因子 diversifier blend 体检(去 size+流动性+动量后)")
    print(f"窗口 {START} → cutoff {CUTOFF} → {END} | universe=all | 等权 z-blend")
    print("=" * 74)
    print("① 各信号 三重中性化残差 IC(full / IS / OOS):")
    for n in SIGNALS:
        print(f"  {n:12s}: {_ic(neut[n],fwd,START,END)}  IS {_ic(neut[n],fwd,START,CUTOFF)}  OOS {_ic(neut[n],fwd,CUTOFF,END)}")
    print(f"  {'BLEND':12s}: {_ic(blend,fwd,START,END)}  IS {_ic(blend,fwd,START,CUTOFF)}  OOS {_ic(blend,fwd,CUTOFF,END)}")

    # 覆盖度(每日有效股票数中位)
    print("\n② 覆盖度(逐日有效股票数中位):")
    for n in SIGNALS:
        print(f"  {n:12s}: {int(neut[n].notna().sum(axis=1).median())}")
    print(f"  {'BLEND':12s}: {int(blend.notna().sum(axis=1).median())}")

    # 两两相关(逐截面 spearman 均值)——低=真分散
    print("\n③ 信号间残差相关(逐截面 spearman 均值,低=真分散):")
    names = list(SIGNALS)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            print(f"  {names[i]} × {names[j]}: {_pair_corr(neut[names[i]], neut[names[j]]):+.3f}")

    print("\n诚实边界:L0,非 alpha。blend 若 ICIR 显著 > 最佳单因子且互相关低 = 分散收益真;入册走 diversifier 轨。")


def _pair_corr(a: pd.DataFrame, b: pd.DataFrame) -> float:
    cs = []
    for t in a.index:
        x, y = a.loc[t], b.loc[t]
        m = x.notna() & y.notna()
        if m.sum() >= 30:
            cs.append(x[m].rank().corr(y[m].rank()))
    return float(np.nanmean(cs)) if cs else float("nan")


if __name__ == "__main__":
    main()
