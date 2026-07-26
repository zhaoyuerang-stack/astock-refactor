"""Band timing: PureTrend dist-based continuous position sizing.

Replaces binary PT (on/off) with continuous exposure:
  exposure = (1 + dist × 8) × I(dist > 0), clamped to [0, 1.5]

dist = small_nav / MA16(small_nav) - 1

Usage:
  python3 scripts/research/band_timing_test.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, PricePanel, Signal
from factors.small_cap import small_cap_timing
from factors.utils import mad_clip, safe_zscore
from strategies.small_cap import load_price_panels


# ── illiquidity factor ──
class Illiq:
    def __init__(self, w=20):
        self.w = w

    def __call__(self, c, v, a, d):
        r = c.pct_change(fill_method=None).abs().replace([np.inf, -np.inf], np.nan)
        return safe_zscore(mad_clip((r / (a + 1)).rolling(self.w).mean()))


# ── Weight builder ──
def build_weights(factor, close, n=25, reb=20):
    fd = factor.dropna(how="all").index.intersection(close.index)
    if len(fd) < 50:
        return {}
    w = {}
    for rd in list(fd[::reb]):
        pos = close.index.get_loc(rd)
        if pos + 1 >= len(close.index):
            continue
        eff = close.index[pos + 1]
        fv = factor.loc[rd].dropna()
        act = close.loc[rd].dropna().index
        fv = fv.reindex(act).dropna()
        if len(fv) < n:
            continue
        w[eff] = pd.Series(1.0 / n, index=fv.nlargest(n).index)
    return w


# ── Timing builders ──
def build_binary(close, amount):
    pt, _, _ = small_cap_timing(close, amount, ma_window=16)
    return pt.astype(float)


def build_band(close, amount):
    _, _, dist = small_cap_timing(close, amount, ma_window=16)
    dist_clipped = dist.clip(lower=-0.5, upper=0.5)
    exposure_raw = 1.0 + dist_clipped * 8.0
    exposure_clipped = exposure_raw.clip(lower=0.0, upper=1.5)
    above_ma = (dist_clipped > 0).astype(float)
    exposure = exposure_clipped * above_ma
    return exposure.shift(1).fillna(0.0)


# ── Run ──
def main():
    print("Loading data...", flush=True)
    close, volume, amount = load_price_panels("2010-01-01")
    td = close.index

    factor = Illiq(20)(close, volume, amount, td)
    weights = build_weights(factor, close, n=25, reb=20)
    prices = PricePanel(close=close, volume=volume, amount=amount)

    timing_bin = build_binary(close, amount)
    timing_band = build_band(close, amount)

    print(f"\n{'='*75}")
    print("  BAND vs BINARY — Three Segments")
    print(f"{'='*75}")

    for label, start, end in [
        ("IS  2018-2022", "2018-01-01", "2022-12-31"),
        ("OOS 2023-2026", "2023-01-01", "2026-12-31"),
        ("压力 2010-2017", "2010-01-01", "2017-12-31"),
    ]:
        print(f"\n  [{label}]")
        print(f"  {'Method':<20} {'Annual':>8} {'MaxDD':>8} {'Sharpe':>7} {'Calmar':>7}")

        mask = (td >= pd.Timestamp(start)) & (td <= pd.Timestamp(end))
        c_seg = close.loc[mask]
        a_seg = amount.loc[mask]
        v_seg = volume.loc[mask]
        t_bin = timing_bin.loc[mask]
        t_band = timing_band.loc[mask]
        w_seg = {dt: wgt for dt, wgt in weights.items() if dt in c_seg.index}

        for name, timing, lev in [("Binary PT 1.25x", t_bin, 1.25),
                                   ("Band 1.0x", t_band, 1.0)]:
            cfg = BacktestConfig(start=start, leverage=lev)
            r = BacktestEngine(
                prices=PricePanel(close=c_seg, volume=v_seg, amount=a_seg),
                config=cfg,
            ).run(Signal(weights=w_seg, timing=timing))
            m = r.metrics
            print(f"  {name:<20} {m['annual']:>+7.1%} {m['maxdd']:>+7.1%} "
                  f"{m['sharpe']:>+6.2f} {m['calmar']:>+6.2f}")

    # ── Full period + yearly ──
    print(f"\n{'='*75}")
    print("  FULL PERIOD + YEARLY (2010-2026)")
    print(f"{'='*75}")

    cfg_full = BacktestConfig(start="2010-01-01", leverage=1.25)
    r_bin = BacktestEngine(prices=prices, config=cfg_full).run(
        Signal(weights=weights, timing=timing_bin))

    cfg_band = BacktestConfig(start="2010-01-01", leverage=1.0)
    r_band = BacktestEngine(prices=prices, config=cfg_band).run(
        Signal(weights=weights, timing=timing_band))

    m_bin = r_bin.metrics
    m_band = r_band.metrics
    print(f"\n  Binary PT 1.25x:  ann={m_bin['annual']:+.1%}  dd={m_bin['maxdd']:+.1%}  "
          f"sh={m_bin['sharpe']:.2f}  calmar={m_bin['calmar']:.2f}")
    print(f"  Band 1.0x:        ann={m_band['annual']:+.1%}  dd={m_band['maxdd']:+.1%}  "
          f"sh={m_band['sharpe']:.2f}  calmar={m_band['calmar']:.2f}")

    # Yearly
    ret_bin = r_bin.returns
    ret_band = r_band.returns

    def yearly(r):
        d = {}
        for y, g in r.groupby(r.index.year):
            d[y] = (1 + g).prod() - 1
        return d

    yr_bin = yearly(ret_bin)
    yr_band = yearly(ret_band)

    print(f"\n  {'Year':<6} {'Binary':>8} {'Band':>8} {'Delta':>8} {'':<6}")
    for y in range(2010, 2027):
        b = yr_bin.get(y, 0)
        bd = yr_band.get(y, 0)
        win = "WIN" if bd > b else ""
        print(f"  {y:<6} {b:>+7.1%} {bd:>+7.1%} {bd - b:>+7.1%}  {win}")


if __name__ == "__main__":
    main()
