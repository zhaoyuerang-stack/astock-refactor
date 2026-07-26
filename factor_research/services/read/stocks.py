"""Single-stock read views backed by data_lake."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LAKE = ROOT / "data_lake"


def normalize_code(code: str) -> str:
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    if len(digits) < 6:
        raise ValueError("stock code must contain 6 digits")
    return digits[:6]


def resolve_stock_code(query: str) -> str | None:
    """把"汇川技术 / 600519 / 贵州茅台怎么样"解析成 6 位代码。
    先认 6 位数字;否则用 data_lake 的 code↔name 映射按名字匹配(精确优先,再含子串)。
    解析不到 → None(由调用方请用户澄清,绝不瞎猜)。"""
    import re

    m = re.search(r"(?<!\d)(\d{6})(?!\d)", query or "")
    if m:
        return m.group(1)
    fp = LAKE / "meta" / "codes.parquet"
    if not query or not fp.exists():
        return None
    df = pd.read_parquet(fp, columns=["code", "name"])
    names = df["name"].astype(str)
    exact = df[names == query.strip()]
    if not exact.empty:
        return str(exact.iloc[0]["code"])
    contains = df[names.apply(lambda n: n in query)]   # "汇川技术怎么样" 含 "汇川技术"
    if contains.empty:
        contains = df[names.apply(lambda n: len(n) >= 2 and n in query)]
    if contains.empty:
        return None
    # 多个命中取最长名(最具体),避免 "平安" 误中
    best = contains.iloc[contains["name"].astype(str).str.len().argmax()]
    return str(best["code"])


def _stock_name(code: str) -> str:
    fp = LAKE / "meta" / "codes.parquet"
    if not fp.exists():
        return code
    df = pd.read_parquet(fp, columns=["code", "name"])
    row = df[df["code"].astype(str) == code]
    if row.empty:
        return code
    return str(row.iloc[0]["name"])


def _latest_by_ts_code(fp: Path, code: str) -> dict:
    if not fp.exists():
        return {}
    ts_prefix = f"{code}."
    df = pd.read_parquet(fp)
    if "ts_code" not in df.columns:
        return {}
    sub = df[df["ts_code"].astype(str).str.startswith(ts_prefix)]
    if sub.empty:
        return {}
    date_col = "trade_date" if "trade_date" in sub.columns else sub.columns[0]
    row = sub.sort_values(date_col).iloc[-1]
    out = {}
    for k, v in row.to_dict().items():
        if hasattr(v, "item"):
            v = v.item()
        out[k] = v
    return out


def stock_profile(code: str) -> dict:
    code = normalize_code(code)
    price_fp = LAKE / "price" / "daily" / f"{code}.parquet"
    if not price_fp.exists():
        raise FileNotFoundError(f"stock price not found: {code}")

    px = pd.read_parquet(price_fp).sort_values("date")
    if px.empty:
        raise ValueError(f"empty price data: {code}")
    last = px.iloc[-1]
    close = px["close"].astype(float)
    ret_20d = float(close.iloc[-1] / close.iloc[-21] - 1) if len(close) > 20 else None
    ret_60d = float(close.iloc[-1] / close.iloc[-61] - 1) if len(close) > 60 else None

    latest_basic = _latest_by_ts_code(LAKE / "daily_basic" / "daily_basic_all.parquet", code)
    latest_moneyflow = _latest_by_ts_code(LAKE / "moneyflow" / "moneyflow_all.parquet", code)

    # 铁律3 复权陷阱:price/daily 是后复权价(回测口径),不是真实股价。
    # 真实股价由 daily_basic 的总市值/总股本推算(总市值万元 / 总股本万股 = 元)。
    price_cny = None
    total_mv, total_share = latest_basic.get("total_mv"), latest_basic.get("total_share")
    if total_mv and total_share:
        try:
            price_cny = round(float(total_mv) / float(total_share), 2)
        except (TypeError, ValueError, ZeroDivisionError):
            price_cny = None

    warnings = []
    if price_cny is not None:
        warnings.append("price/daily 为后复权价(回测口径),非真实股价;真实股价由总市值/总股本推算")

    return {
        "code": code,
        "name": _stock_name(code),
        "price_cny": price_cny,                      # 不复权真实股价(元),用于展示
        "basic_date": latest_basic.get("trade_date"),  # 估值/市值/真实股价对应日期
        "latest_price": {                            # 后复权 OHLC:仅供算收益,勿当股价展示
            "date": str(last["date"])[:10],
            "open": float(last["open"]),
            "high": float(last["high"]),
            "low": float(last["low"]),
            "close": float(last["close"]),
            "close_is_adjusted": True,
            "volume": float(last["volume"]),
            "amount": float(last["amount"]),
        },
        "returns": {                                 # 后复权区间收益率(比率正确)
            "ret_20d": ret_20d,
            "ret_60d": ret_60d,
        },
        "daily_basic": latest_basic,
        "moneyflow": latest_moneyflow,
        "data_sources": [
            f"price/daily/{code}.parquet",
            "daily_basic/daily_basic_all.parquet" if latest_basic else "",
            "moneyflow/moneyflow_all.parquet" if latest_moneyflow else "",
        ],
        "warnings": warnings,
    }
