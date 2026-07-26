"""Fetch cross-asset ETF daily data into data_lake/cross_asset/etf/.

5 个核心 ETF (按 ROADMAP 阶段 5 Phase 2.1):
  511010  国债 ETF        利率敏感, 与股票 corr ~0
  518880  黄金 ETF        通胀/避险, 与股票 corr <0.2
  159920  恒生 ETF        港股暴露, 已 HK 实证 corr 0.26
  510880  红利 ETF        价值大盘, 与小盘 corr ~0.5
  513100  纳指 ETF        美股暴露, 与 A 股 corr <0.3

数据源策略(三源备援，按优先级 fallback):
  主源:  tushare fund_daily (付费主力源，当日盘后即可用，与股票数据同源同日)
  备源1: akshare.fund_etf_hist_em (东财，无代理时可用)
  备源2: baostock (完全免费，无积分门槛，但数据滞后 1 个交易日)
  tushare token 读 data_lake/agent/tushare_config.json 或环境变量 TUSHARE_TOKEN。

baostock 代码格式: sh.511010 / sz.159920 (上交所 sh., 深交所 sz.)
baostock 提供后复权(复权因子*price), 也提供不复权; 通过 adjustflag 参数控制:
  1=后复权, 2=前复权, 3=不复权

存储: data_lake/cross_asset/etf/{code}.parquet
列: date,open,close,high,low,volume,amount (后复权, 轮动回测用)
    + raw_open,raw_close,raw_high,raw_low (不复权, 模拟盘成交/估值/跟单展示用)

用法:
  全量重抓: /usr/bin/python3 -m scripts.data.fetch_cross_asset_etf
  增量(供 scheduled_daily_update 调用): from scripts.data.fetch_cross_asset_etf import update_etfs
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import time
from datetime import datetime, timedelta

import pandas as pd

OUT = ROOT / "data_lake" / "cross_asset" / "etf"

ETFS = {
    "511010": "国债 ETF",
    "518880": "黄金 ETF",
    "159920": "恒生 ETF",
    "510880": "红利 ETF",
    "513100": "纳指 ETF",
}

# baostock 代码映射 (sh.= 上交所, sz.= 深交所)
_BS_CODE = {
    "511010": "sh.511010",   # 国债 ETF  上交所
    "518880": "sh.518880",   # 黄金 ETF  上交所
    "159920": "sz.159920",   # 恒生 ETF  深交所
    "510880": "sh.510880",   # 红利 ETF  上交所
    "513100": "sh.513100",   # 纳指 ETF  上交所
}

# tushare 代码映射 (6位.SH/SZ)
_TS_CODE = {
    "511010": "511010.SH",   # 国债 ETF  上交所
    "518880": "518880.SH",   # 黄金 ETF  上交所
    "159920": "159920.SZ",   # 恒生 ETF  深交所
    "510880": "510880.SH",   # 红利 ETF  上交所
    "513100": "513100.SH",   # 纳指 ETF  上交所
}

START = "20100101"
END = "20261231"

_RENAME = {
    "日期": "date", "开盘": "open", "收盘": "close",
    "最高": "high", "最低": "low",
    "成交量": "volume", "成交额": "amount",
}
_RAW_RENAME = {"open": "raw_open", "close": "raw_close", "high": "raw_high", "low": "raw_low"}


# ─────────────────────────────────────────────
#  主源: akshare (东方财富)
# ─────────────────────────────────────────────

def _fetch_akshare(code, start, end, adjust):
    """单次 akshare 抓取并标准化列;失败抛异常由调用方处理。"""
    import akshare as ak
    df = ak.fund_etf_hist_em(symbol=code, period="daily",
                             start_date=start, end_date=end, adjust=adjust)
    df = df.rename(columns={k: v for k, v in _RENAME.items() if k in df.columns})
    cols = [c for c in ["date", "open", "close", "high", "low", "volume", "amount"] if c in df.columns]
    df = df[cols]
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def fetch_one_akshare(code, start=START, end=END):
    """akshare 抓单只 ETF:后复权 + 不复权合并(raw_* 列),按 date 对齐。"""
    hfq = _fetch_akshare(code, start, end, adjust="hfq")
    time.sleep(0.3)
    raw = _fetch_akshare(code, start, end, adjust="")
    raw = raw[["date", "open", "close", "high", "low"]].rename(columns=_RAW_RENAME)
    return hfq.merge(raw, on="date", how="left")


# ─────────────────────────────────────────────
#  备源: baostock (完全免费, 无积分门槛)
# ─────────────────────────────────────────────

def _bs_to_df(rs, fields):
    """baostock ResultSet → DataFrame，自动转 float。"""
    data = []
    while rs.error_code == "0" and rs.next():
        data.append(rs.get_row_data())
    df = pd.DataFrame(data, columns=fields)
    return df


def fetch_one_baostock(code, start=START, end=END):
    """
    baostock 备源：分两次调用(后复权 + 不复权),合并 raw_* 列。

    baostock adjustflag: "1"=后复权, "3"=不复权(原始价格)
    字段: date, code, open, high, low, close, volume, amount, adjustflag, ...
    """
    import baostock as bs

    bs_code = _BS_CODE.get(code)
    if not bs_code:
        raise ValueError(f"No baostock code mapping for ETF {code}")

    start_str = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
    end_str   = f"{end[:4]}-{end[4:6]}-{end[6:8]}"

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_msg}")

    try:
        fields = "date,open,high,low,close,volume,amount"

        # 后复权
        rs_hfq = bs.query_history_k_data_plus(
            bs_code, fields,
            start_date=start_str, end_date=end_str,
            frequency="d", adjustflag="1"
        )
        hfq = _bs_to_df(rs_hfq, fields.split(","))

        time.sleep(0.2)

        # 不复权 (raw)
        rs_raw = bs.query_history_k_data_plus(
            bs_code, fields,
            start_date=start_str, end_date=end_str,
            frequency="d", adjustflag="3"
        )
        raw = _bs_to_df(rs_raw, fields.split(","))
    finally:
        bs.logout()

    if hfq.empty:
        return pd.DataFrame()

    # 类型转换
    for df in (hfq, raw):
        df["date"] = pd.to_datetime(df["date"])
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 合并 raw_* 列
    raw_cols = raw[["date", "open", "close", "high", "low"]].rename(columns=_RAW_RENAME)
    merged = hfq.merge(raw_cols, on="date", how="left")
    return merged.sort_values("date").reset_index(drop=True)


# ─────────────────────────────────────────────
#  备源2: tushare fund_daily (当日可用，与股票同源)
# ─────────────────────────────────────────────

def fetch_one_tushare(code, start=START, end=END):
    """
    tushare 备源：使用 fund_daily 接口，后复权与不复权各拉一次合并。
    优点：当日盘后即可拿到当日数据（与股票数据同源）；无代理依赖。
    tushare fund_daily 返回不复权价（原始价）；后复权通过 adj_factor 换算。
    简化处理：fund_daily 本身就是不复权，raw_* 直接用；
    后复权列 hfq 通过乘以复权因子得到——若无复权因子则退化为不复权（误差可接受）。
    """
    from lake.sources.tushare import call

    ts_code = _TS_CODE.get(code)
    if not ts_code:
        raise ValueError(f"No tushare code mapping for ETF {code}")

    start_str = f"{start[:4]}{start[4:6]}{start[6:8]}"
    end_str   = f"{end[:4]}{end[4:6]}{end[6:8]}"

    # fund_daily: 不复权原始价
    df_raw = call(
        "fund_daily",
        {"ts_code": ts_code, "start_date": start_str, "end_date": end_str},
        fields="ts_code,trade_date,open,high,low,close,vol,amount",
    )
    if df_raw.empty:
        raise RuntimeError(f"tushare fund_daily 返回空数据: {ts_code}")

    df_raw["date"] = pd.to_datetime(df_raw["trade_date"])
    for col in ["open", "high", "low", "close", "vol", "amount"]:
        df_raw[col] = pd.to_numeric(df_raw[col], errors="coerce")
    df_raw = df_raw.rename(columns={"vol": "volume"})
    df_raw = df_raw.sort_values("date").reset_index(drop=True)

    # 尝试拉复权因子(fund_adj_factor)，失败则退化为不复权作后复权列
    try:
        df_adj = call(
            "fund_adj_factor",
            {"ts_code": ts_code, "start_date": start_str, "end_date": end_str},
            fields="trade_date,adj_factor",
        )
        df_adj["date"] = pd.to_datetime(df_adj["trade_date"])
        df_adj["adj_factor"] = pd.to_numeric(df_adj["adj_factor"], errors="coerce")
        df_raw = df_raw.merge(df_adj[["date", "adj_factor"]], on="date", how="left")
        # 补齐缺失的复权因子(前向填充)
        df_raw["adj_factor"] = df_raw["adj_factor"].ffill().fillna(1.0)
        # 后复权 = 原始价 * adj_factor / 最新adj_factor（令当前价与不复权一致）
        latest_factor = df_raw["adj_factor"].iloc[-1]
        for col in ["open", "high", "low", "close"]:
            df_raw[f"hfq_{col}"] = df_raw[col] * df_raw["adj_factor"] / latest_factor
    except Exception:
        # 无复权因子：后复权列 = 原始价（国债ETF无分红复权，差异可忽略）
        for col in ["open", "high", "low", "close"]:
            df_raw[f"hfq_{col}"] = df_raw[col]

    result = df_raw[["date", "volume", "amount"]].copy()
    result["open"]  = df_raw["hfq_open"]
    result["high"]  = df_raw["hfq_high"]
    result["low"]   = df_raw["hfq_low"]
    result["close"] = df_raw["hfq_close"]
    result["raw_open"]  = df_raw["open"]
    result["raw_high"]  = df_raw["high"]
    result["raw_low"]   = df_raw["low"]
    result["raw_close"] = df_raw["close"]
    return result.sort_values("date").reset_index(drop=True)


# ─────────────────────────────────────────────
#  统一入口：主源优先，三源 fallback
# ─────────────────────────────────────────────

def fetch_one(code, start=START, end=END):
    """
    抓单只 ETF，三源备援（按优先级）：
      1. tushare fund_daily  — 主源（付费），当日盘后即可用，与股票数据同源同日
      2. akshare (东财)      — 备源1，无代理时可用
      3. baostock            — 备源2，完全免费但数据滞后 1 个交易日
    返回含后复权列 + raw_* 不复权列的 DataFrame。
    """
    # 主源: tushare (付费，优先)
    tushare_err = None
    try:
        df = fetch_one_tushare(code, start, end)
        if not df.empty:
            return df
    except Exception as e:
        tushare_err = e
        print(f"  [etf:{code}] tushare 主源失败({type(e).__name__}: {str(e)[:60]}), 切换 akshare 备源...", flush=True)

    # fallback 1: akshare
    akshare_err = None
    try:
        df = fetch_one_akshare(code, start, end)
        if not df.empty:
            print(f"  [etf:{code}] akshare 备源成功 {len(df)} 行", flush=True)
            return df
    except Exception as e:
        akshare_err = e
        print(f"  [etf:{code}] akshare 失败({type(e).__name__}: {str(e)[:60]}), 切换 baostock 备源...", flush=True)

    # fallback 2: baostock
    try:
        df = fetch_one_baostock(code, start, end)
        if not df.empty:
            print(f"  [etf:{code}] baostock 备源成功 {len(df)} 行", flush=True)
            return df
        raise RuntimeError("baostock 返回空数据")
    except Exception as e:
        raise RuntimeError(
            f"ETF {code} 三源均失败 — tushare: {tushare_err!r} | akshare: {akshare_err!r} | baostock: {e!r}"
        ) from e


def update_etfs(codes=None, lookback_days=30):
    """增量更新 ETF 日线(供 scheduled_daily_update 每日调用)。

    现存文件缺 raw_close 列(旧格式只有后复权)→ 全量重抓补齐口径;
    否则只抓最近 lookback_days 自然日窗口并 merge(drop_duplicates keep=last)。
    返回 {code: {"ok": bool, "rows": n, "latest": "YYYY-MM-DD"} | {"ok": False, "error": ...}}
    """
    OUT.mkdir(parents=True, exist_ok=True)
    codes = codes or list(ETFS)
    stats = {}
    for code in codes:
        fp = OUT / f"{code}.parquet"
        try:
            old = pd.read_parquet(fp) if fp.exists() else None
            if old is None or "raw_close" not in old.columns:
                new = fetch_one(code)            # 全量(首次/旧格式补 raw 列)
            else:
                start = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
                inc = fetch_one(code, start=start)
                if inc.empty:
                    # 双源均空 — 保留旧数据
                    latest = str(old["date"].max().date())
                    stats[code] = {"ok": True, "rows": len(old), "latest": latest,
                                   "note": "no_new_data_keep_existing"}
                    print(f"  [etf] {code} {ETFS.get(code, '')}: 无新数据，保留至 {latest}", flush=True)
                    continue
                new = (pd.concat([old, inc]).drop_duplicates("date", keep="last")
                       .sort_values("date").reset_index(drop=True))
            new.to_parquet(fp)
            latest = str(new["date"].max().date())
            stats[code] = {"ok": True, "rows": len(new), "latest": latest}
            print(f"  [etf] {code} {ETFS.get(code, '')}: {len(new)} rows → {latest}", flush=True)
        except Exception as e:
            stats[code] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}
            print(f"  [etf] {code} ⚠ {stats[code]['error']}", flush=True)
        time.sleep(0.3)
    return stats


def main():
    print(f"Fetching {len(ETFS)} ETFs (full, hfq + raw) to {OUT}")
    print(f"  Period: {START} ~ {END}")
    OUT.mkdir(parents=True, exist_ok=True)
    for code, name in ETFS.items():
        print(f"\n[{code}] {name}", flush=True)
        try:
            df = fetch_one(code)
            fp = OUT / f"{code}.parquet"
            df.to_parquet(fp)
            print(f"  ✓ {df.shape}  {df['date'].min().date()} ~ {df['date'].max().date()}  → {fp.name}")
        except Exception as e:
            print(f"  ⚠ {type(e).__name__}: {str(e)[:120]}")
        time.sleep(0.3)
    print(f"\nDone. Files in {OUT}:")
    for fp in sorted(OUT.glob("*.parquet")):
        print(f"  {fp.name}: {fp.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
