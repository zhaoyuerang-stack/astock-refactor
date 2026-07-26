"""模拟盘共享执行引擎(真实盘 T+1 成交逻辑)——被 scripts/ops/paper_trade.py(CLI/Obsidian)
与 services/read/paper.py(web 操作卡)共同使用。

口径(与 paper_trade 一致,抽取时行为零变化):
  · T 日盘后出信号 → 记 pending → T+1 按 FILL_PRICE_MODE 成交(默认 close)
  · 停牌(当天无数据)不可买卖;一字涨停买不进、一字跌停卖不出(约束按开盘价判)
  · 等权 1/top_n,A股 100 股整数倍;成交/估值全用不复权价(daily_raw 的 raw_*)
  · 成本走 app_config.settings 的 CostModelConfig
注意:本模块无 chdir / sys.path 副作用,可被 API 进程安全 import。

账户路径参数化(WS-D 执行侧,paper 多账户并行实测):load_account / save_account /
append_trades / upsert_nav 现接受可选 ``account_fp`` / ``trades_fp`` / ``nav_fp`` 覆盖
入参,默认值 = 现有单账户常量(ACCOUNT_FP/TRADES_FP/NAV_FP)。legacy 调用方
(scripts/ops/paper_trade.py、services/read/paper.py)零改动、零行为变化 ——
它们不传新参数,继续读写同一份 paper/account.json 等文件。多账户管理器
(portfolio/paper_accounts.py)显式传入每账户独立路径,实现账本隔离。
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pandas as pd

from app_config.settings import get_settings

ROOT = Path(__file__).resolve().parents[1]

_cost_cfg = get_settings().cost

INIT_CAPITAL = 1_000_000.0
LEVERAGE = 1.0  # 模拟盘固定 1.0x (动态杠杆从 signal.band_exposure 传入)
BUY_COST = _cost_cfg.buy_cost
SELL_COST = _cost_cfg.sell_cost
ETF_BUY_COST = _cost_cfg.etf_buy_cost
ETF_SELL_COST = _cost_cfg.etf_sell_cost
LOT = 100

# ── 执行成交价模式 (2026-06-07 Task 1.2 audit 实证) ──
# close 模式 = T+1 14:55 收盘价成交 (盘中冲击消化后), 避开高开摩擦
FILL_PRICE_MODE = os.environ.get("PAPER_FILL_MODE", "close")

SIGNALS = ROOT / "signals"
PAPER = ROOT / "paper"
ACCOUNT_FP = PAPER / "account.json"
TRADES_FP = PAPER / "trades.csv"
NAV_FP = PAPER / "nav.csv"
RAW = ROOT / "data_lake/price/daily_raw"


def defensive_bond_authorized(bond: dict | None) -> bool:
    """债券轮动必须有独立 defensive leg 身份,不能只继承 alpha 主腿信号。"""
    if not bond:
        return False
    auth = bond.get("authorization") or {}
    return (
        auth.get("role") == "defensive"
        and bool(str(auth.get("family") or "").strip())
        and bool(str(auth.get("version") or "").strip())
        and bool(str(auth.get("spec_hash") or "").strip())
    )


def bond_authorization_block(bond: dict | None) -> tuple[str, str, str, str]:
    code = (bond or {}).get("code", "511010")
    name = (bond or {}).get("name", "国债ETF")
    side = "买入" if (bond or {}).get("enabled") else "卖出"
    return (side, code, name, "defensive overlay 未授权,拒绝债券轮动")


# ── 价格:全部不复权(daily_raw)──
def _raw(code):
    fp = RAW / f"{code}.parquet"
    return pd.read_parquet(fp) if fp.exists() else None


def get_close(code, date):
    """date(含)前最近不复权收盘——估值用。"""
    df = _raw(code)
    if df is None:
        return None
    df = df[pd.to_datetime(df["date"]) <= pd.Timestamp(date)]
    return float(df["raw_close"].iloc[-1]) if len(df) else None


def get_open(code, date):
    """date 当天不复权开盘——T+1 成交价;停牌(当天无数据/无 open)返回 None。"""
    df = _raw(code)
    if df is None or "raw_open" not in df.columns:
        return None
    row = df[pd.to_datetime(df["date"]) == pd.Timestamp(date)]
    if not len(row):
        return None
    o = row["raw_open"].iloc[0]
    return float(o) if pd.notna(o) and o > 0 else None


def get_fill_price(code, date, mode=None):
    """按 FILL_PRICE_MODE 决定 T+1 成交价 (audit 2026-06-07 推荐 'close').
    mode = "open" | "close" | "ohlc_mid" | "vwap_4"
    停牌 (当天无数据) 返回 None。
    """
    mode = mode or FILL_PRICE_MODE
    df = _raw(code)
    if df is None:
        return None
    row = df[pd.to_datetime(df["date"]) == pd.Timestamp(date)]
    if not len(row):
        return None
    if mode == "open":
        v = row.get("raw_open", row.get("raw_close")).iloc[0]
    elif mode == "close":
        v = row["raw_close"].iloc[0]
    elif mode == "ohlc_mid":
        o, c = row["raw_open"].iloc[0], row["raw_close"].iloc[0]
        if pd.isna(o) or pd.isna(c):
            return None
        v = (o + c) / 2
    elif mode == "vwap_4":
        o, h, l, c = (row["raw_open"].iloc[0], row["raw_high"].iloc[0],
                      row["raw_low"].iloc[0], row["raw_close"].iloc[0])
        if any(pd.isna(x) for x in [o, h, l, c]):
            return None
        v = (o + h + l + c) / 4
    else:
        raise ValueError(f"unknown FILL_PRICE_MODE: {mode}")
    return float(v) if pd.notna(v) and v > 0 else None


def get_prev_close(code, date):
    """date 前一交易日不复权收盘——算涨跌停基准。"""
    df = _raw(code)
    if df is None:
        return None
    df = df[pd.to_datetime(df["date"]) < pd.Timestamp(date)]
    return float(df["raw_close"].iloc[-1]) if len(df) else None


# ── ETF 价格(债券轮动腿,data_lake/cross_asset/etf)──
def _etf_df(code):
    from lake.cross_asset import load_etf_daily
    return load_etf_daily(code)


def _etf_col(df, col):
    """优先不复权 raw_* 列(人在券商 App 看到的盘口价);旧格式文件 fallback 后复权列。"""
    return f"raw_{col}" if f"raw_{col}" in df.columns else col


def get_etf_close(code, date):
    """date(含)前最近 ETF 不复权收盘——估值/参考价用;无数据返回 None。"""
    df = _etf_df(code)
    if df is None:
        return None
    df = df[df["date"] <= pd.Timestamp(date)]
    if not len(df):
        return None
    v = df[_etf_col(df, "close")].iloc[-1]
    return float(v) if pd.notna(v) and v > 0 else None


def get_etf_fill(code, date, mode=None):
    """ETF T+1 成交价,按 FILL_PRICE_MODE(与股票同口径);当日无数据(停牌/数据滞后)返回 None。"""
    mode = mode or FILL_PRICE_MODE
    df = _etf_df(code)
    if df is None:
        return None
    row = df[df["date"] == pd.Timestamp(date)]
    if not len(row):
        return None
    o, c = row[_etf_col(df, "open")].iloc[0], row[_etf_col(df, "close")].iloc[0]
    if mode == "open":
        v = o
    elif mode == "close":
        v = c
    elif mode == "ohlc_mid":
        v = (o + c) / 2 if pd.notna(o) and pd.notna(c) else None
    else:  # vwap_4 等其他模式对 ETF 退化为 close(国债ETF 日内波动 <0.1%,差异可忽略)
        v = c
    return float(v) if v is not None and pd.notna(v) and v > 0 else None


# ── 涨跌停(按板块)──
def limit_pct(code, name):
    if name and "ST" in str(name).upper():
        return 0.05
    if code[:3] in ("300", "301", "688"):   # 创业板/科创
        return 0.20
    return 0.10                              # 主板(北交所已不在universe)


def buyable_open(code, date, name):
    """T+1 可买入价 (按 FILL_PRICE_MODE):未停牌且非开盘涨停;否则 None。
    涨停约束仍按 T+1 开盘价判 (即使 fill_mode = close, 开盘涨停依然买不进)。"""
    o = get_open(code, date)   # 涨跌停约束用开盘价
    if o is None:
        return None
    pc = get_prev_close(code, date)
    if pc and o >= round(pc * (1 + limit_pct(code, name)), 2) - 1e-6:   # 开盘即涨停(涨停价按分四舍五入)
        return None
    # 实际成交价按 FILL_PRICE_MODE
    return get_fill_price(code, date)


def sellable_open(code, date, name):
    """T+1 可卖出价 (按 FILL_PRICE_MODE):未停牌且非开盘跌停;否则 None。"""
    o = get_open(code, date)   # 涨跌停约束用开盘价
    if o is None:
        return None
    pc = get_prev_close(code, date)
    if pc and o <= round(pc * (1 - limit_pct(code, name)), 2) + 1e-6:   # 开盘即跌停(跌停价按分四舍五入)
        return None
    # 实际成交价按 FILL_PRICE_MODE
    return get_fill_price(code, date)


# ── account / 流水 IO ──
def load_names():
    fp = ROOT / "data_lake/meta/codes.parquet"
    if fp.exists():
        df = pd.read_parquet(fp)
        return dict(zip(df["code"].astype(str), df["name"], strict=True))
    return {}


def load_account(account_fp: Path | None = None):
    """account_fp=None(默认)→ 读单账户 legacy 路径 ACCOUNT_FP,行为与重构前逐字节一致。
    多账户管理器传入各自 <account_dir>/account.json 实现账本隔离。"""
    fp = account_fp or ACCOUNT_FP
    if fp.exists():
        return json.loads(fp.read_text())
    return {"init_capital": INIT_CAPITAL, "inception": None, "cash": INIT_CAPITAL,
            "positions": {}, "pending": None, "last_date": None}


def save_account(acc, account_fp: Path | None = None):
    fp = account_fp or ACCOUNT_FP
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(acc, ensure_ascii=False, indent=2))


def append_trades(rows, trades_fp: Path | None = None):
    fp = trades_fp or TRADES_FP
    fp.parent.mkdir(parents=True, exist_ok=True)
    new = not fp.exists()
    with fp.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "code", "name", "side", "shares", "price", "notional", "cost", "cash_after"])
        w.writerows(rows)


def upsert_nav(date, nav, cash, pos_value, ret, nav_fp: Path | None = None):
    fp = nav_fp or NAV_FP
    fp.parent.mkdir(parents=True, exist_ok=True)
    rows = {}
    if fp.exists():
        with fp.open() as f:
            for r in csv.DictReader(f):
                rows[r["date"]] = r
    rows[date] = {"date": date, "nav": f"{nav:.2f}", "cash": f"{cash:.2f}",
                  "position_value": f"{pos_value:.2f}", "total_return": f"{ret:.6f}"}
    with fp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "nav", "cash", "position_value", "total_return"])
        w.writeheader()
        for d in sorted(rows):
            w.writerow(rows[d])


# ── T+1 成交:把持仓调到 target ──
def execute_to_target(acc, date, target, top_n, names, trades, blocked, leverage=None, bond=None):
    """leverage: 动态杠杆 (从 signal 的 band_exposure 传入). 默认 LEVERAGE 常量 (1.0).
    bond: 债券轮动指令 {"enabled": bool, "code", "name"}(来自 pending.bond);None = 无轮动(行为不变)。
    执行顺序(固定四段,bear 稳态/bull 转换/regime 与 in_market 背离日 均正确):
      1 卖出掉出 target 的股票 → 2 轮动关闭时先卖光债券(资金回笼) →
      3 买入新进 target 的股票 → 4 轮动开启时剩余全部闲置现金买入债券 ETF
    """
    if leverage is None:
        leverage = LEVERAGE
    bond_authorized = defensive_bond_authorized(bond)
    bond_requested = bool(bond and bond.get("enabled"))
    bond_enabled = bool(bond_requested and bond_authorized)
    if bond is not None and not bond_authorized:
        has_legacy_holding = bool((acc.get("bond") or {}).get("shares"))
        if bond_requested or has_legacy_holding:
            blocked.append(bond_authorization_block(bond))
    target = set(target)
    # 1. 卖出:持仓中不在 target 的(掉出名单),用 date 开盘价
    for code in list(acc["positions"]):
        if code in target:
            continue
        price = sellable_open(code, date, names.get(code))
        if price is None:
            blocked.append(("卖出", code, names.get(code, code), "停牌/一字跌停,卖不出"))
            continue
        pos = acc["positions"].pop(code)
        notional = pos["shares"] * price
        cost = notional * SELL_COST
        acc["cash"] += notional - cost
        trades.append([date, code, names.get(code, code), "SELL", pos["shares"],
                       round(price, 3), round(notional, 2), round(cost, 2), round(acc["cash"], 2)])
    # 2. 轮动关闭(BULL)且持有债券 → 先卖光 ETF,资金回笼供买股。
    #    fail-closed(有意设计,勿"修"):未授权时连卖出也不自动执行,遗留持仓留人工处置
    #    ——未授权指令不得被当成可执行轮动(见 test_paper_etf.py L85 与 run_daily 部署纪律)。
    held = acc.get("bond")
    if held and held.get("shares") and not bond_enabled and bond_authorized:
        price = get_etf_fill(held["code"], date)
        if price is None:
            blocked.append(("卖出", held["code"], held.get("name", held["code"]), "ETF 当日无数据,卖不出"))
        else:
            notional = held["shares"] * price
            cost = notional * ETF_SELL_COST
            acc["cash"] += notional - cost
            trades.append([date, held["code"], held.get("name", held["code"]), "SELL", held["shares"],
                           round(price, 3), round(notional, 2), round(cost, 2), round(acc["cash"], 2)])
            acc["bond"] = None
    # 3. 买入:target 中未持有的(新进名单),等权 total_equity/top_n,用 date 开盘价
    pos_value = sum(p["shares"] * (get_close(c, date) or p["avg_cost"]) for c, p in acc["positions"].items())
    budget_each = (acc["cash"] + pos_value) * leverage / top_n
    for code in target:
        if code in acc["positions"]:
            continue
        price = buyable_open(code, date, names.get(code))
        if price is None:
            blocked.append(("买入", code, names.get(code, code), "停牌/一字涨停,买不进"))
            continue
        shares = int(budget_each / price // LOT) * LOT
        if shares <= 0:
            continue
        notional = shares * price
        cost = notional * BUY_COST
        if notional + cost > acc["cash"]:
            continue
        acc["cash"] -= notional + cost
        acc["positions"][code] = {"shares": shares, "avg_cost": round(price, 3)}
        trades.append([date, code, names.get(code, code), "BUY", shares,
                       round(price, 3), round(notional, 2), round(cost, 2), round(acc["cash"], 2)])
    # 4. 轮动开启(BEAR)→ 剩余全部闲置现金买入债券 ETF(已持有则继续补足)
    if bond_enabled:
        code = bond.get("code", "511010")
        bname = bond.get("name", "国债ETF")
        price = get_etf_fill(code, date)
        if price is None:
            blocked.append(("买入", code, bname, "ETF 当日无数据,买不进"))
        else:
            shares = int(acc["cash"] / (price * (1 + ETF_BUY_COST)) // LOT) * LOT
            if shares > 0:
                notional = shares * price
                cost = notional * ETF_BUY_COST
                acc["cash"] -= notional + cost
                held = acc.get("bond")
                if held and held.get("code") == code and held.get("shares"):
                    total = held["shares"] + shares
                    held["avg_cost"] = round((held["avg_cost"] * held["shares"] + notional) / total, 3)
                    held["shares"] = total
                else:
                    acc["bond"] = {"code": code, "name": bname, "shares": shares, "avg_cost": round(price, 3)}
                trades.append([date, code, bname, "BUY", shares,
                               round(price, 3), round(notional, 2), round(cost, 2), round(acc["cash"], 2)])


def valuation(acc, date):
    pos_value = 0.0
    detail = []
    for code, pos in acc["positions"].items():
        price = get_close(code, date)
        mv = pos["shares"] * price if price else 0.0
        pos_value += mv
        pnl = (price - pos["avg_cost"]) * pos["shares"] if price else 0.0
        detail.append({"code": code, "shares": pos["shares"], "cost": pos["avg_cost"],
                       "price": price, "mv": mv, "pnl": pnl})
    held = acc.get("bond")
    if held and held.get("shares"):
        price = get_etf_close(held["code"], date)
        mv = held["shares"] * price if price else 0.0
        pos_value += mv
        pnl = (price - held["avg_cost"]) * held["shares"] if price else 0.0
        detail.append({"code": held["code"], "name": held.get("name", held["code"]),
                       "shares": held["shares"], "cost": held["avg_cost"],
                       "price": price, "mv": mv, "pnl": pnl, "asset": "etf"})
    return acc["cash"] + pos_value, pos_value, detail


def estimate_basket(date, codes, equity, top_n, names):
    """按参考价(date 收盘)估算等权建仓清单——给"明日计划"/预览展示,实际按 T+1 开盘成交。"""
    budget_each = equity * LEVERAGE / top_n
    rows = []
    for code in codes:
        ref = get_close(code, date)
        if not ref:
            continue
        shares = int(budget_each / ref // LOT) * LOT
        if shares > 0:
            rows.append((code, names.get(code, code), shares, ref, shares * ref))
    return rows


def estimate_bond_order(date, cash_avail, code="511010"):
    """按参考价(date 收盘)估算「全部闲置现金买入债券 ETF」——给明日计划/web 操作卡展示。
    返回 (shares, ref_price, est_notional) 或 None(无数据/现金不足一手)。"""
    ref = get_etf_close(code, date)
    if not ref:
        return None
    shares = int(cash_avail / (ref * (1 + ETF_BUY_COST)) // LOT) * LOT
    if shares <= 0:
        return None
    return shares, ref, shares * ref
