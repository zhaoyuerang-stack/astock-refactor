"""Priority audit for amount-rank + MA16 timing.

Audit order:
  1. Data / universe
  2. Drawdown reproduction gap
  3. Capacity / tradability

This is a read-only research script. It does not register or promote anything.

Usage:
  cd /Users/kiki/astcok/factor_research
  python3 scripts/research/amount_timing_priority_audit.py
"""
from __future__ import annotations

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
from factors.small_cap import small_cap_timing
from lake.load_lake import LAKE, load_raw_close, load_tushare_panel
from strategies.small_cap import _drop_star, load_price_panels

STATS_START = "2018-01-01"
WARMUP_START = "2010-01-01"
TOP_N = 25


def amount_factor(amount: pd.DataFrame) -> pd.DataFrame:
    return amount.rank(axis=1, pct=True)


def metrics(ret: pd.Series) -> dict[str, float]:
    r = ret.dropna()
    annual = float(r.mean() * 252)
    vol = float(r.std() * np.sqrt(252))
    sharpe = annual / vol if vol > 0 else float("nan")
    nav = (1.0 + r).cumprod()
    maxdd = float((nav / nav.cummax() - 1.0).min())
    return {"annual": annual, "maxdd": maxdd, "sharpe": sharpe, "n": len(r)}


def fmt_pct(x: float) -> str:
    return "nan" if not np.isfinite(x) else f"{x:+.1%}"


def run_case(
    close: pd.DataFrame,
    volume: pd.DataFrame,
    amount: pd.DataFrame,
    *,
    label: str,
    timing_mode: str,
    leverage: float,
    start: str = STATS_START,
    cost: CostModel | None = None,
) -> tuple[dict, pd.DataFrame, pd.Series, pd.Series]:
    factor = amount_factor(amount)
    timing_bool, _, dist = small_cap_timing(close, amount, 16)
    exposure_cap = 1.0
    if timing_mode == "none":
        timing = None
    elif timing_mode == "binary":
        timing = timing_bool.astype(float)
    elif timing_mode == "band":
        d = dist.shift(1)
        timing = ((1.0 + d * 8.0).clip(0.0, 1.5) * (d > 0).astype(float)).fillna(0.0)
        exposure_cap = 1.5
    else:
        raise ValueError(timing_mode)

    prices = PricePanel(close=close, volume=volume, amount=amount)
    engine = BacktestEngine(
        prices,
        BacktestConfig(start=start, cost=cost or CostModel(), leverage=leverage),
    )
    signal = Signal(
        factor=factor,
        top_n=TOP_N,
        direction=-1,
        rebalance_freq="20D",
        timing=timing,
        exposure_cap=exposure_cap,
        family="amount-timing",
        version=label,
    )
    result = engine.run(signal)
    weights = signal._resolve_weights(prices)
    row = {"case": label, "leverage": leverage, "timing": timing_mode, **metrics(result.returns)}
    return row, weights, result.returns, timing_bool


def drawdown_periods(ret: pd.Series, top_n: int = 5) -> list[dict]:
    nav = (1.0 + ret.dropna()).cumprod()
    dd = nav / nav.cummax() - 1.0
    periods = []
    in_dd = False
    start = valley = None
    depth = 0.0
    for dt, value in dd.items():
        if value < 0 and not in_dd:
            in_dd = True
            start = valley = dt
            depth = float(value)
        elif value < 0 and in_dd and value < depth:
            valley = dt
            depth = float(value)
        elif value >= 0 and in_dd:
            periods.append({"start": start, "valley": valley, "end": dt, "depth": depth})
            in_dd = False
    if in_dd:
        periods.append({"start": start, "valley": valley, "end": dd.index[-1], "depth": depth})
    return sorted(periods, key=lambda x: x["depth"])[:top_n]


def load_list_dates() -> pd.Series:
    fp = LAKE / "meta/list_date.parquet"
    if not fp.exists():
        return pd.Series(dtype="datetime64[ns]")
    df = pd.read_parquet(fp)
    return pd.to_datetime(df.set_index("code")["first_date"])


def load_st_history(index: pd.DatetimeIndex, columns: pd.Index) -> pd.DataFrame:
    fp = LAKE / "meta/st_history.parquet"
    if not fp.exists():
        return pd.DataFrame(False, index=index, columns=columns)
    st = pd.read_parquet(fp)
    st.index = pd.to_datetime(st.index)
    return st.reindex(index=index, columns=columns).fillna(False).astype(bool)


def audit_universe(
    close: pd.DataFrame,
    volume: pd.DataFrame,
    amount: pd.DataFrame,
    weights: pd.DataFrame,
) -> pd.DataFrame:
    idx = close.index
    raw_close = load_raw_close(start=WARMUP_START).reindex(index=idx, columns=close.columns)
    adv20 = amount.rolling(20).mean()
    list_dates = load_list_dates()
    st = load_st_history(idx, close.columns)
    suspend = load_tushare_panel("suspend", idx, fields=["suspend_type"], codes=list(close.columns))["suspend_type"]
    limits = load_tushare_panel("stk_limit", idx, fields=["up_limit", "down_limit"], codes=list(close.columns))
    up_limit = limits["up_limit"]
    down_limit = limits["down_limit"]

    rows = []
    for dt, row in weights.loc[weights.index >= pd.Timestamp(STATS_START)].iterrows():
        names = row[row > 0].index
        if len(names) == 0:
            continue
        amt = amount.loc[dt, names]
        adv = adv20.loc[dt, names]
        raw = raw_close.loc[dt, names]
        up = up_limit.loc[dt, names]
        down = down_limit.loc[dt, names]
        age = []
        for code in names:
            first = list_dates.get(code, pd.NaT)
            age.append(np.nan if pd.isna(first) else (dt - first).days)
        rows.append(
            {
                "date": dt,
                "n": len(names),
                "star_ratio": float(pd.Index(names).astype(str).str.startswith("688").mean()),
                "st_ratio": float(st.loc[dt, names].mean()),
                "suspend_ratio": float(suspend.loc[dt, names].notna().mean()),
                "up_limit_ratio": float((np.isclose(raw, up, rtol=0, atol=1e-4)).mean()),
                "down_limit_ratio": float((np.isclose(raw, down, rtol=0, atol=1e-4)).mean()),
                "amount_min": float(amt.min()),
                "amount_median": float(amt.median()),
                "adv20_min": float(adv.min()),
                "adv20_median": float(adv.median()),
                "age_min_days": float(np.nanmin(age)) if len(age) else float("nan"),
                "age_lt_60_ratio": float(np.nanmean(np.array(age) < 60)) if len(age) else float("nan"),
            }
        )
    return pd.DataFrame(rows).set_index("date")


def capacity_table(audit: pd.DataFrame, aums=(5_000_000, 50_000_000, 500_000_000)) -> list[dict]:
    out = []
    for aum in aums:
        per_name = aum / TOP_N
        participation = per_name / audit["adv20_median"].replace(0, np.nan)
        worst_name_participation = per_name / audit["adv20_min"].replace(0, np.nan)
        out.append(
            {
                "aum": aum,
                "median_participation": float(participation.median()),
                "p95_participation": float(participation.quantile(0.95)),
                "median_worst_name": float(worst_name_participation.median()),
                "p95_worst_name": float(worst_name_participation.quantile(0.95)),
            }
        )
    return out


def print_scenario(rows: list[dict]) -> None:
    print(f"{'case':<20}{'lev':>6}{'timing':>9}{'annual':>10}{'maxdd':>10}{'sharpe':>9}{'n':>7}")
    print("-" * 71)
    for row in rows:
        print(
            f"{row['case']:<20}{row['leverage']:>6.2f}{row['timing']:>9}"
            f"{fmt_pct(row['annual']):>10}{fmt_pct(row['maxdd']):>10}"
            f"{row['sharpe']:>9.2f}{row['n']:>7}"
        )


def print_universe_summary(label: str, audit: pd.DataFrame) -> None:
    print(f"\n[{label}] universe/tradability summary across rebalance holdings")
    fields = [
        "star_ratio",
        "st_ratio",
        "suspend_ratio",
        "up_limit_ratio",
        "down_limit_ratio",
        "amount_min",
        "amount_median",
        "adv20_min",
        "adv20_median",
        "age_min_days",
        "age_lt_60_ratio",
    ]
    for field in fields:
        s = audit[field].dropna()
        if s.empty:
            continue
        if field.endswith("ratio"):
            print(f"{field:<18} mean={s.mean():.1%} p95={s.quantile(0.95):.1%} max={s.max():.1%}")
        elif "amount" in field or "adv20" in field:
            print(f"{field:<18} median={s.median()/1e6:.1f}M p05={s.quantile(0.05)/1e6:.1f}M min={s.min()/1e6:.1f}M")
        else:
            print(f"{field:<18} median={s.median():.0f} p05={s.quantile(0.05):.0f} min={s.min():.0f}")


def main() -> None:
    close, volume, amount = load_price_panels(WARMUP_START)
    print("=" * 80)
    print("amount-timing priority audit")
    print("=" * 80)
    print(f"data: {close.shape[1]} stocks x {close.shape[0]} days [{close.index[0].date()} ~ {close.index[-1].date()}]")

    scenarios = []
    main_row, main_weights, main_ret, _ = run_case(
        close, volume, amount, label="all_binary_1.25", timing_mode="binary", leverage=1.25
    )
    scenarios.append(main_row)
    for label, timing_mode, leverage in [
        ("all_binary_1.00", "binary", 1.0),
        ("all_none_1.25", "none", 1.25),
        ("all_band_1.25", "band", 1.25),
    ]:
        row, _, _, _ = run_case(close, volume, amount, label=label, timing_mode=timing_mode, leverage=leverage)
        scenarios.append(row)

    close_x, volume_x, amount_x = _drop_star(close, volume, amount)
    ex_row, ex_weights, ex_ret, _ = run_case(
        close_x, volume_x, amount_x, label="ex688_binary_1.25", timing_mode="binary", leverage=1.25
    )
    scenarios.append(ex_row)

    print("\n[1] Drawdown reproduction matrix")
    print_scenario(scenarios)
    print("\nReport claimed maxdd around -13.4%; current closest tested case is still materially worse.")

    print("\n[2] Top drawdown periods, all_binary_1.25")
    for p in drawdown_periods(main_ret):
        print(
            f"{p['start'].date()} -> {p['end'].date()} "
            f"valley={p['valley'].date()} depth={p['depth']:+.1%}"
        )

    print("\n[3] Data/factor sanity")
    f = amount_factor(amount).loc[STATS_START:]
    active = amount.loc[STATS_START:].notna()
    print(f"factor_nan_pct={f.isna().sum().sum()/f.size:.1%}")
    print(f"amount_zero_count={int((amount.loc[STATS_START:] == 0).sum().sum())}")
    print(f"amount_inf_count={int(np.isinf(amount.loc[STATS_START:].to_numpy()).sum())}")
    print(f"active_obs={int(active.sum().sum())}")

    audit_all = audit_universe(close, volume, amount, main_weights)
    audit_ex = audit_universe(close_x, volume_x, amount_x, ex_weights)
    print_universe_summary("all_binary_1.25", audit_all)
    print_universe_summary("ex688_binary_1.25", audit_ex)

    print("\n[4] Capacity proxy: target position / selected ADV20")
    print(f"{'universe':<10}{'aum':>12}{'med_part':>12}{'p95_part':>12}{'med_worst':>12}{'p95_worst':>12}")
    print("-" * 70)
    for label, audit in [("all", audit_all), ("ex688", audit_ex)]:
        for row in capacity_table(audit):
            print(
                f"{label:<10}{row['aum']/1e6:>10.0f}M"
                f"{row['median_participation']:>12.1%}"
                f"{row['p95_participation']:>12.1%}"
                f"{row['median_worst_name']:>12.1%}"
                f"{row['p95_worst_name']:>12.1%}"
            )

    print("\n[5] Latest holdings concentration")
    latest = close.index[-1]
    for label, weights in [("all", main_weights), ("ex688", ex_weights)]:
        latest_weight_date = weights.index[weights.index <= latest][-1]
        names = weights.loc[latest_weight_date]
        names = names[names > 0].index.astype(str)
        print(
            f"{label}: weight_date={latest_weight_date.date()} "
            f"n={len(names)} star={sum(c.startswith('688') for c in names)}/{len(names)} "
            f"holdings={', '.join(names)}"
        )


if __name__ == "__main__":
    main()
