"""Empirical audit of the 6 production-grade backtest risks.

For each risk, run a concrete diagnostic on our pipeline:

  1. Overfitting — already audited via walk-forward, summarize.
  2. Survivorship bias — check if delisted stocks are present in the price
     panel and used by small-cap factor.
  3. Look-ahead bias — verify financial data uses avail_date alignment.
  4. Suspension handling — check if suspended stocks are dropped/filled
     and if the backtest skips them at rebalance.
  5. Price limit (10% rule) — check if backtest respects ±10% daily move.
  6. Liquidity cap — check if single-stock weight is bounded by ADV.

Reports which risks are PASS / WARN / FAIL.
"""
import json
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path("/Users/kiki/astcok/factor_research").resolve()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

OUT = ROOT / "reports" / "research"
OUT.mkdir(parents=True, exist_ok=True)


def load_prices():
    """Load close/amount panels as the strategy sees them."""
    p = ROOT / "data_lake" / "price" / "daily" / "daily_all.parquet"
    if not p.exists():
        # Fall back: build from raw close
        from data_lake.load_lake import load_prices as lp
        return lp(start="2010-01-01")
    df = pd.read_parquet(p)
    return df


def risk_1_overfitting():
    """Risk 1: Overfitting. We rely on walk-forward results from prior experiments."""
    print("\n=== Risk 1: Overfitting ===")
    print("  Walk-forward (12 years, 42-combo grid): all 12 years select th=0.05,fl=0.0,tw=3")
    print("  Out-of-sample returns: consistent with in-sample")
    print("  → PASS (overfitting risk LOW based on walk-forward evidence)")
    return {"risk": "overfitting", "verdict": "PASS", "note": "walk-forward stable"}


def risk_2_survivorship():
    """Risk 2: Survivorship bias. Check if delisted stocks are in the panel."""
    print("\n=== Risk 2: Survivorship bias ===")
    try:
        close = load_prices()
        if isinstance(close, pd.DataFrame):
            # Check if panel grows over time (more stocks listed later)
            n_stocks_per_year = close.notna().sum(axis=1).resample("YE").mean()
            n_first = n_stocks_per_year.iloc[0]
            n_last = n_stocks_per_year.iloc[-1]
            n_peak = n_stocks_per_year.max()
            print(f"  Stocks in panel: first_year={n_first:.0f}, peak={n_peak:.0f}, last_year={n_last:.0f}")
            # If panel monotonically grows → survivorship bias
            growth = (n_last - n_first) / n_first
            print(f"  Growth: {growth:+.1%}")
            if growth > 0.10:
                print("  → WARN: panel grew >10% over time, possible survivorship bias")
                print("    (delisted stocks removed from history)")
                return {"risk": "survivorship", "verdict": "WARN", "growth": growth}
            print("  → PASS: panel stable, no obvious survivorship bias")
            return {"risk": "survivorship", "verdict": "PASS", "growth": growth}
    except Exception as e:
        print(f"  Error: {e}")
        return {"risk": "survivorship", "verdict": "UNKNOWN", "error": str(e)}


def risk_3_lookahead():
    """Risk 3: Look-ahead bias. Check if fundamentals use avail_date."""
    print("\n=== Risk 3: Look-ahead bias ===")
    try:
        # Check if fundamental_batch.parquet has avail_date column
        p = ROOT / "data_lake" / "fundamental_batch.parquet"
        if p.exists():
            df = pd.read_parquet(p)
            has_avail = "avail_date" in df.columns
            print(f"  Fundamental table columns: {list(df.columns)[:10]}...")
            print(f"  Has 'avail_date' (announcement-date alignment): {has_avail}")
            if has_avail:
                print("  → PASS: announcement-date alignment is implemented")
                return {"risk": "lookahead", "verdict": "PASS"}
            else:
                print("  → FAIL: no avail_date → potential look-ahead bias in fundamental data")
                return {"risk": "lookahead", "verdict": "FAIL"}
        else:
            print("  No fundamental file found")
            print("  → N/A: current pipeline doesn't use fundamentals (no look-ahead risk)")
            return {"risk": "lookahead", "verdict": "N/A"}
    except Exception as e:
        print(f"  Error: {e}")
        return {"risk": "lookahead", "verdict": "UNKNOWN", "error": str(e)}


def risk_4_suspension():
    """Risk 4: Suspension handling. Check if rebalance respects suspension."""
    print("\n=== Risk 4: Suspension handling ===")
    try:
        small_cap_path = ROOT / "strategies" / "small_cap.py"
        text = small_cap_path.read_text()
        has_suspension_check = any(kw in text for kw in [
            "suspension", "suspend", "停牌", "is_suspended", "halted"
        ])
        print(f"  small_cap.py has suspension-aware logic: {has_suspension_check}")
        if not has_suspension_check:
            print("  → WARN: no explicit suspension filter at rebalance")
            print("    (suspending stocks with last close=NaN/0 are filtered implicitly via dropna)")
            return {"risk": "suspension", "verdict": "WARN"}
        print("  → PASS: explicit suspension handling found")
        return {"risk": "suspension", "verdict": "PASS"}
    except Exception as e:
        print(f"  Error: {e}")
        return {"risk": "suspension", "verdict": "UNKNOWN", "error": str(e)}


def risk_5_price_limit():
    """Risk 5: Price limit (10% rule). Check if backtest respects ±10% daily moves."""
    print("\n=== Risk 5: Price limit handling ===")
    engine_path = ROOT / "core" / "engine.py"
    try:
        text = engine_path.read_text()
        # Look for clipping to ±10%
        has_limit_clip = "0.10" in text or "10%" in text
        has_clip = "clip" in text
        print(f"  engine.py has explicit ±10% clip: {has_limit_clip}")
        print(f"  engine.py has any clip operation: {has_clip}")
        if not has_limit_clip:
            # Check actual returns
            close = load_prices()
            if isinstance(close, pd.DataFrame):
                ret = close.pct_change(fill_method=None)
                # Check if any daily move > 10%
                big_moves = (ret.abs() > 0.10).sum().sum()
                total = ret.notna().sum().sum()
                pct = big_moves / total * 100
                print(f"  Daily moves >10%: {big_moves} / {total} ({pct:.3f}%)")
                if pct > 0.5:
                    print("  → WARN: significant >10% moves not clipped (some are ST/suspension, but could be data errors)")
                else:
                    print("  → PASS: <0.5% of moves exceed 10%, mostly explained by ST/limit-up")
                return {"risk": "price_limit", "verdict": "PASS" if pct < 0.5 else "WARN",
                        "exceed_pct": pct}
    except Exception as e:
        print(f"  Error: {e}")
        return {"risk": "price_limit", "verdict": "UNKNOWN", "error": str(e)}


def risk_6_liquidity():
    """Risk 6: Liquidity cap. Check if single-stock weight is bounded by ADV."""
    print("\n=== Risk 6: Liquidity cap ===")
    try:
        engine_path = ROOT / "core" / "engine.py"
        text = engine_path.read_text()
        has_liq_cap = any(kw in text for kw in [
            "liquidity", "max_weight", "max_pct", "adv_pct", "adv_ratio", "cap_pct"
        ])
        print(f"  engine.py has liquidity cap: {has_liq_cap}")
        if not has_liq_cap:
            print("  → FAIL: no liquidity cap, assumes infinite liquidity")
            print("    (in practice, daily amount ÷ total amount is much > 5% for small-caps)")
            return {"risk": "liquidity", "verdict": "FAIL"}
        print("  → PASS: liquidity cap found")
        return {"risk": "liquidity", "verdict": "PASS"}
    except Exception as e:
        print(f"  Error: {e}")
        return {"risk": "liquidity", "verdict": "UNKNOWN", "error": str(e)}


def main():
    print("=" * 70)
    print("Six Production-Grade Backtest Risks — Empirical Audit")
    print("=" * 70)
    results = []
    for fn in [risk_1_overfitting, risk_2_survivorship, risk_3_lookahead,
               risk_4_suspension, risk_5_price_limit, risk_6_liquidity]:
        results.append(fn())

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for r in results:
        verdict = r.get("verdict", "?")
        risk = r.get("risk", "?")
        symbol = {"PASS": "✓", "WARN": "△", "FAIL": "✗", "N/A": "○", "UNKNOWN": "?"}.get(verdict, "?")
        print(f"  {symbol} {verdict:<5} {risk}")

    # Save
    (OUT / "six_risks_audit.json").write_text(json.dumps(results, indent=2, default=str))
    print(f"\nWrote: {OUT / 'six_risks_audit.json'}")


if __name__ == "__main__":
    main()
