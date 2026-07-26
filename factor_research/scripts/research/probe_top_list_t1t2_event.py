#!/usr/bin/env python3
"""龙虎榜 T+1 / T+2 事件策略 L0 体检。

口径(A股, 榜单 T 日盘后披露):
  - 最早可交易日 = T+1
  - hold1:  T+1 收 → T+2 收  (1 个交易日持有)
  - hold2:  T+1 收 → T+3 收  (2 个交易日持有)
  - gap1:   T 收 → T+1 收    (含隔夜跳空; 仅作参考,实盘未必吃满)

规则事先写死, 不网格搜阈值。成本: 单边 15bp ×2 (简化往返 30bp)。
IS=2018-2022 / OOS=2023-2024 / holdout≥2025 只报。

只产证据,不入册。
"""
from __future__ import annotations

import json
import os
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

ROUND_TRIP_COST = 0.003  # 30bp
IS_END = "2022-12-31"
OOS_END = "2024-12-31"
HOLDOUT = "2025-01-01"


def _to6(s: pd.Series) -> pd.Series:
    return s.astype(str).str.split(".").str[0]


def load_events() -> pd.DataFrame:
    tl = pd.read_parquet(
        "data_lake/institutional/top_list_all.parquet",
        columns=[
            "trade_date",
            "ts_code",
            "net_amount",
            "net_rate",
            "pct_change",
            "turnover_rate",
            "amount",
            "reason",
        ],
    )
    tl["date"] = pd.to_datetime(tl["trade_date"].astype(str))
    tl["code"] = _to6(tl["ts_code"])
    tl["net_amount"] = pd.to_numeric(tl["net_amount"], errors="coerce")
    tl["net_rate"] = pd.to_numeric(tl["net_rate"], errors="coerce")
    tl["pct_change"] = pd.to_numeric(tl["pct_change"], errors="coerce")
    tl["turnover_rate"] = pd.to_numeric(tl["turnover_rate"], errors="coerce")
    # aggregate multi-reason same stock-day: sum net, max turnover, join reasons
    g = (
        tl.groupby(["date", "code"], sort=False)
        .agg(
            net_amount=("net_amount", "sum"),
            net_rate=("net_rate", "mean"),
            pct_change=("pct_change", "mean"),
            turnover_rate=("turnover_rate", "max"),
            amount=("amount", "sum"),
            reason=("reason", lambda s: "|".join(sorted(set(map(str, s))))),
            n_rows=("net_amount", "size"),
        )
        .reset_index()
    )
    return g


def reason_flags(reason: pd.Series) -> pd.DataFrame:
    r = reason.fillna("").astype(str)
    return pd.DataFrame(
        {
            "is_up": r.str.contains("涨幅|涨停|收盘价格涨", regex=True),
            "is_down": r.str.contains("跌幅|跌停|收盘价格跌", regex=True),
            "is_turn": r.str.contains("换手", regex=True),
            "is_amp": r.str.contains("振幅", regex=True),
            "is_3day": r.str.contains("连续三个|三个交易日", regex=True),
        },
        index=reason.index,
    )


def load_close() -> pd.DataFrame:
    da = pd.read_parquet(
        "data_lake/price/daily_all.parquet", columns=["date", "code", "close"]
    )
    da["date"] = pd.to_datetime(da["date"])
    da["code"] = da["code"].astype(str)
    return da.pivot_table(index="date", columns="code", values="close").sort_index()


def attach_forward_rets(events: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    """Map each event to hold1/hold2/gap1 returns via positional shift on calendar."""
    dates = close.index.sort_values()
    pos = {d: i for i, d in enumerate(dates)}
    codes = close.columns
    # numpy for speed
    arr = close.to_numpy(dtype=float)
    code_to_j = {c: j for j, c in enumerate(codes)}

    rows = []
    for rec in events.itertuples(index=False):
        d, code = rec.date, rec.code
        if d not in pos or code not in code_to_j:
            continue
        i, j = pos[d], code_to_j[code]
        c0 = arr[i, j]
        if not np.isfinite(c0) or c0 <= 0:
            continue

        def px(k, j=j):
            if k < 0 or k >= len(dates):
                return np.nan
            v = arr[k, j]
            return v if np.isfinite(v) and v > 0 else np.nan

        c1, c2, c3 = px(i + 1), px(i + 2), px(i + 3)
        gap1 = c1 / c0 - 1.0 if np.isfinite(c1) else np.nan
        hold1 = c2 / c1 - 1.0 if np.isfinite(c1) and np.isfinite(c2) else np.nan
        hold2 = c3 / c1 - 1.0 if np.isfinite(c1) and np.isfinite(c3) else np.nan
        rows.append(
            {
                "date": d,
                "code": code,
                "net_amount": rec.net_amount,
                "net_rate": rec.net_rate,
                "pct_change": rec.pct_change,
                "turnover_rate": rec.turnover_rate,
                "reason": rec.reason,
                "gap1": gap1,
                "hold1": hold1,
                "hold2": hold2,
            }
        )
    out = pd.DataFrame(rows)
    flags = reason_flags(out["reason"])
    return pd.concat([out, flags], axis=1)


def rule_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Frozen a-priori rules (no grid)."""
    nb = df["net_amount"]
    nr = df["net_rate"]
    pc = df["pct_change"]
    tr = df["turnover_rate"]
    return {
        "all_list": pd.Series(True, index=df.index),
        "net_buy": nb > 0,
        "net_sell": nb < 0,
        "strong_buy": (nb > 0) & (nr > 5),
        "strong_sell": (nb < 0) & (nr < -5),
        "up_list": df["is_up"],
        "down_list": df["is_down"],
        "up_and_buy": df["is_up"] & (nb > 0),
        "up_and_sell": df["is_up"] & (nb < 0),  # 涨停板出货?
        "down_and_buy": df["is_down"] & (nb > 0),  # 杀跌吸筹?
        "turn_list": df["is_turn"],
        "turn_and_buy": df["is_turn"] & (nb > 0),
        "high_turn_buy": (tr > 20) & (nb > 0),
        # 反转先验: 大涨上榜 + 净卖
        "fade_up_sell": df["is_up"] & (nb < 0) & (pc > 7),
        # 动量先验: 大涨上榜 + 净买 (常踩雷)
        "chase_up_buy": df["is_up"] & (nb > 0) & (pc > 7),
    }


def summarize(ret: pd.Series, cost: float = ROUND_TRIP_COST) -> dict:
    r = ret.dropna()
    if len(r) < 30:
        return {"n": int(len(r)), "mean": None, "mean_net": None, "hit": None, "sharpe_ann": None}
    mean = float(r.mean())
    # 事件收益近似独立 → 年化用 sqrt(N_year); 粗算: 假设每年事件密度与全样本类似
    std = float(r.std())
    # per-event sharpe * sqrt(252) 不合适; 报告事件夏普与简单年化
    hit = float((r > 0).mean())
    mean_net = mean - cost
    # 若每年约 n_per_year 次独立事件
    return {
        "n": int(len(r)),
        "mean": mean,
        "mean_net": mean_net,
        "hit": hit,
        "std": std,
        "tstat": float(mean / (std / np.sqrt(len(r)))) if std > 0 else None,
        "tstat_net": float(mean_net / (std / np.sqrt(len(r)))) if std > 0 else None,
    }


def split_eval(df: pd.DataFrame, mask: pd.Series, col: str) -> dict:
    sub = df.loc[mask]
    out = {}
    for name, lo, hi in (
        ("IS", "2018-01-01", IS_END),
        ("OOS", "2023-01-01", OOS_END),
        ("holdout", HOLDOUT, "2099-01-01"),
        ("full_research", "2018-01-01", OOS_END),
    ):
        m = (sub["date"] >= lo) & (sub["date"] <= hi)
        out[name] = summarize(sub.loc[m, col])
    return out


def main() -> int:
    print("=" * 72)
    print("  龙虎榜 T+1/T+2 事件策略 L0")
    print("  hold1=T+1→T+2 close | hold2=T+1→T+3 | cost=30bp round-trip")
    print("=" * 72)

    print("[1/3] load events + prices ...")
    events = load_events()
    close = load_close()
    print(f"  events={len(events):,}  close={close.shape}")

    print("[2/3] attach forward returns ...")
    df = attach_forward_rets(events, close)
    print(f"  matched events={len(df):,}")
    print(
        f"  hold1 mean={df['hold1'].mean():+.4%}  hold2={df['hold2'].mean():+.4%}  "
        f"gap1={df['gap1'].mean():+.4%}"
    )

    print("[3/3] rules ...")
    masks = rule_masks(df)
    horizons = ("hold1", "hold2")
    table = []

    print("\n### hold1 (T+1 close → T+2 close)  |  mean gross / net(−30bp) / hit / n / t")
    print(
        f"{'rule':<18} {'IS_net':>8} {'OOS_net':>8} {'IS_n':>6} {'OOS_n':>6} "
        f"{'IS_hit':>7} {'OOS_hit':>7} {'OOS_t':>7}"
    )
    for rule, mask in masks.items():
        for h in horizons:
            ev = split_eval(df, mask, h)
            row = {
                "rule": rule,
                "horizon": h,
                "IS": ev["IS"],
                "OOS": ev["OOS"],
                "holdout": ev["holdout"],
                "full_research": ev["full_research"],
            }
            table.append(row)
            if h == "hold1":
                is_, oos = ev["IS"], ev["OOS"]
                def fmt(d, k):
                    v = d.get(k)
                    if v is None:
                        return f"{'n/a':>8}"
                    if k in ("mean", "mean_net"):
                        return f"{v:>+7.2%}"
                    if k == "hit":
                        return f"{v:>6.1%}"
                    if k == "tstat_net":
                        return f"{v:>7.2f}"
                    return f"{v:>6}"

                print(
                    f"{rule:<18} {fmt(is_,'mean_net')} {fmt(oos,'mean_net')} "
                    f"{fmt(is_,'n')} {fmt(oos,'n')} {fmt(is_,'hit')} {fmt(oos,'hit')} "
                    f"{fmt(oos,'tstat_net')}"
                )

    print("\n### hold2 (T+1 close → T+3 close) OOS net mean")
    print(f"{'rule':<18} {'OOS_net':>8} {'OOS_n':>6} {'OOS_hit':>7} {'OOS_t':>7}")
    for row in table:
        if row["horizon"] != "hold2":
            continue
        oos = row["OOS"]
        if oos["mean_net"] is None:
            continue
        print(
            f"{row['rule']:<18} {oos['mean_net']:>+7.2%} {oos['n']:>6} "
            f"{oos['hit']:>6.1%} {oos['tstat_net']:>7.2f}"
        )

    # mechanical shortlist: OOS net>0, t>1.5, n>=200, IS net>0 same sign
    print("\n### shortlist (OOS net>0, t_net>1.5, n≥200, IS net>0)")
    short = []
    for row in table:
        is_, oos = row["IS"], row["OOS"]
        if not is_ or not oos or is_["mean_net"] is None or oos["mean_net"] is None:
            continue
        if (
            oos["mean_net"] > 0
            and is_["mean_net"] > 0
            and oos["tstat_net"] is not None
            and oos["tstat_net"] > 1.5
            and oos["n"] >= 200
        ):
            short.append(row)
            print(
                f"  {row['rule']}/{row['horizon']}: "
                f"IS_net={is_['mean_net']:+.2%} OOS_net={oos['mean_net']:+.2%} "
                f"n={oos['n']} t={oos['tstat_net']:.2f}"
            )
    if not short:
        print("  (none)")

    # baseline all_list
    print("\n### baseline all_list")
    for h in horizons:
        for row in table:
            if row["rule"] == "all_list" and row["horizon"] == h:
                for seg in ("IS", "OOS", "holdout"):
                    d = row[seg]
                    if d["mean"] is None:
                        continue
                    print(
                        f"  {h} {seg}: gross={d['mean']:+.2%} net={d['mean_net']:+.2%} "
                        f"hit={d['hit']:.1%} n={d['n']} t_net={d['tstat_net']:.2f}"
                    )

    print("\nADVISORY:")
    print("  - 榜单次日通常偏弱(反转); 追涨净买(chase_up_buy)常为负")
    print("  - 更可能的方向: 涨榜净卖出 fade / 跌榜净买入 等反转结构")
    print("  - 即使事件均值为正, 也需考虑: 涨停买不到、掉队、冲击、同时持仓数")
    print("  - L0 only; 不入册")

    # JSON serializable
    def clean(d):
        return {k: (None if v is None or (isinstance(v, float) and not np.isfinite(v)) else v) for k, v in d.items()}

    payload = {
        "meta": {
            "round_trip_cost": ROUND_TRIP_COST,
            "hold1": "close[T+2]/close[T+1]-1",
            "hold2": "close[T+3]/close[T+1]-1",
            "note": "L0 top-list event T+1/T+2; not alpha",
        },
        "baseline_all": {
            h: {
                seg: clean(next(r[seg] for r in table if r["rule"] == "all_list" and r["horizon"] == h))
                for seg in ("IS", "OOS", "holdout")
            }
            for h in horizons
        },
        "rules": [
            {
                "rule": r["rule"],
                "horizon": r["horizon"],
                "IS": clean(r["IS"]),
                "OOS": clean(r["OOS"]),
                "holdout": clean(r["holdout"]),
            }
            for r in table
        ],
        "shortlist": [
            {"rule": r["rule"], "horizon": r["horizon"], "OOS": clean(r["OOS"]), "IS": clean(r["IS"])}
            for r in short
        ],
    }
    out = Path("scratch/probe_top_list_t1t2_event.json")
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=float))
    print(f"\nJSON → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
