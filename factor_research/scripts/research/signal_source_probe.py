#!/usr/bin/env python3
"""信号源 / 因子正交性 + IS-OOS probe(确定性、可复现、可审计)。

回答关于一个候选因子(尤其"新信息源")的四个 L0 问题:
  ① 原始 rank-IC —— 有没有横截面预测力?
  ② 残差 IC(去 size/流动性暴露后)—— 是否**正交于"小盘/流动性"簇**(本系统反复坍缩的方向)?
  ③ 风格相关(对 size/流动性/动量)—— 是不是某个已知风格的伪装?
  ④ IS vs OOS(切于 cutoff)—— 样本外**塌不塌**(留存率)?

边界(铁律):本脚本只产 **L0 证据,不是 alpha**——不扣成本、无 DSR/PBO/容量/9-Gate。
判断"是否有效/可入册"归确定性门禁 + workflow(R-WF-001),本脚本**只算 + 报,不裁决**(R-LLM-001)。
排序/解读以"正交增量"为先,不以裸 IC/收益为先(避免诱导过拟合)。

IC 用 canonical ``engine.factor_analysis.calc_ic / newey_west_icir``;中性化为标准截面 OLS 残差
(与 ``engine.neutralize`` 同法 lstsq,controls 取 size/流动性,而非其行业+市值口径)。

用法:
  python scripts/research/signal_source_probe.py \
      --factor factors.northbound:northbound_accumulation --param window=20 \
      --universe northbound --start 2018-01-01 --cutoff 2022-12-31 --end 2024-12-31
  # --universe: all | northbound   --json <path> 落 JSON
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from engine.factor_analysis import calc_ic, newey_west_icir  # noqa: E402

LAKE = ROOT / "data_lake"


def _to6(s: pd.Series) -> pd.Series:
    return s.astype(str).str.split(".").str[0]


def _load_close(universe: str) -> pd.DataFrame:
    da = pd.read_parquet(LAKE / "price/daily_all.parquet", columns=["date", "code", "close"])
    if universe == "northbound":
        nb = pd.read_parquet(LAKE / "capital/northbound_all.parquet", columns=["code"])
        uni = set(nb["code"].astype(str))
        da = da[da["code"].astype(str).isin(uni)]
    return da.pivot_table(index="date", columns="code", values="close").sort_index()


def _load_controls(close: pd.DataFrame) -> dict[str, pd.DataFrame]:
    db = pd.read_parquet(
        LAKE / "daily_basic/daily_basic_all.parquet",
        columns=["ts_code", "trade_date", "circ_mv", "turnover_rate"],
    )
    db["code"] = _to6(db["ts_code"])
    db["date"] = pd.to_datetime(db["trade_date"], format="%Y%m%d")
    size = np.log(db.pivot_table(index="date", columns="code", values="circ_mv"))
    size = size.reindex(close.index).ffill()
    liq = db.pivot_table(index="date", columns="code", values="turnover_rate").reindex(close.index).ffill()
    mom = close.pct_change(60, fill_method=None)
    return {"size": size, "liquidity": liq, "momentum": mom}


def _monthly_rebalance(close: pd.DataFrame, start: str, end: str) -> list:
    d = close.index
    rb = pd.Series(d, index=d).groupby([d.year, d.month]).last().tolist()
    return [x for x in rb if pd.Timestamp(start) <= x <= pd.Timestamp(end)]


def _forward_returns(close: pd.DataFrame, rb: list) -> pd.DataFrame:
    return pd.DataFrame({rb[i]: close.loc[rb[i + 1]] / close.loc[rb[i]] - 1 for i in range(len(rb) - 1)}).T


def _neutralize(fac: pd.DataFrame, controls: list[pd.DataFrame]) -> pd.DataFrame:
    """逐截面 OLS 残差:去掉 controls(标准化)的线性暴露。与 engine.neutralize 同法(lstsq 残差)。"""
    out = fac * np.nan
    for t in fac.index:
        cols = [c.loc[t] for c in controls if t in c.index]
        if not cols:
            continue
        d = pd.concat([fac.loc[t]] + cols, axis=1).dropna()
        if len(d) < 30:
            continue
        y = d.iloc[:, 0].values
        x = d.iloc[:, 1:].values
        x = (x - x.mean(0)) / (x.std(0) + 1e-9)
        x = np.column_stack([np.ones(len(x)), x])
        beta = np.linalg.lstsq(x, y, rcond=None)[0]
        out.loc[t, d.index] = y - x @ beta
    return out


def _xcorr(a: pd.DataFrame, b: pd.DataFrame) -> float | None:
    cs = [
        a.loc[t].corr(b.loc[t], method="spearman")
        for t in a.index
        if t in b.index and a.loc[t].notna().sum() > 30
    ]
    cs = [c for c in cs if pd.notna(c)]
    return round(float(np.mean(cs)), 3) if cs else None


def _seg_ic(fac: pd.DataFrame, fwd: pd.DataFrame, lo: str, hi: str) -> dict | None:
    idx = [t for t in fac.index if pd.Timestamp(lo) <= t <= pd.Timestamp(hi) and t in fwd.index]
    if not idx:
        return None
    ic = calc_ic(fac.reindex(idx), fwd.reindex(idx), method="rank")
    return {"ic": round(float(ic.mean()), 4), "icir": round(float(newey_west_icir(ic)), 2), "months": len(idx)}


def _retention(seg_is: dict | None, seg_oos: dict | None) -> str:
    if not seg_is or not seg_oos or not seg_is["ic"]:
        return "—"
    return f"{seg_oos['ic'] / seg_is['ic'] * 100:.0f}%"


def probe(factor_ref: str, params: dict, universe: str, start: str, cutoff: str, end: str) -> dict:
    mod_name, fn_name = factor_ref.split(":")
    factor_fn = getattr(importlib.import_module(mod_name), fn_name)

    close = _load_close(universe)
    fac_full = factor_fn(close, **params)
    rb = _monthly_rebalance(close, start, end)
    fwd = _forward_returns(close, rb)
    rb2 = [t for t in rb if t in fwd.index]
    fac = fac_full.reindex(rb2)

    controls = {k: v.reindex(rb2) for k, v in _load_controls(close).items()}
    resid = _neutralize(fac, [controls["size"], controls["liquidity"]])

    return {
        "factor": factor_ref,
        "params": params,
        "universe": universe,
        "window": {"start": start, "cutoff": cutoff, "end": end},
        "raw_ic": {
            "full": _seg_ic(fac, fwd, start, end),
            "IS": _seg_ic(fac, fwd, start, cutoff),
            "OOS": _seg_ic(fac, fwd, cutoff, end),
        },
        "residual_ic_size_liq": {
            "full": _seg_ic(resid, fwd, start, end),
            "IS": _seg_ic(resid, fwd, start, cutoff),
            "OOS": _seg_ic(resid, fwd, cutoff, end),
        },
        "style_corr": {k: _xcorr(fac, controls[k]) for k in controls},
    }


def _print_report(r: dict) -> None:
    print("=" * 78)
    print(f"信号源 probe(L0,非 alpha)| {r['factor']} {r['params']} | universe={r['universe']}")
    print(f"窗口 {r['window']['start']} → cutoff {r['window']['cutoff']} → {r['window']['end']}")
    print("=" * 78)
    raw, res = r["raw_ic"], r["residual_ic_size_liq"]

    def row(label, seg):
        if not seg:
            return f"  {label:18} —"
        return f"  {label:18} IC={seg['ic']:>8.4f}  ICIR={seg['icir']:>5.2f}  ({seg['months']}月)"

    print("① 原始 rank-IC:")
    for k in ("IS", "OOS", "full"):
        print(row(k, raw[k]))
    print(f"  → OOS/IS 留存(原始): {_retention(raw['IS'], raw['OOS'])}")
    print("② 残差 IC(去 size/流动性 → 是否正交于小盘/流动性簇):")
    for k in ("IS", "OOS", "full"):
        print(row(k, res[k]))
    print(f"  → OOS/IS 留存(残差): {_retention(res['IS'], res['OOS'])}")
    if res["full"] and raw["full"] and raw["full"]["ic"]:
        keep = res["full"]["ic"] / raw["full"]["ic"] * 100
        print(f"  → 残差/原始 IC(正交保留率): {keep:.0f}%  (~100%=完全正交;大幅缩水=size/流动性代理)")
    print("③ 风格相关(spearman,|值|大=该风格的伪装):")
    print("   " + "  ".join(f"{k}={v}" for k, v in r["style_corr"].items()))
    print("-" * 78)
    print("诚实边界:仅 L0 证据,非 alpha(无成本/DSR/PBO/容量/9-Gate)。入册走 workflow(R-WF-001)。")


def main() -> None:
    ap = argparse.ArgumentParser(description="信号源/因子正交性 + IS-OOS probe(L0,确定性)")
    ap.add_argument("--factor", required=True, help="module:function,如 factors.northbound:northbound_accumulation")
    ap.add_argument("--param", action="append", default=[], help="k=v(可多次),如 --param window=20")
    ap.add_argument("--universe", default="all", choices=["all", "northbound"])
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--cutoff", default="2022-12-31")
    ap.add_argument("--end", default="2024-12-31")
    ap.add_argument("--json", default="", help="落 JSON 路径(可选)")
    a = ap.parse_args()

    params: dict = {}
    for p in a.param:
        k, v = p.split("=", 1)
        if v.lstrip("-").isdigit():
            params[k] = int(v)
        elif v.replace(".", "", 1).lstrip("-").isdigit():
            params[k] = float(v)
        else:
            params[k] = v

    r = probe(a.factor, params, a.universe, a.start, a.cutoff, a.end)
    _print_report(r)
    if a.json:
        Path(a.json).write_text(json.dumps(r, ensure_ascii=False, indent=2))
        print(f"JSON 落 {a.json}")


if __name__ == "__main__":
    main()
