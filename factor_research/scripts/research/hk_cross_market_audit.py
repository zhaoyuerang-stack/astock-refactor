"""HK 港股 cross-market 探索 — STATUS 结论 #3 "跨资产真正多元化" 的实证。

设计:
  1. 加载 hk_daily 111 只港股 (2018-2026)
  2. 跑 HK size 因子 (与 A 股 small_cap 同公式) + HK 自己的 timing
  3. 拿 hk_returns
  4. 算与 A 股 ACTIVE (illiquidity + small-cap) 的 corr + MI
  5. 评估: 是否真正独立 (corr < 0.5)?
  6. 如果是 → 港股 LIVE 候选, 接入 portfolio
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
from portfolio.strategy_runners import run_active

HK_DIR = ROOT / "data_lake" / "price" / "hk_daily"


def load_hk_panels():
    """Load all HK stocks into (close, volume, amount) panels."""
    print(f"Loading HK daily from {HK_DIR}...")
    closes = {}
    volumes = {}
    for fp in HK_DIR.glob("*.parquet"):
        code = fp.stem
        try:
            df = pd.read_parquet(fp)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            closes[code] = df["close"]
            volumes[code] = df["volume"]
        except Exception:
            continue
    close = pd.DataFrame(closes).sort_index()
    volume = pd.DataFrame(volumes).sort_index()
    # HK 港股没有 raw_close ↔ 复权口径概念 (港股 ak 已是后复权)
    # amount = volume × close (粗略,无 raw)
    amount = volume * close
    print(f"  {close.shape} ({close.shape[1]} HK stocks, {close.shape[0]} days)")
    return close, volume, amount


def hk_small_cap_factor(amount, window=60):
    """与 A 股 small_cap_factor 同公式: -log(rolling mean amount)."""
    return safe_zscore(mad_clip(-np.log(amount.rolling(window).mean() + 1)))


def hk_small_cap_timing(close, amount, ma_window=16):
    """与 A 股 small_cap_timing 同公式但用 HK universe."""
    ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    small_mask = amount.rolling(20).mean().rank(axis=1, pct=True) < 0.5
    small_idx = (ret * small_mask).sum(axis=1) / small_mask.sum(axis=1)
    small_nav = (1 + small_idx.fillna(0)).cumprod()
    timing = (small_nav > small_nav.rolling(ma_window).mean()).shift(1, fill_value=False).astype(float)
    return timing


def build_weights(factor, close, top_n=15, rebal=20):
    """HK 池小, top_n 也要小 (15 而非 25)."""
    fdates = factor.dropna(how="all").index.intersection(close.index)
    if len(fdates) < 50:
        return {}
    w = {}
    for rd in list(fdates[::rebal]):
        pos = close.index.get_loc(rd)
        if pos + 1 >= len(close.index):
            continue
        eff = close.index[pos + 1]
        fv = factor.loc[rd].dropna()
        act = close.loc[rd].dropna().index
        fv = fv.reindex(act).dropna()
        if len(fv) < top_n:
            continue
        w[eff] = pd.Series(1.0 / top_n, index=fv.nlargest(top_n).index)
    return w


def metrics(ret: pd.Series) -> dict:
    r = ret.dropna()
    if len(r) < 50:
        return {}
    annual = float(r.mean() * 252)
    vol = float(r.std() * np.sqrt(252))
    sh = annual / (vol + 1e-9)
    cum = (1 + r).cumprod()
    mdd = float((cum / cum.cummax() - 1).min())
    return {"annual": annual, "vol": vol, "sharpe": sh, "maxdd": mdd,
            "calmar": annual / abs(mdd) if mdd < 0 else 0.0}


def main():
    close, volume, amount = load_hk_panels()
    if close.shape[1] < 30:
        print(f"⚠ too few HK stocks ({close.shape[1]})")
        return

    print("\n=== HK Strategy ===")
    factor = hk_small_cap_factor(amount, window=60)
    timing = hk_small_cap_timing(close, amount, ma_window=16)
    weights = build_weights(factor, close, top_n=15, rebal=20)
    print(f"  rebal periods: {len(weights)}")

    prices = PricePanel(close=close, volume=volume, amount=amount)
    cfg = BacktestConfig(start=str(close.index[0].date()),
                          cost=CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065),
                          leverage=1.25)
    engine = BacktestEngine(prices=prices, config=cfg)
    sig = Signal(weights=weights, timing=timing)
    result = engine.run(sig)
    hk_ret = result.returns.dropna()

    m = metrics(hk_ret)
    print("\n  HK small-cap (Binary MA16, 1.25x):")
    print(f"  annual={m['annual']:+.1%}, sharpe={m['sharpe']:+.2f}, "
          f"maxdd={m['maxdd']:+.1%}, calmar={m['calmar']:+.2f}")

    # ── 与 A 股 LIVE 对比 ──
    print("\n=== Cross-market correlation vs A 股 ACTIVE ===")
    print("  Running A 股 ACTIVE strategies...")
    a_returns = run_active(start="2018-01-01")

    print("\n  Correlation matrix:")
    print(f"  {'A 股 strategy':<30s} {'corr to HK':>11s}")
    print(f"  {'-'*45}")
    for name, a_ret in a_returns.items():
        common = a_ret.index.intersection(hk_ret.index)
        if len(common) < 100:
            print(f"  {name:<30s}     (insufficient overlap)")
            continue
        corr = a_ret.loc[common].corr(hk_ret.loc[common])
        print(f"  {name:<30s}  {corr:>+10.3f}")

    # ── Portfolio test ──
    print("\n=== Portfolio test: A 股 ACTIVE + HK ===")
    from portfolio.composer import compose
    from portfolio.composer import metrics as port_metrics
    combined = {**a_returns, "HK_small_cap": hk_ret}

    # Equal weight
    port_eq, _ = compose(combined, method="equal_weight")
    me = port_metrics(port_eq)
    print("  A 股 + HK equal weight:")
    print(f"  annual={me['annual']:+.1%}, sharpe={me['sharpe']:+.2f}, "
          f"maxdd={me['maxdd']:+.1%}, calmar={me['calmar']:+.2f}")

    # Risk parity
    port_rp, _ = compose(combined, method="risk_parity")
    mr = port_metrics(port_rp)
    print("  A 股 + HK risk_parity:")
    print(f"  annual={mr['annual']:+.1%}, sharpe={mr['sharpe']:+.2f}, "
          f"maxdd={mr['maxdd']:+.1%}, calmar={mr['calmar']:+.2f}")

    # Without HK baseline
    print("\n  vs (A 股 ACTIVE only baseline):")
    port_a, _ = compose(a_returns, method="risk_parity")
    ma = port_metrics(port_a)
    print("  A 股 only risk_parity:")
    print(f"  annual={ma['annual']:+.1%}, sharpe={ma['sharpe']:+.2f}, "
          f"maxdd={ma['maxdd']:+.1%}, calmar={ma['calmar']:+.2f}")

    delta_sharpe = mr["sharpe"] - ma["sharpe"]
    delta_calmar = mr["calmar"] - ma["calmar"]
    delta_mdd = mr["maxdd"] - ma["maxdd"]
    print("\n  Δ (加 HK − 不加 HK):")
    print(f"    sharpe: {delta_sharpe:+.3f}")
    print(f"    calmar: {delta_calmar:+.3f}")
    print(f"    maxdd:  {delta_mdd:+.2%}")
    if delta_sharpe > 0.05 or delta_calmar > 0.1:
        print("  ⭐ HK 真正多元化 — 显著改善组合")
    elif delta_sharpe > 0:
        print("  ◐ HK 微改善 — 值得 SHADOW 持续观察")
    else:
        print("  ❌ HK 不改善组合 — 可能 HK 单独 sharpe 太弱拖累")


if __name__ == "__main__":
    main()
