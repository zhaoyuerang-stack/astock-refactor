"""Validate amount-rank + PureTrend MA16 timing.

This script is intentionally a validation harness, not a registry/promote step.

Usage:
  cd /Users/kiki/astcok/factor_research
  python3 scripts/research/validate_amount_timing.py
"""
from __future__ import annotations

import os
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.small_cap import small_cap_timing
from research_toolkit import newey_west_icir
from strategies.small_cap import _drop_star, load_price_panels

STATS_START = "2018-01-01"
WARMUP_START = "2010-01-01"
TOP_N = 25
REBALANCE_FREQ = "20D"
LEVERAGE = 1.25


def newey_west_sharpe(returns: pd.Series, max_lag: int = 20) -> float:
    """Annualized Sharpe with Newey-West long-run variance."""
    r = returns.dropna().astype(float)
    if len(r) < 30:
        return float("nan")
    x = r - r.mean()
    n = len(x)
    gamma0 = float(np.dot(x, x) / n)
    lrv = gamma0
    for lag in range(1, min(max_lag, n - 1) + 1):
        cov = float(np.dot(x.iloc[lag:], x.iloc[:-lag]) / n)
        weight = 1.0 - lag / (max_lag + 1.0)
        lrv += 2.0 * weight * cov
    if lrv <= 0:
        return float("nan")
    return float(r.mean() / np.sqrt(lrv) * np.sqrt(252))


def metrics(returns: pd.Series) -> dict[str, float]:
    r = returns.dropna()
    if len(r) < 30:
        return {
            "annual": float("nan"),
            "maxdd": float("nan"),
            "sharpe": float("nan"),
            "nw_sharpe": float("nan"),
            "calmar": float("nan"),
            "n": len(r),
        }
    annual = float(r.mean() * 252)
    vol = float(r.std() * np.sqrt(252))
    sharpe = annual / vol if vol > 0 else float("nan")
    nav = (1.0 + r).cumprod()
    maxdd = float((nav / nav.cummax() - 1.0).min())
    calmar = annual / abs(maxdd) if maxdd < 0 else float("nan")
    return {
        "annual": annual,
        "maxdd": maxdd,
        "sharpe": sharpe,
        "nw_sharpe": newey_west_sharpe(r),
        "calmar": calmar,
        "n": len(r),
    }


def amount_factor(amount: pd.DataFrame) -> pd.DataFrame:
    return amount.rank(axis=1, pct=True)


def run_backtest(
    close: pd.DataFrame,
    volume: pd.DataFrame,
    amount: pd.DataFrame,
    *,
    label: str,
    cost: CostModel | None = None,
    timing_mode: str = "binary",
    start: str = STATS_START,
) -> dict:
    factor = amount_factor(amount)
    timing_bool, small_nav, timing_dist = small_cap_timing(close, amount, ma_window=16)

    timing: pd.Series | None
    exposure_cap = 1.0
    if timing_mode == "none":
        timing = None
    elif timing_mode == "binary":
        timing = timing_bool.astype(float)
    elif timing_mode == "band":
        dist = timing_dist.shift(1)
        timing = ((1.0 + dist * 8.0).clip(0.0, 1.5) * (dist > 0).astype(float)).fillna(0.0)
        exposure_cap = 1.5
    else:
        raise ValueError(f"unknown timing_mode={timing_mode}")

    engine = BacktestEngine(
        PricePanel(close=close, volume=volume, amount=amount),
        BacktestConfig(
            start=start,
            cost=cost or CostModel(),
            leverage=LEVERAGE,
        ),
    )
    result = engine.run(
        Signal(
            factor=factor,
            top_n=TOP_N,
            direction=-1,
            rebalance_freq=REBALANCE_FREQ,
            timing=timing,
            exposure_cap=exposure_cap,
            family="amount-timing",
            version=label,
        )
    )
    return {
        "label": label,
        "result": result,
        "metrics": metrics(result.returns),
        "timing": timing_bool,
        "timing_dist": timing_dist,
        "small_nav": small_nav,
    }


def fmt_pct(x: float) -> str:
    return "nan" if not np.isfinite(x) else f"{x:+.1%}"


def fmt_num(x: float) -> str:
    return "nan" if not np.isfinite(x) else f"{x:.2f}"


def print_metric_table(rows: list[dict]) -> None:
    print(f"{'case':<22}{'annual':>10}{'maxdd':>10}{'sharpe':>9}{'NW':>8}{'calmar':>9}{'n':>7}")
    print("-" * 75)
    for row in rows:
        m = row["metrics"]
        print(
            f"{row['label']:<22}"
            f"{fmt_pct(m['annual']):>10}"
            f"{fmt_pct(m['maxdd']):>10}"
            f"{fmt_num(m['sharpe']):>9}"
            f"{fmt_num(m['nw_sharpe']):>8}"
            f"{fmt_num(m['calmar']):>9}"
            f"{m['n']:>7}"
        )


def yearly_table(result) -> pd.Series:
    return result.returns.groupby(result.returns.index.year).apply(lambda g: (1.0 + g).prod() - 1.0)


def walk_forward_by_year(close: pd.DataFrame, volume: pd.DataFrame, amount: pd.DataFrame) -> list[dict]:
    years = [y for y in sorted(close.index.year.unique()) if y >= 2018]
    rows = []
    for year in years:
        test_dates = close.index[close.index.year == year]
        if len(test_dates) < 30:
            continue
        test_start = test_dates[0]
        test_end = test_dates[-1]
        train_start = test_start - pd.DateOffset(years=3)
        mask = (close.index >= train_start) & (close.index <= test_end)
        c = close.loc[mask]
        v = volume.loc[mask]
        a = amount.loc[mask]
        row = run_backtest(
            c,
            v,
            a,
            label=f"wf_{year}",
            timing_mode="binary",
            start=str(test_start.date()),
        )
        ret = row["result"].returns.loc[:test_end]
        m = metrics(ret)
        holding = float(row["timing"].reindex(ret.index).fillna(False).mean())
        rows.append({"year": year, **m, "holding": holding})
    return rows


def factor_ic_audit(label: str, close: pd.DataFrame, volume: pd.DataFrame, amount: pd.DataFrame) -> None:
    factor = -amount_factor(amount)
    engine = BacktestEngine(
        PricePanel(close=close, volume=volume, amount=amount),
        BacktestConfig(start=STATS_START),
    )
    factor_stats = factor.loc[STATS_START:]
    ic = engine.run_ic_analysis(factor_stats, forward_days=5)["ic_series"].dropna()
    raw_icir = abs(float(ic.mean())) / float(ic.std()) if float(ic.std()) > 0 else float("nan")
    nw_icir = float(newey_west_icir(ic, max_lag=5))
    strat = engine.run_stratify(factor_stats, forward_days=5, n_quantile=5).mean()
    print(
        f"{label:<10} ic_mean={float(ic.mean()):+.4f} "
        f"raw_icir={raw_icir:.3f} nw_icir={nw_icir:.3f} n={len(ic)}"
    )
    print("           " + "  ".join(f"{idx}={value:+.4%}" for idx, value in strat.items()))


def main() -> None:
    print("=" * 78)
    print("amount-rank + PureTrend MA16 validation")
    print("=" * 78)
    close, volume, amount = load_price_panels(WARMUP_START)
    print(
        f"data: {close.shape[1]} stocks x {close.shape[0]} days "
        f"[{close.index[0].date()} ~ {close.index[-1].date()}]"
    )

    all_market = [
        run_backtest(close, volume, amount, label="all_binary", timing_mode="binary"),
        run_backtest(close, volume, amount, label="all_no_timing", timing_mode="none"),
        run_backtest(close, volume, amount, label="all_band", timing_mode="band"),
    ]
    print("\n[1] Full universe scenarios")
    print_metric_table(all_market)

    close_x, volume_x, amount_x = _drop_star(close, volume, amount)
    ex688 = [
        run_backtest(close_x, volume_x, amount_x, label="ex688_binary", timing_mode="binary"),
        run_backtest(close_x, volume_x, amount_x, label="ex688_no_timing", timing_mode="none"),
        run_backtest(close_x, volume_x, amount_x, label="ex688_band", timing_mode="band"),
    ]
    print("\n[2] Exclude STAR board 688 scenarios")
    print_metric_table(ex688)

    print("\n[3] Cost sensitivity, full universe binary")
    cost_rows = []
    for mult in [0.5, 1.0, 2.0, 3.0]:
        base = CostModel()
        cost = CostModel(
            buy_cost=base.buy_cost * mult,
            sell_cost=base.sell_cost * mult,
            financing_rate=base.financing_rate,
        )
        cost_rows.append(
            run_backtest(close, volume, amount, label=f"cost_{mult:.1f}x", cost=cost, timing_mode="binary")
        )
    print_metric_table(cost_rows)

    print("\n[4] Calendar-year returns, full universe binary")
    yr = yearly_table(all_market[0]["result"])
    for year, value in yr.items():
        print(f"{year}: {value:+.1%}")

    print("\n[5] Walk-forward by calendar year, 3y warmup each test year")
    wf = walk_forward_by_year(close, volume, amount)
    print(f"{'year':<6}{'annual':>10}{'maxdd':>10}{'sharpe':>9}{'NW':>8}{'holding':>10}{'n':>7}")
    print("-" * 66)
    for row in wf:
        print(
            f"{row['year']:<6}"
            f"{fmt_pct(row['annual']):>10}"
            f"{fmt_pct(row['maxdd']):>10}"
            f"{fmt_num(row['sharpe']):>9}"
            f"{fmt_num(row['nw_sharpe']):>8}"
            f"{row['holding']:>9.1%}"
            f"{row['n']:>7}"
        )
    if wf:
        print("-" * 66)
        print(
            f"WF positive years: {sum(r['annual'] > 0 for r in wf)}/{len(wf)}, "
            f"mean annual: {np.nanmean([r['annual'] for r in wf]):+.1%}, "
            f"mean NW: {np.nanmean([r['nw_sharpe'] for r in wf]):.2f}, "
            f"mean maxdd: {np.nanmean([r['maxdd'] for r in wf]):+.1%}"
        )

    print("\n[6] 5-day IC audit, high score = lower amount")
    factor_ic_audit("all", close, volume, amount)
    factor_ic_audit("ex688", close_x, volume_x, amount_x)

    last = close.index[-1]
    timing = all_market[0]["timing"]
    dist = all_market[0]["timing_dist"]
    holdings = amount_factor(amount).loc[last].dropna().nsmallest(TOP_N).index.tolist()
    holdings_x = amount_factor(amount_x).loc[last].dropna().nsmallest(TOP_N).index.tolist()
    print("\n[7] Latest signal")
    print(f"date: {last.date()}")
    print(f"in_market: {bool(timing.loc[last])}")
    print(f"small_nav_vs_ma16: {float(dist.loc[last]):+.2%}")
    print("holdings_all: " + ", ".join(map(str, holdings)))
    print("holdings_ex688: " + ", ".join(map(str, holdings_x)))

    # Export validation results to JSON
    import json
    export_data = {
        "updated_at": datetime.now().isoformat() if "datetime" in globals() else pd.Timestamp.now().isoformat(),
        "latest_signal": {
            "date": str(last.date()),
            "in_market": bool(timing.loc[last]),
            "small_nav_vs_ma16": float(dist.loc[last]),
            "holdings_all": [str(c) for c in holdings],
            "holdings_ex688": [str(c) for c in holdings_x]
        },
        "all_market": [
            {
                "label": x["label"],
                "metrics": {k: float(v) if np.isfinite(v) else None for k, v in x["metrics"].items()}
            }
            for x in all_market
        ],
        "ex688": [
            {
                "label": x["label"],
                "metrics": {k: float(v) if np.isfinite(v) else None for k, v in x["metrics"].items()}
            }
            for x in ex688
        ],
        "cost_sensitivity": [
            {
                "label": x["label"],
                "metrics": {k: float(v) if np.isfinite(v) else None for k, v in x["metrics"].items()}
            }
            for x in cost_rows
        ],
        "walk_forward": [
            {
                "year": int(x["year"]),
                "annual": float(x["annual"]) if np.isfinite(x["annual"]) else None,
                "maxdd": float(x["maxdd"]) if np.isfinite(x["maxdd"]) else None,
                "sharpe": float(x["sharpe"]) if np.isfinite(x["sharpe"]) else None,
                "nw_sharpe": float(x["nw_sharpe"]) if np.isfinite(x["nw_sharpe"]) else None,
                "holding": float(x["holding"]) if np.isfinite(x["holding"]) else None,
                "n": int(x["n"])
            }
            for x in wf
        ]
    }
    
    out_file = ROOT / "reports" / "ops" / "amount_timing_validation.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(export_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[+] Saved validation results to {out_file.name}")


if __name__ == "__main__":
    main()
