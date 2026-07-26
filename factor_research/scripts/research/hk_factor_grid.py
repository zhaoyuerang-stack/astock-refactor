"""HK 因子工程 grid — 找单 Sharpe ≥ 0.5 的 HK 策略。

前置: hk_cross_market_audit.py 实测 HK small-cap(MA16) 仅 sharpe 0.30。
HK universe 仅 91 只 (vs A 股 5207),top_n / 因子需要重新设计。

Grid:
  · 因子: size60 / mom60 / mom252 / -volatility60 / reverse20 (反转)
  · top_n: 10 / 15 / 20 / 30  (HK universe 91, 即 11%/16%/22%/33%)
  · rebal: 20D / 60D (机构主导, 趋势可能更长)
  · timing: none / Binary MA16 / Band MA16
  · leverage: 1.0 / 1.25

判定: sharpe ≥ 0.5 且 maxdd ≥ -35% 入"HK 候选池", 进一步测组合贡献。
"""
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.utils import mad_clip, safe_zscore

HK_DIR = ROOT / "data_lake" / "price" / "hk_daily"


def load_hk_panels():
    closes, volumes = {}, {}
    for fp in HK_DIR.glob("*.parquet"):
        try:
            df = pd.read_parquet(fp)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            closes[fp.stem] = df["close"]
            volumes[fp.stem] = df["volume"]
        except Exception:
            continue
    close = pd.DataFrame(closes).sort_index()
    volume = pd.DataFrame(volumes).sort_index()
    amount = volume * close
    return close, volume, amount


# ── HK 因子集合 ──
def f_size(close, volume, amount, n=60):
    return safe_zscore(mad_clip(-np.log(amount.rolling(n).mean() + 1)))


def f_mom(close, volume, amount, n=60, skip=20):
    """N 日 momentum, 跳过最近 skip 日 (规避短反转)."""
    return safe_zscore(mad_clip(close.shift(skip) / close.shift(n + skip) - 1))


def f_low_vol(close, volume, amount, n=60):
    ret = close.pct_change(fill_method=None)
    return safe_zscore(mad_clip(-ret.rolling(n).std()))


def f_reverse(close, volume, amount, n=20):
    """短期反转: 负 N 日累计 return."""
    return safe_zscore(mad_clip(-(close / close.shift(n) - 1)))


def f_illiquidity(close, volume, amount, n=20):
    """Amihud."""
    ret = close.pct_change(fill_method=None).abs()
    illiq = (ret / (amount.replace(0, np.nan) + 1)).rolling(n).mean()
    return safe_zscore(mad_clip(illiq))


# ── HK timing ──
def hk_binary_timing(close, amount, ma_window=16):
    ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    small_mask = amount.rolling(20).mean().rank(axis=1, pct=True) < 0.5
    small_idx = (ret * small_mask).sum(axis=1) / small_mask.sum(axis=1)
    small_nav = (1 + small_idx.fillna(0)).cumprod()
    timing = (small_nav > small_nav.rolling(ma_window).mean()).shift(1, fill_value=False).astype(float)
    dist = small_nav / small_nav.rolling(ma_window).mean() - 1
    return timing, dist


def hk_band_timing(dist, slope=8, cap=1.5):
    dc = dist.clip(-0.5, 0.5)
    raw = (1.0 + dc * slope).clip(0.0, cap)
    above = (dc > 0).astype(float)
    return (raw * above).shift(1).fillna(0).astype(float)


# ── Weights ──
def build_weights(factor, close, top_n, rebal):
    fdates = factor.dropna(how="all").index.intersection(close.index)
    if len(fdates) < 50:
        return {}
    w = {}
    for rd in list(fdates[::rebal]):
        pos = close.index.get_loc(rd)
        if pos + 1 >= len(close.index): continue
        eff = close.index[pos + 1]
        fv = factor.loc[rd].dropna()
        act = close.loc[rd].dropna().index
        fv = fv.reindex(act).dropna()
        if len(fv) < top_n: continue
        w[eff] = pd.Series(1.0 / top_n, index=fv.nlargest(top_n).index)
    return w


def run_strategy(factor, close, volume, amount, top_n, rebal, timing, leverage, exposure_cap=1.0):
    weights = build_weights(factor, close, top_n, rebal)
    if not weights:
        return None
    prices = PricePanel(close=close, volume=volume, amount=amount)
    engine = BacktestEngine(prices=prices, config=BacktestConfig(
        start=str(close.index[0].date()),
        cost=CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065),
        leverage=leverage))
    return engine.run(Signal(weights=weights, timing=timing, exposure_cap=exposure_cap)).returns.dropna()


def metrics(ret):
    r = ret.dropna()
    if len(r) < 50: return None
    annual = float(r.mean() * 252)
    vol = float(r.std() * np.sqrt(252))
    sh = annual / (vol + 1e-9)
    cum = (1 + r).cumprod()
    mdd = float((cum / cum.cummax() - 1).min())
    cal = annual / abs(mdd) if mdd < 0 else 0.0
    return {"annual": annual, "vol": vol, "sharpe": sh, "maxdd": mdd, "calmar": cal}


def main():
    print("Loading HK panels...")
    close, volume, amount = load_hk_panels()
    print(f"  {close.shape}")

    # 预算: 5 factor × 4 top_n × 2 rebal × 3 timing × 2 lev = 240, 太多
    # 精简到: 5 因子 × 3 配置 (default / wide top_n / band timing) = 15
    factors = {
        "size60": f_size(close, volume, amount, n=60),
        "mom60_skip20": f_mom(close, volume, amount, n=60, skip=20),
        "mom252_skip20": f_mom(close, volume, amount, n=252, skip=20),
        "low_vol60": f_low_vol(close, volume, amount, n=60),
        "reverse20": f_reverse(close, volume, amount, n=20),
        "illiq20": f_illiquidity(close, volume, amount, n=20),
    }

    binary_timing, dist = hk_binary_timing(close, amount, ma_window=16)
    band_timing = hk_band_timing(dist, slope=8, cap=1.5)

    configs = [
        # (label, top_n, rebal, timing, leverage, exposure_cap)
        ("base_T15_R20_MA16_125x", 15, 20, binary_timing, 1.25, 1.0),
        ("wide_T30_R20_MA16_125x", 30, 20, binary_timing, 1.25, 1.0),
        ("slow_T15_R60_MA16_125x", 15, 60, binary_timing, 1.25, 1.0),
        ("notiming_T15_R20_125x", 15, 20, None, 1.25, 1.0),
        ("band_T15_R20_10x", 15, 20, band_timing, 1.0, 1.5),
    ]

    print(f"\n{'factor':<18} {'config':<25} {'ann':>7} {'sh':>5} {'mdd':>7} {'cal':>6}")
    print("-" * 78)

    promising = []  # sharpe >= 0.5
    for fname, factor in factors.items():
        for cfg_label, top_n, rebal, timing, lev, exp_cap in configs:
            try:
                r = run_strategy(factor, close, volume, amount, top_n, rebal, timing, lev, exp_cap)
                if r is None:
                    continue
                m = metrics(r)
                if m is None:
                    continue
                mark = "  ⭐" if m["sharpe"] >= 0.5 else ""
                print(f"{fname:<18} {cfg_label:<25} {m['annual']:+7.1%} {m['sharpe']:+5.2f} "
                      f"{m['maxdd']:+7.1%} {m['calmar']:+6.2f}{mark}")
                if m["sharpe"] >= 0.5 and m["maxdd"] >= -0.40:
                    promising.append((m["sharpe"], fname, cfg_label, r, m))
            except Exception as e:
                print(f"{fname:<18} {cfg_label:<25} ERROR: {type(e).__name__}")

    print(f"\n{'='*60}")
    print(f"  HK 候选池 (sharpe >= 0.5 且 maxdd >= -40%): {len(promising)}")
    print(f"{'='*60}")
    promising.sort(reverse=True)
    for sh, fname, cfg, _, m in promising[:5]:
        print(f"  ⭐ {fname} {cfg}: sh={sh:.2f}, ann={m['annual']:+.1%}, "
              f"mdd={m['maxdd']:+.1%}, cal={m['calmar']:+.2f}")

    if not promising:
        print("  (无候选; HK universe 太小或 alpha 弱)")
        return

    # 组合层: best HK 候选 + A 股 ACTIVE
    print(f"\n{'='*60}")
    print("  组合测试: A 股 ACTIVE + 最佳 HK")
    print(f"{'='*60}")
    from portfolio.composer import compose
    from portfolio.composer import metrics as pm
    from portfolio.strategy_runners import run_active
    a_returns = run_active(start="2018-01-01")

    # Baseline: A 股 ACTIVE only
    base_rp, _ = compose(a_returns, method="risk_parity")
    mb = pm(base_rp)
    print(f"  baseline (A only risk_parity): "
          f"ann={mb['annual']:+.1%} sh={mb['sharpe']:+.2f} "
          f"mdd={mb['maxdd']:+.1%} cal={mb['calmar']:+.2f}")

    best_sh, best_fname, best_cfg, best_r, best_m = promising[0]
    combo = {**a_returns, f"HK_{best_fname}_{best_cfg}": best_r}
    combo_rp, _ = compose(combo, method="risk_parity")
    mc = pm(combo_rp)
    print(f"  + best HK ({best_fname} {best_cfg}): "
          f"ann={mc['annual']:+.1%} sh={mc['sharpe']:+.2f} "
          f"mdd={mc['maxdd']:+.1%} cal={mc['calmar']:+.2f}")
    print(f"  Δ: sh={mc['sharpe']-mb['sharpe']:+.3f} "
          f"cal={mc['calmar']-mb['calmar']:+.3f} "
          f"mdd={mc['maxdd']-mb['maxdd']:+.2%}")
    if mc['sharpe'] > mb['sharpe']:
        print("  ✅ HK 进入组合改善 Sharpe — Cross-market 突破成功")
    elif mc['calmar'] > mb['calmar']:
        print("  ◐ HK 改善 Calmar 不改善 Sharpe (risk-adjusted 视角好)")
    else:
        print("  ❌ HK 仍拖累组合")


if __name__ == "__main__":
    main()
