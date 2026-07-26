#!/usr/bin/env python3
"""龙虎榜 + 量 + 业绩 条件的 T+1/T+2 事件 L0。

在榜单事件上叠加事先冻结条件(禁止网格):
  量:
    vol_ratio = T 日 amount / ADV20(至 T-1)
    mild_vol  = 1.2 ≤ vol_ratio ≤ 4     # 放量但不过热
    hot_vol   = vol_ratio > 5           # 过热
    cool_turn = turnover_rate < 15      # 榜内相对低温
  业绩(PIT=avail_date ffill, 信号日 T 用 ≤T 已披露):
    earn_pos  = net_profit_yoy > 0
    earn_hi   = net_profit_yoy > 20
    roe_ok    = roe > 8
    rev_pos   = revenue_yoy > 0

持有口径同前:
  hold1 = close[T+2]/close[T+1]-1
  hold2 = close[T+3]/close[T+1]-1
成本 30bp 往返; IS/OOS/holdout 分段。

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

ROUND_TRIP_COST = 0.003
IS_END = "2022-12-31"
OOS_END = "2024-12-31"
HOLDOUT = "2025-01-01"

# frozen thresholds
VOL_MILD_LO, VOL_MILD_HI = 1.2, 4.0
VOL_HOT = 5.0
TURN_COOL = 15.0
YOY_HI = 20.0
ROE_OK = 8.0


def _to6(s: pd.Series) -> pd.Series:
    return s.astype(str).str.split(".").str[0]


def load_list_events() -> pd.DataFrame:
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
    for c in ("net_amount", "net_rate", "pct_change", "turnover_rate", "amount"):
        tl[c] = pd.to_numeric(tl[c], errors="coerce")
    g = (
        tl.groupby(["date", "code"], sort=False)
        .agg(
            net_amount=("net_amount", "sum"),
            net_rate=("net_rate", "mean"),
            pct_change=("pct_change", "mean"),
            turnover_rate=("turnover_rate", "max"),
            amount=("amount", "sum"),
            reason=("reason", lambda s: "|".join(sorted(set(map(str, s))))),
        )
        .reset_index()
    )
    r = g["reason"].fillna("").astype(str)
    g["is_up"] = r.str.contains("涨幅|涨停|收盘价格涨", regex=True)
    g["is_down"] = r.str.contains("跌幅|跌停|收盘价格跌", regex=True)
    g["is_turn"] = r.str.contains("换手", regex=True)
    return g


def load_close_amount() -> tuple[pd.DataFrame, pd.DataFrame]:
    da = pd.read_parquet(
        "data_lake/price/daily_all.parquet",
        columns=["date", "code", "close", "amount"],
    )
    da["date"] = pd.to_datetime(da["date"])
    da["code"] = da["code"].astype(str)
    close = da.pivot_table(index="date", columns="code", values="close").sort_index()
    amount = da.pivot_table(index="date", columns="code", values="amount").sort_index()
    return close, amount


def load_earn_panels(close: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """avail_date 生效 ffill → date×code。"""
    fb = pd.read_parquet(
        "data_lake/fundamental_batch.parquet",
        columns=["code", "avail_date", "net_profit_yoy", "revenue_yoy", "roe"],
    )
    fb["code"] = fb["code"].astype(str).str.split(".").str[0]
    fb["avail_date"] = pd.to_datetime(fb["avail_date"])
    fb = fb.dropna(subset=["avail_date", "code"])
    out = {}
    for col in ("net_profit_yoy", "revenue_yoy", "roe"):
        piv = fb.pivot_table(
            index="avail_date", columns="code", values=col, aggfunc="last"
        ).sort_index()
        # ffill to trading days; shift(1) so T uses only strictly prior avail if same-day uncertainty
        aligned = piv.reindex(piv.index.union(close.index)).sort_index().ffill()
        aligned = aligned.reindex(close.index).reindex(columns=close.columns)
        out[col] = aligned.shift(1)
    return out


def attach_features(events: pd.DataFrame, close: pd.DataFrame, amount: pd.DataFrame, earn: dict) -> pd.DataFrame:
    dates = close.index.sort_values()
    pos = {d: i for i, d in enumerate(dates)}
    codes = list(close.columns)
    code_j = {c: j for j, c in enumerate(codes)}
    c_arr = close.to_numpy(dtype=float)
    a_arr = amount.reindex(index=dates, columns=codes).to_numpy(dtype=float)
    # ADV20 ending at t-1
    adv = (
        amount.reindex(index=dates, columns=codes)
        .rolling(20, min_periods=10)
        .mean()
        .shift(1)
        .to_numpy(dtype=float)
    )
    # earn arrays
    yoy = earn["net_profit_yoy"].reindex(index=dates, columns=codes).to_numpy(dtype=float)
    rev = earn["revenue_yoy"].reindex(index=dates, columns=codes).to_numpy(dtype=float)
    roe = earn["roe"].reindex(index=dates, columns=codes).to_numpy(dtype=float)

    rows = []
    for rec in events.itertuples(index=False):
        d, code = rec.date, rec.code
        if d not in pos or code not in code_j:
            continue
        i, j = pos[d], code_j[code]
        c0 = c_arr[i, j]
        if not np.isfinite(c0) or c0 <= 0:
            continue

        def px(k, j=j):
            if k < 0 or k >= len(dates):
                return np.nan
            v = c_arr[k, j]
            return v if np.isfinite(v) and v > 0 else np.nan

        c1, c2, c3 = px(i + 1), px(i + 2), px(i + 3)
        hold1 = c2 / c1 - 1.0 if np.isfinite(c1) and np.isfinite(c2) else np.nan
        hold2 = c3 / c1 - 1.0 if np.isfinite(c1) and np.isfinite(c3) else np.nan

        amt_t = a_arr[i, j]
        adv_t = adv[i, j]
        vol_ratio = (
            amt_t / adv_t
            if np.isfinite(amt_t) and np.isfinite(adv_t) and adv_t > 0
            else np.nan
        )

        rows.append(
            {
                "date": d,
                "code": code,
                "net_amount": rec.net_amount,
                "net_rate": rec.net_rate,
                "pct_change": rec.pct_change,
                "turnover_rate": rec.turnover_rate,
                "is_up": rec.is_up,
                "is_down": rec.is_down,
                "is_turn": rec.is_turn,
                "vol_ratio": vol_ratio,
                "yoy": yoy[i, j],
                "rev_yoy": rev[i, j],
                "roe": roe[i, j],
                "hold1": hold1,
                "hold2": hold2,
            }
        )
    return pd.DataFrame(rows)


def build_rules(df: pd.DataFrame) -> dict[str, pd.Series]:
    nb = df["net_amount"] > 0
    strong_b = nb & (df["net_rate"] > 5)
    mild_v = df["vol_ratio"].between(VOL_MILD_LO, VOL_MILD_HI)
    hot_v = df["vol_ratio"] > VOL_HOT
    cool_t = df["turnover_rate"] < TURN_COOL
    epos = df["yoy"] > 0
    ehi = df["yoy"] > YOY_HI
    roe_ok = df["roe"] > ROE_OK
    rev_pos = df["rev_yoy"] > 0
    # 业绩数据缺失不进业绩规则
    has_e = df["yoy"].notna()

    return {
        # 基线
        "all_list": pd.Series(True, index=df.index),
        "net_buy": nb,
        "strong_buy": strong_b,
        # 量
        "buy_mild_vol": nb & mild_v,
        "buy_hot_vol": nb & hot_v,
        "buy_cool_turn": nb & cool_t,
        # 业绩
        "buy_earn_pos": nb & has_e & epos,
        "buy_earn_hi": nb & has_e & ehi,
        "buy_roe_ok": nb & df["roe"].notna() & roe_ok,
        "buy_rev_pos": nb & df["rev_yoy"].notna() & rev_pos,
        # 量+业绩
        "buy_mildvol_earnpos": nb & mild_v & has_e & epos,
        "buy_mildvol_earnhi": nb & mild_v & has_e & ehi,
        "buy_coolturn_earnpos": nb & cool_t & has_e & epos,
        "buy_coolturn_earnhi_roe": nb & cool_t & has_e & ehi & roe_ok,
        "strong_mildvol_earnpos": strong_b & mild_v & has_e & epos,
        "strong_coolturn_earnhi": strong_b & cool_t & has_e & ehi,
        # 涨榜场景 + 量业绩
        "up_buy_mildvol_earn": df["is_up"] & nb & mild_v & has_e & epos,
        "up_buy_cool_earnhi": df["is_up"] & nb & cool_t & has_e & ehi,
        # 跌榜吸筹 + 业绩(质量抄底先验)
        "down_buy_earnpos": df["is_down"] & nb & has_e & epos,
        "down_buy_mildvol_earn": df["is_down"] & nb & mild_v & has_e & epos,
        # 对照: 热度+差业绩(应更差)
        "buy_hot_earnneg": nb & hot_v & has_e & (df["yoy"] <= 0),
    }


def summarize(ret: pd.Series, cost: float = ROUND_TRIP_COST) -> dict:
    r = ret.dropna()
    if len(r) < 30:
        return {
            "n": int(len(r)),
            "mean": None,
            "mean_net": None,
            "hit": None,
            "tstat_net": None,
            "std": None,
        }
    mean = float(r.mean())
    std = float(r.std())
    mean_net = mean - cost
    t_net = float(mean_net / (std / np.sqrt(len(r)))) if std > 0 else None
    return {
        "n": int(len(r)),
        "mean": mean,
        "mean_net": mean_net,
        "hit": float((r > 0).mean()),
        "std": std,
        "tstat_net": t_net,
    }


def split_eval(df: pd.DataFrame, mask: pd.Series, col: str) -> dict:
    sub = df.loc[mask]
    out = {}
    for name, lo, hi in (
        ("IS", "2018-01-01", IS_END),
        ("OOS", "2023-01-01", OOS_END),
        ("holdout", HOLDOUT, "2099-01-01"),
    ):
        m = (sub["date"] >= lo) & (sub["date"] <= hi)
        out[name] = summarize(sub.loc[m, col])
    return out


def main() -> int:
    print("=" * 72)
    print("  龙虎榜 + 量 + 业绩 → T+1/T+2 事件 L0")
    print(
        f"  mild_vol=[{VOL_MILD_LO},{VOL_MILD_HI}] hot>{VOL_HOT} "
        f"cool_turn<{TURN_COOL} yoy_hi>{YOY_HI} roe>{ROE_OK}"
    )
    print("=" * 72)

    print("[1/4] list events ...")
    events = load_list_events()
    print("[2/4] prices + ADV ...")
    close, amount = load_close_amount()
    print("[3/4] earnings PIT ...")
    earn = load_earn_panels(close)
    print("[4/4] features + rules ...")
    df = attach_features(events, close, amount, earn)
    print(f"  matched={len(df):,}  yoy_cover={df['yoy'].notna().mean():.1%}  vol_cover={df['vol_ratio'].notna().mean():.1%}")

    rules = build_rules(df)
    table = []
    print("\n### hold1 net(−30bp) | IS / OOS / n / hit / t_OOS")
    print(
        f"{'rule':<28} {'IS_net':>8} {'OOS_net':>8} {'IS_n':>6} {'OOS_n':>6} "
        f"{'OOS_hit':>7} {'OOS_t':>7}"
    )
    for name, mask in rules.items():
        for h in ("hold1", "hold2"):
            ev = split_eval(df, mask, h)
            table.append({"rule": name, "horizon": h, **ev})
            if h != "hold1":
                continue
            is_, oos = ev["IS"], ev["OOS"]

            def fnet(d):
                return f"{d['mean_net']:>+7.2%}" if d["mean_net"] is not None else f"{'n/a':>8}"

            def fn(d):
                return f"{d['n']:>6}" if d["n"] is not None else f"{'n/a':>6}"

            def fhit(d):
                return f"{d['hit']:>6.1%}" if d["hit"] is not None else f"{'n/a':>7}"

            def ft(d):
                return f"{d['tstat_net']:>7.2f}" if d["tstat_net"] is not None else f"{'n/a':>7}"

            print(
                f"{name:<28} {fnet(is_)} {fnet(oos)} {fn(is_)} {fn(oos)} {fhit(oos)} {ft(oos)}"
            )

    print("\n### shortlist hold1: IS_net>0 & OOS_net>0 & OOS_t>1.5 & OOS_n≥150")
    short = []
    for row in table:
        if row["horizon"] != "hold1":
            continue
        is_, oos = row["IS"], row["OOS"]
        if is_["mean_net"] is None or oos["mean_net"] is None:
            continue
        if (
            is_["mean_net"] > 0
            and oos["mean_net"] > 0
            and oos["tstat_net"] is not None
            and oos["tstat_net"] > 1.5
            and oos["n"] >= 150
        ):
            short.append(row)
            print(
                f"  {row['rule']}: IS={is_['mean_net']:+.2%} OOS={oos['mean_net']:+.2%} "
                f"n={oos['n']} t={oos['tstat_net']:.2f} hit={oos['hit']:.1%}"
            )
    if not short:
        print("  (none)")

    # best OOS by net among n>=200
    print("\n### hold1 OOS rank by mean_net (n≥200)")
    cands = []
    for row in table:
        if row["horizon"] != "hold1":
            continue
        oos = row["OOS"]
        if oos["mean_net"] is None or oos["n"] < 200:
            continue
        cands.append((oos["mean_net"], row["rule"], oos))
    cands.sort(reverse=True)
    for mean_net, name, oos in cands[:12]:
        print(
            f"  {name:<28} OOS_net={mean_net:+.2%} n={oos['n']} "
            f"hit={oos['hit']:.1%} t={oos['tstat_net']:.2f}"
        )

    print("\nADVISORY: L0 only; 加量+业绩若仍全负 → 榜后反转主导,过滤难翻盘")

    def clean(d):
        return {
            k: (None if v is None or (isinstance(v, float) and not np.isfinite(v)) else float(v) if isinstance(v, (float, np.floating)) else v)
            for k, v in d.items()
        }

    payload = {
        "meta": {
            "vol_mild": [VOL_MILD_LO, VOL_MILD_HI],
            "vol_hot": VOL_HOT,
            "turn_cool": TURN_COOL,
            "yoy_hi": YOY_HI,
            "roe_ok": ROE_OK,
            "cost": ROUND_TRIP_COST,
            "hold1": "T+1 close → T+2 close",
            "note": "top_list + volume + earnings T+1/T+2 L0; not alpha",
        },
        "coverage": {
            "n_events": len(df),
            "yoy_cover": float(df["yoy"].notna().mean()),
            "vol_cover": float(df["vol_ratio"].notna().mean()),
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
        "shortlist_hold1": [
            {"rule": r["rule"], "IS": clean(r["IS"]), "OOS": clean(r["OOS"])} for r in short
        ],
    }
    out = Path("scratch/probe_top_list_vol_earn_t1t2.json")
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"\nJSON → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
