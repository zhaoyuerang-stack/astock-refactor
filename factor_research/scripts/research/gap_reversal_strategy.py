"""New strategy round 2: build on what works, or find genuine standalone alpha.

Phase 1: Pure gap/reversal/range → all failed (no standalone alpha)
Phase 2: Test:
  D. Small-cap × momentum filter (only buy small caps with positive recent returns)
  E. Small-cap × earnings growth filter (only buy small caps with positive profit growth)
  F. Standalone earnings momentum (net_profit_yoy, no size factor)
  G. Standalone revenue growth (revenue_yoy, no size factor)

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/gap_reversal_strategy.py
"""
import warnings; warnings.filterwarnings("ignore")
import os
import sys
from pathlib import Path

ROOT = Path("/Users/kiki/astcok/factor_research").resolve()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, PricePanel, Signal
from factors.small_cap import small_cap_factor, small_cap_timing
from factors.utils import mad_clip, safe_zscore
from lake.load_lake import load_fundamental_panel
from strategies.small_cap import load_price_panels

OUT = ROOT / "reports" / "research"
OUT.mkdir(parents=True, exist_ok=True)

FREQ = 20
TOP_N = 25


def build_rebalance_weights(factor, close, top_n=TOP_N, rebalance_days=FREQ):
    fdates = factor.dropna(how="all").index.intersection(close.index)
    if len(fdates) < 100:
        return {}
    weights = {}
    for rd in list(fdates[::rebalance_days]):
        pos = close.index.get_loc(rd)
        if pos + 1 >= len(close.index):
            continue
        effective = close.index[pos + 1]
        f = factor.loc[rd].dropna()
        active = close.loc[rd].dropna().index
        f = f.reindex(active).dropna()
        if len(f) < top_n:
            continue
        weights[effective] = pd.Series(1.0 / top_n, index=f.nlargest(top_n).index)
    return weights


def run_test(label, close, volume, amount, factor, use_timing=False):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}", flush=True)

    prices = PricePanel(close=close, volume=volume, amount=amount)
    scheduled = build_rebalance_weights(factor, close)

    timing = None
    if use_timing:
        timing, _, _ = small_cap_timing(close, amount, ma_window=16)

    engine = BacktestEngine(
        prices=prices,
        config=BacktestConfig(start="2018-01-01", leverage=1.25),
    )
    signal = Signal(weights=scheduled, timing=timing, family="exploration", version="v0")
    result = engine.run(signal)

    m = result.metrics
    print(f"  年化={m['annual']:+.2%} 回撤={m['maxdd']:.2%} 夏普={m['sharpe']:.2f} "
          f"卡玛={m['calmar']:.2f} 达标={'✅' if m['hit'] else '❌'}", flush=True)
    print(f"  换手={result.detail['turnover'].mean()*252:.1f}x "
          f"成本拖累={result.detail['cost'].mean()*252:.2%}", flush=True)
    yearly = result.yearly_returns
    print("  分年度:", " ".join(f"{y}:{r:+.0%}" for y, r in yearly.items()), flush=True)
    return result


def main():
    print("Loading data...", flush=True)
    close, volume, amount = load_price_panels("2018-01-01")
    print(f"  close: {close.shape[1]} stocks × {close.shape[0]} days [{close.index[0].date()}~{close.index[-1].date()}]",
          flush=True)

    # ── Baseline: Small-cap v2.0 ──
    sc_factor_raw = small_cap_factor(amount, window=60)
    sc_timing, _, _ = small_cap_timing(close, amount, ma_window=16)
    sc_weights = build_rebalance_weights(sc_factor_raw, close)

    sc_prices = PricePanel(close=close, volume=volume, amount=amount)
    sc_engine = BacktestEngine(prices=sc_prices, config=BacktestConfig(start="2018-01-01", leverage=1.25))
    sc_signal = Signal(weights=sc_weights, timing=sc_timing, family="small-cap-size", version="v2.0")
    sc_result = sc_engine.run(sc_signal)
    m = sc_result.metrics
    print(f"\n  BASELINE small-cap v2.0: 年化={m['annual']:+.2%} 回撤={m['maxdd']:.2%} "
          f"夏普={m['sharpe']:.2f} 卡玛={m['calmar']:.2f}", flush=True)

    # ── Load fundamental data ──
    print("\nLoading fundamental data...", flush=True)
    trade_dates = close.index
    fund = load_fundamental_panel(trade_dates, codes=None,
                                   fields=["net_profit_yoy", "revenue_yoy", "roe", "gross_margin"])
    fund_npy = fund.get("net_profit_yoy", pd.DataFrame())
    fund_rev = fund.get("revenue_yoy", pd.DataFrame())
    fund_roe = fund.get("roe", pd.DataFrame())
    fund_gm = fund.get("gross_margin", pd.DataFrame())

    # Align fundamental panels to price dates
    if not fund_npy.empty:
        fund_npy = fund_npy.reindex(trade_dates).ffill()
        fund_rev = fund_rev.reindex(trade_dates).ffill() if not fund_rev.empty else fund_rev
        fund_roe = fund_roe.reindex(trade_dates).ffill() if not fund_roe.empty else fund_roe
        fund_gm = fund_gm.reindex(trade_dates).ffill() if not fund_gm.empty else fund_gm
        print(f"  net_profit_yoy: {fund_npy.dropna(how='all').shape[0]} days with data", flush=True)
        print(f"  revenue_yoy: {fund_rev.dropna(how='all').shape[0]} days with data", flush=True)
        print(f"  roe: {fund_roe.dropna(how='all').shape[0]} days with data", flush=True)
    else:
        print("  WARNING: no fundamental data loaded!", flush=True)
        return

    # ═══════════════════════════════════════════════════════════
    # D. Small-cap × Price Momentum filter
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'#'*60}")
    print("  D. SMALL-CAP × MOMENTUM FILTER")
    print(f"{'#'*60}", flush=True)

    for mom_window in [20, 40, 60]:
        mom = close.pct_change(mom_window, fill_method=None)
        mom_z = safe_zscore(mad_clip(mom))

        # Combined: size + momentum, equally weighted
        combined = safe_zscore(mad_clip(sc_factor_raw + mom_z))
        run_test(f"Size + Mom({mom_window}d)", close, volume, amount, combined, use_timing=False)
        run_test(f"Size + Mom({mom_window}d) + PureTrend", close, volume, amount, combined, use_timing=True)

        # Alternative: use momentum as a HARD FILTER (only keep small caps with positive mom)
        mom_filter = (mom > 0).astype(float)  # 1 if positive momentum, 0 otherwise
        filtered = safe_zscore(mad_clip(sc_factor_raw * mom_filter))
        run_test(f"Size × Mom>{0}({mom_window}d)", close, volume, amount, filtered, use_timing=False)
        run_test(f"Size × Mom>{0}({mom_window}d) + PureTrend", close, volume, amount, filtered, use_timing=True)

    # ═══════════════════════════════════════════════════════════
    # E. Small-cap × Earnings Growth filter
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'#'*60}")
    print("  E. SMALL-CAP × EARNINGS GROWTH FILTER")
    print(f"{'#'*60}", flush=True)

    if not fund_npy.empty:
        npy_z = safe_zscore(mad_clip(fund_npy))
        rev_z = safe_zscore(mad_clip(fund_rev)) if not fund_rev.empty else None
        roe_z = safe_zscore(mad_clip(fund_roe)) if not fund_roe.empty else None

        # Size + earnings growth, equally weighted
        combined = safe_zscore(mad_clip(sc_factor_raw + npy_z))
        run_test("Size + NetProfit YoY", close, volume, amount, combined, use_timing=False)
        run_test("Size + NetProfit YoY + PureTrend", close, volume, amount, combined, use_timing=True)

        # Hard filter: only small caps with positive earnings growth
        npy_pos = (fund_npy > 0).astype(float)
        filtered = safe_zscore(mad_clip(sc_factor_raw * npy_pos))
        run_test("Size × NPY>0", close, volume, amount, filtered, use_timing=False)
        run_test("Size × NPY>0 + PureTrend", close, volume, amount, filtered, use_timing=True)

        # Triple combo: size + earnings growth + revenue growth
        if rev_z is not None:
            triple = safe_zscore(mad_clip(sc_factor_raw + npy_z + rev_z))
            run_test("Size + NPY + Rev YoY + PureTrend", close, volume, amount, triple, use_timing=True)

    # ═══════════════════════════════════════════════════════════
    # F. Standalone Earnings Momentum (no size factor)
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'#'*60}")
    print("  F. STANDALONE EARNINGS MOMENTUM")
    print(f"{'#'*60}", flush=True)

    if not fund_npy.empty:
        for label, fund_factor in [
            ("NetProfit YoY", npy_z),
            ("Revenue YoY", rev_z if rev_z is not None else None),
            ("ROE", roe_z if roe_z is not None else None),
        ]:
            if fund_factor is None:
                continue
            run_test(f"{label} only", close, volume, amount, fund_factor, use_timing=False)
            run_test(f"{label} + PureTrend", close, volume, amount, fund_factor, use_timing=True)

        # Combined fundamental (no size)
        if rev_z is not None and roe_z is not None:
            combo_fund = safe_zscore(mad_clip(npy_z + rev_z + roe_z))
            run_test("NPY + Rev + ROE (no size)", close, volume, amount, combo_fund, use_timing=False)
            run_test("NPY + Rev + ROE + PureTrend", close, volume, amount, combo_fund, use_timing=True)

    # ═══════════════════════════════════════════════════════════
    # G. Fundamental × Positive Momentum (no size)
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'#'*60}")
    print("  G. EARNINGS GROWTH × MOMENTUM (NO SIZE)")
    print(f"{'#'*60}", flush=True)

    if not fund_npy.empty:
        mom60_z = safe_zscore(mad_clip(close.pct_change(60, fill_method=None)))
        combo = safe_zscore(mad_clip(npy_z + mom60_z))
        run_test("NPY + Mom(60d) no size", close, volume, amount, combo, use_timing=False)
        run_test("NPY + Mom(60d) + PureTrend", close, volume, amount, combo, use_timing=True)

    # ── Correlation summary ──
    print(f"\n{'#'*60}")
    print("  CORRELATION to Small-cap v2.0")
    print(f"{'#'*60}", flush=True)

    tests = []
    if not fund_npy.empty:
        tests.append(("NPY only + Trend", npy_z))
    mom60_z = safe_zscore(mad_clip(close.pct_change(60, fill_method=None)))
    tests.append(("Mom(60d) only + Trend", mom60_z))
    tests.append(("Size + Mom(60d) + Trend", safe_zscore(mad_clip(sc_factor_raw + mom60_z))))

    for label, factor in tests:
        w = build_rebalance_weights(factor, close)
        t, _, _ = small_cap_timing(close, amount, ma_window=16)
        engine = BacktestEngine(prices=PricePanel(close=close, volume=volume, amount=amount),
                                config=BacktestConfig(start="2018-01-01", leverage=1.25))
        r = engine.run(Signal(weights=w, timing=t, family="exploration", version="v0"))
        common = r.returns.index.intersection(sc_result.returns.index)
        corr = r.returns.loc[common].corr(sc_result.returns.loc[common])
        print(f"  {label}: daily corr vs baseline = {corr:.3f}", flush=True)

    print("\nDone.", flush=True)


if __name__ == "__main__":
    main()
