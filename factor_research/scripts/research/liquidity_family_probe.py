"""流动性因子族体检(L0,非 alpha)。外部实证称流动性族最强预测;但 Amihud 非流动性已是
本系统核心(illiquidity 策略 6 版,Sharpe 1.06)。故真问题不是"流动性预测吗"(已知是),而是:

  ① 零交易天数 / 零收益天数 / 流动性波动率 这些**没挖的维度**,对现有 Amihud+size 核心有无**增量**;
  ② 还是只是同一个**小盘代理**(corr(size) 高 → 加深"小盘坍缩"+ 2024 拥挤雷)。

关键列:残差 IC 去 [size+turnover+**amihud**](增量于核心)+ 截面 corr(size)(坍缩风险)。
诚实边界:L0,非 alpha。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)

import numpy as np  # noqa: E402

from factors.utils import mad_clip, safe_zscore  # noqa: E402
from lake.load_lake import load_prices  # noqa: E402
from scripts.research import signal_source_probe as P  # noqa: E402

START, CUTOFF, END = "2018-01-01", "2022-12-31", "2024-12-31"


def _z(df):
    return safe_zscore(mad_clip(df.replace([np.inf, -np.inf], np.nan)))


def main():
    px = load_prices(start="2010-01-01")
    close, volume, amount = px["close"], px["volume"], px["amount"]
    listed = close.notna()

    # ── 候选(各自定向:高=更illiq→流动性溢价→预期正 IC)──
    win = 60
    zero_vol = _z((volume.fillna(0).eq(0) & listed).rolling(win).sum())           # Liu 零交易量天数
    ret = close.pct_change(fill_method=None)
    zero_ret = _z((ret.abs().lt(1e-6) & listed).rolling(win).sum())               # Lesmond 零收益天数
    # 流动性波动率:turnover 滚动 std(用 daily_basic turnover)
    ctl = P._load_controls(close)
    turn = ctl["liquidity"]
    liq_vol = _z(turn.rolling(win).std())
    # Amihud benchmark(现有核心):|ret|/amount 的窗均
    amihud_raw = (ret.abs() / (amount.replace(0, np.nan) + 1.0)).rolling(win).mean()
    amihud = _z(amihud_raw)

    cand = {"zero_vol_days": zero_vol, "zero_ret_days": zero_ret,
            "liq_vol": liq_vol, "amihud(核心benchmark)": amihud}

    # ── IC 框架 ──
    rb = P._monthly_rebalance(close, START, END)
    fwd = P._forward_returns(close, rb)
    rb2 = [t for t in rb if t in fwd.index]
    size = ctl["size"].reindex(rb2)
    liq = ctl["liquidity"].reindex(rb2)
    mom = ctl["momentum"].reindex(rb2)
    am = amihud.reindex(rb2)

    def ic(fac, lo, hi):
        d = P._seg_ic(fac, fwd, lo, hi)
        return (round(d["ic"], 4), round(d["icir"], 2)) if d else None

    def corr_size(fac):
        cs = []
        for t in rb2:
            x, y = fac.loc[t], size.loc[t]
            m = x.notna() & y.notna()
            if m.sum() >= 100:
                cs.append(x[m].rank().corr(y[m].rank()))
        return float(np.nanmean(cs)) if cs else float("nan")

    print("=" * 96)
    print("流动性因子族体检(去风格残差 IC + 坍缩风险)| universe=all | IS 2018-22 / OOS 2023-24")
    print("=" * 96)
    print(f"{'因子':22s} {'原始IC(full)':>14} {'残差[size+turn]':>16} {'残差[+amihud]':>16} {'corr(size)':>11} {'OOS残[+am]':>12}")
    for name, fac in cand.items():
        f = fac.reindex(rb2)
        raw = ic(f, START, END)
        r_sl = P._neutralize(f, [size, liq])
        r_sla = P._neutralize(f, [size, liq, mom, am])
        print(f"{name:22s} {str(raw):>14} {str(ic(r_sl,START,END)):>16} "
              f"{str(ic(r_sla,START,END)):>16} {corr_size(f):>+11.3f} {str(ic(r_sla,CUTOFF,END)):>12}")

    print("\n判读:残差[+amihud]≈0 = 只是现有 Amihud/size 的代理(无增量,且 corr(size) 高=加深坍缩);")
    print("     残差[+amihud] 显著且 corr(size) 低 = 真新维度。诚实边界:L0,非 alpha,入册走 workflow。")


if __name__ == "__main__":
    main()
