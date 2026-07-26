"""个股基本面画像读层 (fundamental profile).

从 data_lake 真实财务/估值/价量数据,按 ann_date 防未来对齐(只取已披露),
喂给 factory.fundamental 的确定性引擎,产出 议价权 / 现金循环周期 / 估值预期差。
缺字段(如资负表科目未摄取)→ 对应指标返回 None,绝不编造。

服务读层:允许 import factory(受控接缝);不调 LLM(讲解在 Agent 层 + 数字护栏)。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from factory.fundamental import (
    BargainingPowerEstimator,
    FinancialProfile,
    MarketPricingProfile,
    PricingGapEstimator,
)

ROOT = Path(__file__).resolve().parents[2]
LAKE = ROOT / "data_lake"


def _norm_code(code: str) -> str:
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    if len(digits) < 6:
        raise ValueError("stock code must contain 6 digits")
    return digits[:6]


def _name(code: str) -> str:
    fp = LAKE / "meta" / "codes.parquet"
    if not fp.exists():
        return code
    df = pd.read_parquet(fp, columns=["code", "name"])
    row = df[df["code"].astype(str) == code]
    return code if row.empty else str(row.iloc[0]["name"])


def _latest_disclosed(fname: str, code: str) -> dict:
    """取该股最近**已披露**(ann_date 最大)的一行,防未来函数。无则空 dict。"""
    fp = LAKE / "financials" / fname
    if not fp.exists():
        return {}
    df = pd.read_parquet(fp)
    if "ts_code" not in df.columns:
        return {}
    sub = df[df["ts_code"].astype(str).str.startswith(f"{code}.")]
    if sub.empty:
        return {}
    sort_cols = [c for c in ("ann_date", "end_date") if c in sub.columns]
    if sort_cols:
        sub = sub.sort_values(sort_cols)
    row = sub.iloc[-1].to_dict()
    return {k: (v.item() if hasattr(v, "item") else v) for k, v in row.items()}


def _num(d: dict, key: str) -> float | None:
    v = d.get(key)
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _sum_opt(*vals: float | None) -> float | None:
    present = [v for v in vals if v is not None]
    return sum(present) if present else None


def _annualize(flow: float | None, end_date) -> float | None:
    """财务表为年内累计 → 年化:×12/报告期月份(Q1=3→×4,Q2=6→×2,Q3=9→×1.33,Q4=12→×1)。
    用于把时点存量(应收/应付/存货)和年化流量匹配,算正确的周转天数/BPI。"""
    if flow is None:
        return None
    try:
        month = int(str(end_date)[4:6])
    except (TypeError, ValueError):
        return flow
    if month not in (3, 6, 9, 12):
        return flow
    return flow * 12.0 / month


def _daily_basic_valuation(code: str) -> dict:
    """最新 PE/PB + 其 5 年历史分位(0-1)。"""
    fp = LAKE / "daily_basic" / "daily_basic_all.parquet"
    if not fp.exists():
        return {}
    df = pd.read_parquet(fp)
    if "ts_code" not in df.columns:
        return {}
    sub = df[df["ts_code"].astype(str).str.startswith(f"{code}.")]
    if sub.empty:
        return {}
    date_col = "trade_date" if "trade_date" in sub.columns else sub.columns[0]
    sub = sub.sort_values(date_col)
    out: dict = {}
    for col, pct_key in (("pe", "pe_pctile"), ("pb", "pb_pctile")):
        if col not in sub.columns:
            continue
        series = pd.to_numeric(sub[col], errors="coerce").dropna()
        series = series[series > 0]                 # PE/PB 负值不计入分位
        if series.empty:
            continue
        latest = float(series.iloc[-1])
        hist = series.iloc[-1250:]                  # ~5 年交易日
        out[col] = round(latest, 4)
        if len(hist) >= 60:
            out[pct_key] = round(float((hist <= latest).mean()), 4)
    return out


def _returns(code: str) -> dict:
    fp = LAKE / "price" / "daily" / f"{code}.parquet"
    if not fp.exists():
        return {}
    px = pd.read_parquet(fp).sort_values("date")
    close = px["close"].astype(float)
    out = {}
    if len(close) > 20:
        out["ret_20d"] = round(float(close.iloc[-1] / close.iloc[-21] - 1), 4)
    if len(close) > 60:
        out["ret_60d"] = round(float(close.iloc[-1] / close.iloc[-61] - 1), 4)
    return out


def fundamental_profile(code: str) -> dict:
    """个股基本面分析画像:议价权 + 现金循环周期 + 质量 + 估值预期差。"""
    code = _norm_code(code)
    name = _name(code)
    income = _latest_disclosed("income_all.parquet", code)
    balance = _latest_disclosed("balancesheet_all.parquet", code)
    fina = _latest_disclosed("fina_indicator_all.parquet", code)

    receivables = _sum_opt(_num(balance, "accounts_receiv"), _num(balance, "notes_receiv"))
    payables = _sum_opt(_num(balance, "acct_payable"), _num(balance, "notes_payable"))
    fp = FinancialProfile(
        code=code, name=name,
        # 流量年化:财务表为年内累计(Q1=3月/Q2=6月/Q3=9月/Q4=12月),
        # 时点存量÷流量算周转天数前须 ×12/月份,否则 Q1 周转天数虚高 ~4 倍
        revenue=_annualize(_num(income, "revenue"), income.get("end_date")),
        cost=_annualize(_num(income, "oper_cost"), income.get("end_date")),
        ebit=_annualize(_num(income, "ebit"), income.get("end_date")),  # 与 revenue 同步年化,保 ebit/revenue 比率不变
        receivables=receivables,
        payables=payables,
        inventory=_num(balance, "inventories"),
        gross_margin=(_num(fina, "grossprofit_margin") / 100.0
                      if _num(fina, "grossprofit_margin") is not None else None),  # tushare 为百分数
    )
    bp = BargainingPowerEstimator()
    bargaining = bp.assess(fp)

    val = _daily_basic_valuation(code)
    rets = _returns(code)
    mp = MarketPricingProfile(
        code=code, name=name,
        pe_percentile=val.get("pe_pctile"),
        pb_percentile=val.get("pb_pctile"),
        return_20d=rets.get("ret_20d"),
        return_60d=rets.get("ret_60d"),
        analyst_revision_ratio=None,           # 暂无分析师上修数据(Phase 2 接入)
    )
    pg = PricingGapEstimator()
    pricing = pg.assess(bargaining["pricing_power_score"], mp)

    has_balance_items = receivables is not None or payables is not None or fp.inventory is not None
    warnings = []
    if not has_balance_items:
        warnings.append("资负表应收/应付/存货未摄取,议价权 BPI/CCC 暂为 None;跑 update_tushare --interface balancesheet 后点亮")
    if not rets:
        warnings.append("缺价格数据,动量/估值反应不完整")

    return {
        "code": code,
        "name": name,
        "as_of": income.get("ann_date") or balance.get("ann_date"),
        "quality": {
            "gross_margin": fp.gross_margin,
            "net_margin": (_num(fina, "netprofit_margin") / 100.0
                           if _num(fina, "netprofit_margin") is not None else None),
            "roe": _num(fina, "roe"),
            "or_yoy": _num(fina, "or_yoy"),
            "netprofit_yoy": _num(fina, "netprofit_yoy"),
        },
        "bargaining": bargaining,
        "valuation": {**val, **rets, "reaction_score": pricing["reaction_score"]},
        "pricing": {"pricing_gap": pricing["pricing_gap"], "pricing_state": pricing["pricing_state"]},
        "warnings": warnings,
        "data_sources": ["financials/income_all", "financials/balancesheet_all",
                         "financials/fina_indicator_all", "daily_basic", f"price/daily/{code}"],
    }
