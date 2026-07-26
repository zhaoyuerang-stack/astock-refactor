"""Band SHADOW Review — Binary vs Band 平行 NAV 累计 + 对比。

设计:
  · 读 signals/YYYY-MM-DD.json (run_daily.py 写,含 holdings + binary in_market + shadow_band_exposure)
  · 每日按 holdings 等权算次日收益 (T+1 收盘价口径,纯计算 SHADOW,不含 paper T+1 摩擦)
  · 两个虚拟 NAV:
      - Binary NAV: ret × in_market × leverage(1.25)
      - Band NAV:   ret × band_exposure × leverage(1.0)
  · 累计写 paper/band_shadow.csv (date, ret_held, binary_exp, band_exp,
                                  binary_nav, band_nav)

执行: /usr/bin/python3 -m scripts.research.band_shadow_review
  · --since 2026-06-07 限定回顾起点
  · --update 增量计算并写文件
  · --summary 仅打印汇总对比 (默认)
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from lake.load_lake import load_prices  # noqa: E402

SIGNALS = ROOT / "signals"
PAPER = ROOT / "paper"
PAPER.mkdir(exist_ok=True)
SHADOW_CSV = PAPER / "band_shadow.csv"

BINARY_LEV = 1.25
BAND_LEV = 1.0
COST_PER_REBAL = 0.005   # 0.5% per rebalance (~ 0.225+0.275)


def _load_signals(since: date = None) -> list[dict]:
    out = []
    for fp in sorted(SIGNALS.glob("20*-*-*.json")):
        try:
            d = json.loads(fp.read_text())
        except Exception:
            continue
        sig_date = pd.Timestamp(d["date"]).date()
        if since and sig_date < since:
            continue
        out.append(d)
    return out


def _holdings_ret(close: pd.DataFrame, holdings: list[str], t0: pd.Timestamp,
                  t1: pd.Timestamp) -> float:
    """等权 holdings 在 t0→t1 的回报 (T 日收盘 → T+1 日收盘口径)."""
    if t0 not in close.index or t1 not in close.index or not holdings:
        return 0.0
    have = [c for c in holdings if c in close.columns]
    if not have:
        return 0.0
    p0 = close.loc[t0, have]
    p1 = close.loc[t1, have]
    r = (p1 / p0 - 1).dropna()
    return float(r.mean()) if len(r) else 0.0


def compute_shadow():
    print("Loading close panel...")
    px = load_prices(start="2024-01-01", fields=("close",))
    close = px["close"]

    sigs = _load_signals(since=date(2026, 6, 1))   # SHADOW 起点
    if not sigs:
        print("⚠ no signals on or after 2026-06-07; nothing to track yet")
        return

    rows = []
    cum_binary = 1.0
    cum_band = 1.0
    last_binary_holdings = []
    last_band_holdings = []

    for _, sig in enumerate(sigs):
        t = pd.Timestamp(sig["date"])
        # Find next trade day in close.index
        future = close.index[close.index > t]
        if len(future) == 0:
            break  # no next-day return yet
        t_next = future[0]

        # Returns of T-day signaled holdings, applied to T→T+1
        b_h = sig.get("holdings", []) or []
        bd_h = sig.get("shadow_band_holdings", []) or []

        r_b = _holdings_ret(close, b_h, t, t_next)
        r_bd = _holdings_ret(close, bd_h, t, t_next)

        in_mkt_b = 1.0 if sig.get("in_market", False) else 0.0
        exp_bd = float(sig.get("shadow_band_exposure", 0.0))

        # Cost on rebalance (turnover proxy)
        cost_b = COST_PER_REBAL if (set(b_h) != set(last_binary_holdings) and b_h) else 0.0
        cost_bd = COST_PER_REBAL if (set(bd_h) != set(last_band_holdings) and bd_h) else 0.0

        day_ret_b = r_b * in_mkt_b * BINARY_LEV - cost_b
        day_ret_bd = r_bd * exp_bd * BAND_LEV - cost_bd

        cum_binary *= (1 + day_ret_b)
        cum_band *= (1 + day_ret_bd)

        rows.append({
            "date": sig["date"],
            "next_t": t_next.strftime("%Y-%m-%d"),
            "in_market_binary": int(in_mkt_b),
            "exposure_band": exp_bd,
            "ret_held_binary": round(r_b, 6),
            "ret_held_band": round(r_bd, 6),
            "day_ret_binary": round(day_ret_b, 6),
            "day_ret_band": round(day_ret_bd, 6),
            "nav_binary": round(cum_binary, 6),
            "nav_band": round(cum_band, 6),
        })

        last_binary_holdings = b_h
        last_band_holdings = bd_h

    if rows:
        df = pd.DataFrame(rows)
        df.to_csv(SHADOW_CSV, index=False)
        print(f"✓ wrote {SHADOW_CSV} ({len(rows)} days)")
    return rows


def print_summary():
    if not SHADOW_CSV.exists():
        print("⚠ no shadow data yet; run with --update first after some daily signals")
        return
    df = pd.read_csv(SHADOW_CSV)
    if len(df) == 0:
        print("⚠ empty shadow data")
        return

    print(f"\n{'='*65}")
    print(f"  BAND SHADOW REVIEW  ({df['date'].min()} ~ {df['date'].max()}, {len(df)} days)")
    print(f"{'='*65}")

    nav_b = df["nav_binary"].iloc[-1]
    nav_bd = df["nav_band"].iloc[-1]
    in_mkt_pct = df["in_market_binary"].mean()
    exp_avg = df[df["exposure_band"] > 0]["exposure_band"].mean() if (df["exposure_band"] > 0).any() else 0.0

    print("\n  Binary (current LIVE):")
    print(f"    NAV          : {nav_b:.4f} ({(nav_b-1)*100:+.2f}%)")
    print(f"    in_market 占比: {in_mkt_pct:.0%}")
    print(f"    leverage     : {BINARY_LEV}x")
    print("\n  Band (SHADOW):")
    print(f"    NAV          : {nav_bd:.4f} ({(nav_bd-1)*100:+.2f}%)")
    print(f"    avg exposure : {exp_avg:.2f}x (when > 0)")
    print(f"    leverage     : {BAND_LEV}x")

    # Sharpe per day
    rb = df["day_ret_binary"].dropna()
    rbd = df["day_ret_band"].dropna()
    if len(rb) >= 10:
        sh_b = rb.mean() / (rb.std() + 1e-9) * np.sqrt(252)
        sh_bd = rbd.mean() / (rbd.std() + 1e-9) * np.sqrt(252)
        print("\n  Annualized Sharpe (cum 至今):")
        print(f"    Binary: {sh_b:+.2f}")
        print(f"    Band:   {sh_bd:+.2f}")

    print(f"\n  Δ (Band − Binary): NAV {(nav_bd - nav_b)*100:+.2f}pp")
    if nav_bd > nav_b:
        print("    → Band 领先,符合 plan 预期")
    elif nav_bd < nav_b * 0.99:
        print("    → Band 落后 > 1%,质疑回测结论")
    else:
        print("    → 持平 (期数太少,需更多观察)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="重新计算并写 CSV")
    args = ap.parse_args()

    if args.update:
        compute_shadow()
    print_summary()


if __name__ == "__main__":
    main()
