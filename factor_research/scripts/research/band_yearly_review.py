"""Band timing 历年详细回测.

用法:
  cd /Users/kiki/astcok/factor_research
  /opt/homebrew/bin/python3 scripts/research/band_yearly_review.py
"""
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
os.chdir(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, str(Path.cwd()))

import numpy as np

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.small_cap import small_cap_factor, small_cap_timing
from strategies.small_cap import build_rebalance_weights, load_price_panels


def main():
    close, volume, amount = load_price_panels("2010-01-01")
    factor = small_cap_factor(amount, window=60)
    scheduled = build_rebalance_weights(factor, close, top_n=25, rebalance_days=20)
    pt_timing, _, timing_dist = small_cap_timing(close, amount, ma_window=16)

    # Band
    dist_s = timing_dist.shift(1).reindex(pt_timing.index)
    band_exp = ((1 + dist_s * 8).clip(0, 1.5) * (dist_s > 0)).fillna(0.0)
    # Binary
    bin_timing = pt_timing.astype(float).reindex(band_exp.index).fillna(0.0)

    prices = PricePanel(close=close, volume=None, amount=amount)
    cost = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065)

    for mode_label, timing, exp_cap, lev in [
        ("Band", band_exp, 1.5, 1.0),
        ("Binary", bin_timing, 1.0, 1.25),
    ]:
        lev_desc = "dynamic 0-1.5x" if mode_label == "Band" else "fixed 1.25x"
        print(f"\n{'='*90}")
        print(f"  {mode_label} timing ({lev_desc})")
        print(f"{'='*90}")

        cfg = BacktestConfig(start="2018-01-01", cost=cost, leverage=lev)
        engine = BacktestEngine(prices=prices, config=cfg)
        signal = Signal(weights=scheduled, timing=timing, exposure_cap=exp_cap,
                        family="illiquidity", version="v1.0")
        result = engine.run(signal)
        r = result.returns.loc["2018-01-01":].dropna()
        t = timing.loc["2018-01-01":]

        header = f"  {'Year':<6} {'Return':>8} {'MaxDD':>8} {'Vol':>7} {'Sharpe':>6} {'Invest%':>7} {'Turn':>6} {'AvgExp':>7}"
        print(header)
        print("  " + "-" * 65)

        for yr in sorted(set(r.index.year)):
            mask = r.index.year == yr
            grp = r[mask]
            if len(grp) < 50:
                continue
            ann = float(grp.mean() * 252)
            vol = float(grp.std() * np.sqrt(252))
            sh = (ann - 0.025) / vol if vol > 0 else 0
            cum = (1 + grp).cumprod()
            dd = float((cum / cum.cummax() - 1).min())
            ret = float((1 + grp).prod() - 1)
            t_yr = t.loc[str(yr)]
            invested = (t_yr > 0).mean() * 100
            to = result.detail["turnover"].loc[str(yr)].mean() * 252
            avg_exp = t_yr.mean()
            print(f"  {yr:<6} {ret:>+7.1%} {dd:>+7.1%} {vol:>6.0%} {sh:>5.2f} {invested:>6.0f}% {to:>5.1f}x {avg_exp:>6.2f}")

        # 全期
        ann = float(r.mean() * 252)
        vol = float(r.std() * np.sqrt(252))
        sh = (ann - 0.025) / vol if vol > 0 else 0
        cum = (1 + r).cumprod()
        dd = float((cum / cum.cummax() - 1).min())
        cal = ann / abs(dd) if dd < 0 else 0
        ret = float((1 + r).prod() - 1)
        invested = (t > 0).mean() * 100
        to = result.detail["turnover"].mean() * 252
        nav = cum.iloc[-1] * 100
        print("  " + "-" * 65)
        print(f"  {'All':<6} {ret:>+7.1%} {dd:>+7.1%} {vol:>6.0%} {sh:>5.2f} {invested:>6.0f}% {to:>5.1f}x {t.mean():>6.2f}")
        print(f"  Annualized={ann:+.2%}  Calmar={cal:.2f}  NAV={nav:.0f}wan (100wan start)")

        # 月度热力
        print("\n  Monthly returns:")
        for yr in sorted(set(r.index.year)):
            row = f"  {yr} "
            for m in range(1, 13):
                mm = (r.index.year == yr) & (r.index.month == m)
                if mm.sum() > 0:
                    v = (1 + r[mm]).prod() - 1
                    row += f" {v:>+5.1%}"
                else:
                    row += f" {'':>5}"
            yr_ret = (1 + r[r.index.year == yr]).prod() - 1
            row += f"  | {yr_ret:+.1%}"
            print(row)

    print()


if __name__ == "__main__":
    main()
