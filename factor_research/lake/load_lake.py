"""
数据加载层：多维度对齐面板（防未来函数）

把数据湖的价量+财务+估值统一成 date×code 面板，供因子计算。
关键：财务按 avail_date(披露可用日) 对齐，杜绝未来函数。
"""
from pathlib import Path

import numpy as np
import pandas as pd

from lake.cleaning import apply_quarantine, repair_ohlc

LAKE = Path(__file__).parent.parent / "data_lake"


# ── 价量面板 ──
def load_prices(codes=None, start="2010-01-01", fields=("close", "volume", "amount")):
    """加载日线 → {field: date×code 宽表}。

    优先读取 daily_all.parquet（大表），不存在则 fallback 到逐只文件。
    canonical 单位对所有板块一致：volume=股、amount=元。
    """
    all_fp = LAKE / "price/daily_all.parquet"
    if all_fp.exists():
        cols = ["date", "code"] + list(fields)
        try:
            # Optimize: pushdown date filter to avoid reading the whole file (15M+ rows) into memory
            df = pd.read_parquet(all_fp, columns=cols, filters=[("date", ">=", pd.Timestamp(start))])
        except Exception:
            df = pd.read_parquet(all_fp, columns=cols)
            df["date"] = pd.to_datetime(df["date"])
            df = df[df["date"] >= pd.Timestamp(start)]
        df["date"] = pd.to_datetime(df["date"])
        if codes:
            df = df[df["code"].isin(codes)]
        df = repair_ohlc(apply_quarantine(df))   # 确定性清洗(隔离坏数据 + OHLC 自洽)
        return {f: df.pivot(index="date", columns="code", values=f) for f in fields}

    # fallback: 逐只 parquet
    daily = LAKE / "price/daily"
    files = ([daily / f"{c}.parquet" for c in codes] if codes
             else sorted(daily.glob("*.parquet")))
    frames = []
    for fp in files:
        if not fp.exists():
            continue
        df = pd.read_parquet(fp, columns=["date"] + list(fields))
        df["code"] = fp.stem
        frames.append(df)
    if not frames:
        prices_dir = str(daily.resolve())
        raise FileNotFoundError(
            f"未找到任何价格 parquet（扫描目录: {prices_dir}）。"
            "data_lake 未挂载：worktree 环境需将主仓 data_lake symlink 进来"
        )
    long = pd.concat(frames, ignore_index=True)
    long = long[long["date"] >= pd.Timestamp(start)]
    long = repair_ohlc(apply_quarantine(long))   # 确定性清洗
    return {f: long.pivot(index="date", columns="code", values=f) for f in fields}


from lake.schema import FUNDAMENTAL_FIELDS

# ── 财务面板（批量长表，防未来函数对齐）──

def load_fundamental_panel(trade_dates, codes=None, fields=FUNDAMENTAL_FIELDS):
    """
    从批量长表 fundamental_batch.parquet 加载，对齐到交易日面板 {field: date×code}。
    用 avail_date(公告日) 作为生效日 ffill，确保 T 日只用 T 日前已披露的财务（防未来函数）。
    """
    fp = LAKE / "fundamental_batch.parquet"
    if not fp.exists():
        return {f: pd.DataFrame() for f in fields}
    df = pd.read_parquet(fp).dropna(subset=["avail_date"])
    if codes:
        df = df[df["code"].isin(codes)]
    trade_idx = pd.DatetimeIndex(trade_dates)
    panels = {}
    for f in fields:
        if f not in df.columns:
            continue
        sub = (df[["avail_date", "code", f]].dropna()
               .sort_values("avail_date")
               .drop_duplicates(["code", "avail_date"], keep="last"))
        pivot = sub.pivot_table(index="avail_date", columns="code", values=f, aggfunc="last")
        # 公告日生效，ffill 到交易日（T日只用T日前已公告的财务）
        aligned = pivot.reindex(pivot.index.union(trade_idx)).ffill().reindex(trade_idx)
        panels[f] = aligned
    return panels


# ── 估值自算（财务+价量）──
def compute_valuation(close, eps, bps):
    """PE=close/EPS, PB=close/BPS（eps/bps 已是 date×code 防未来函数面板）"""
    pe = close / eps.replace(0, np.nan)
    pb = close / bps.replace(0, np.nan)
    return {"pe": pe, "pb": pb}


# ── 不复权价加载（估值专用，通达信原始价）──
def load_raw_close(codes=None, start="2010-01-01"):
    """加载不复权close → date×code（PE/PB自算必须用不复权价）。

    优先读取 daily_raw_all.parquet（大表），不存在则 fallback 到逐只文件。
    """
    all_fp = LAKE / "price/daily_raw_all.parquet"
    if all_fp.exists():
        try:
            # Optimize: pushdown date filter to avoid reading the whole file (15M+ rows) into memory
            df = pd.read_parquet(all_fp, columns=["date", "code", "raw_close"], filters=[("date", ">=", pd.Timestamp(start))])
        except Exception:
            df = pd.read_parquet(all_fp, columns=["date", "code", "raw_close"])
            df["date"] = pd.to_datetime(df["date"])
            df = df[df["date"] >= pd.Timestamp(start)]
        df["date"] = pd.to_datetime(df["date"])
        if codes:
            df = df[df["code"].isin(codes)]
        df = apply_quarantine(df)   # 与复权价一致地排除隔离区间
        return df.pivot(index="date", columns="code", values="raw_close")

    # fallback: 逐只 parquet
    raw_dir = LAKE / "price/daily_raw"
    files = ([raw_dir / f"{c}.parquet" for c in codes] if codes
             else sorted(raw_dir.glob("*.parquet")))
    frames = []
    for fp in files:
        if not fp.exists():
            continue
        df = pd.read_parquet(fp)
        df["code"] = fp.stem
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    long = pd.concat(frames, ignore_index=True)
    long = long[long["date"] >= pd.Timestamp(start)]
    long = apply_quarantine(long)
    return long.pivot(index="date", columns="code", values="raw_close")


from lake.schema import CAPITAL_FIELDS

# ── 资金面面板（两融/北向，按披露可用日滞后一日防未来函数）──

def _load_capital_long(name, codes=None, start="2010-01-01"):
    fp = LAKE / "capital" / f"{name}_all.parquet"
    if not fp.exists():
        return pd.DataFrame()
    try:
        # Optimize: pushdown date filter to avoid reading the whole file into memory
        df = pd.read_parquet(fp, filters=[("date", ">=", pd.Timestamp(start))])
    except Exception:
        df = pd.read_parquet(fp)
    if "date" not in df.columns or "code" not in df.columns:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    df["code"] = df["code"].astype(str).str.zfill(6)
    df = df[df["date"] >= pd.Timestamp(start)]
    if codes:
        df = df[df["code"].isin(codes)]
    return df


def load_capital_panel(trade_dates, codes=None, start="2010-01-01", fields=CAPITAL_FIELDS):
    """
    加载资金面长表并对齐成 date×code 面板。

    防未来函数：交易所/东财资金面数据通常在 T 日盘后发布，因此 T 日记录
    只允许从 T+1 起被策略看到。实现上 pivot 后统一 shift(1)。
    """
    trade_idx = pd.DatetimeIndex(trade_dates)
    frames = []
    for name in ["margin", "northbound"]:
        df = _load_capital_long(name, codes=codes, start=start)
        if not df.empty:
            frames.append(df)
    panels = {f: pd.DataFrame(index=trade_idx, columns=codes, dtype=float) for f in fields}
    if not frames:
        return panels
    long = pd.concat(frames, ignore_index=True, sort=False)
    for f in fields:
        if f not in long.columns:
            continue
        sub = long[["date", "code", f]].dropna()
        if sub.empty:
            continue
        pivot = sub.pivot_table(index="date", columns="code", values=f, aggfunc="last")
        aligned = pivot.reindex(pivot.index.union(trade_idx)).sort_index().shift(1).reindex(trade_idx)
        panels[f] = aligned.reindex(columns=codes)
    return panels


# ── 每日指标(tushare daily_basic:市值/股本/换手/估值)──
from lake.schema import DAILY_BASIC_FIELDS


def load_daily_basic_panel(trade_dates, codes=None, fields=DAILY_BASIC_FIELDS):
    """加载 daily_basic → {field: date×code 面板}。

    市值/换手/估值是**价格衍生当日量**(total_mv=total_share×close_T,T 日收盘已知),
    与 close/amount 同口径,**不 shift**(区别于财务/资金面的 ffill+shift)。
    ts_code(600519.SH)归一为 code(600519)。
    """
    fp = LAKE / "daily_basic/daily_basic_all.parquet"
    trade_idx = pd.DatetimeIndex(trade_dates)
    if not fp.exists():
        return {f: pd.DataFrame(index=trade_idx, columns=codes, dtype=float) for f in fields}
    cols = ["ts_code", "trade_date"] + [f for f in fields]
    df = pd.read_parquet(fp, columns=cols)
    return pivot_daily_basic(df, trade_idx, fields, codes)


def pivot_daily_basic(df, trade_idx, fields, codes=None):
    """daily_basic 长表(ts_code/trade_date/<fields>)→ {field: date×code}。纯函数,可测。"""
    df = df.copy()
    df["date"] = pd.to_datetime(df["trade_date"].astype(str))
    df["code"] = df["ts_code"].str.split(".").str[0]
    if codes:
        df = df[df["code"].isin(codes)]
    panels = {}
    for f in fields:
        pivot = df.pivot_table(index="date", columns="code", values=f, aggfunc="last")
        panels[f] = pivot.reindex(trade_idx).reindex(columns=codes) if codes else pivot.reindex(trade_idx)
    return panels


# ── 财务指标(tushare fina_indicator:杠杆/质量/成长)──
from lake.schema import FINA_INDICATOR_FIELDS


def ffill_by_anndate(df, fields, trade_idx, codes=None):
    """财报长表(ts_code/ann_date/<fields>)→ {field: date×code},公告日生效 ffill。

    防未来铁律:用 ann_date(公告日)作生效日,ffill 到交易日 → T 日只用 T 日前已公告。
    纯函数,可测。
    """
    df = df.dropna(subset=["ann_date"]).copy()
    df["code"] = df["ts_code"].str.split(".").str[0]
    df["avail"] = pd.to_datetime(df["ann_date"].astype(str))
    if codes:
        df = df[df["code"].isin(codes)]
    panels = {}
    for f in fields:
        if f not in df.columns:
            continue
        sub = (df[["avail", "code", f]].dropna()
               .sort_values("avail").drop_duplicates(["code", "avail"], keep="last"))
        if sub.empty:
            panels[f] = pd.DataFrame(index=trade_idx, columns=codes, dtype=float)
            continue
        pivot = sub.pivot_table(index="avail", columns="code", values=f, aggfunc="last")
        panels[f] = pivot.reindex(pivot.index.union(trade_idx)).ffill().reindex(trade_idx)
        if codes:
            panels[f] = panels[f].reindex(columns=codes)
    return panels


def load_fina_indicator_panel(trade_dates, codes=None, fields=FINA_INDICATOR_FIELDS):
    """加载 fina_indicator → {field: date×code},公告日 ffill(防未来)。"""
    fp = LAKE / "financials/fina_indicator_all.parquet"
    trade_idx = pd.DatetimeIndex(trade_dates)
    if not fp.exists():
        return {f: pd.DataFrame(index=trade_idx, columns=codes, dtype=float) for f in fields}
    df = pd.read_parquet(fp)
    return ffill_by_anndate(df, fields, trade_idx, codes)


# ── Tushare 扩展维度统一加载入口(by_date 当日对齐 / anndate 公告日 ffill)──
from lake.schema import TUSHARE_DATASETS


def load_tushare_panel(dataset, trade_dates, fields=None, codes=None):
    """统一加载任一 tushare 扩展维度 → {field: date×code}。

    口径自动按数据集选择(见 TUSHARE_DATASETS):
      by_date         价格/市场当日量 → pivot 对齐,不 shift
      by_date_shift1  盘后披露次日可用 → pivot 后按交易日 shift(1)
      anndate         财务/事件公告 → ann_date 公告日 ffill(防未来)
    """
    if dataset not in TUSHARE_DATASETS:
        raise KeyError(f"未知 dataset {dataset};可选 {list(TUSHARE_DATASETS)}")
    store, mode, default_fields = TUSHARE_DATASETS[dataset]
    fields = fields or default_fields
    fp = LAKE / store
    trade_idx = pd.DatetimeIndex(trade_dates)
    if not fp.exists():
        return {f: pd.DataFrame(index=trade_idx, columns=codes, dtype=float) for f in fields}
    df = pd.read_parquet(fp)
    if mode == "by_date":
        return pivot_daily_basic(df, trade_idx, fields, codes)
    if mode == "by_date_shift1":
        # 复用 by_date 对齐后按交易日滞后一日(与 load_capital_panel shift(1) 同语义)
        panels = pivot_daily_basic(df, trade_idx, fields, codes)
        return {f: p.shift(1) for f, p in panels.items()}
    return ffill_by_anndate(df, fields, trade_idx, codes)


# ── 股权质押统计(pledge_stat):稀疏周度状态源,不能按普通公告日无限 ffill ──
PLEDGE_STAT_FIELDS = [
    "pledge_ratio",
    "pledge_count",
    "pledge_share_amount",
    "pledge_observed",
    "pledge_stale_days",
    "pledge_coverage_state",
]


def _empty_pledge_panels(trade_idx, codes=None, state="unknown"):
    cols = codes or []
    panels = {
        "pledge_ratio": pd.DataFrame(index=trade_idx, columns=cols, dtype=float),
        "pledge_count": pd.DataFrame(index=trade_idx, columns=cols, dtype=float),
        "pledge_share_amount": pd.DataFrame(index=trade_idx, columns=cols, dtype=float),
        "pledge_stale_days": pd.DataFrame(index=trade_idx, columns=cols, dtype=float),
        "pledge_observed": pd.DataFrame(False, index=trade_idx, columns=cols, dtype=object),
        "pledge_coverage_state": pd.DataFrame(state, index=trade_idx, columns=cols, dtype=object),
    }
    return panels


def align_pledge_stat(df, trade_dates, codes=None, max_stale_days=30):
    """pledge_stat 长表 → date×code 状态面板。

    源表是稀疏周度快照,不是全市场面板:
      * 防未来: T 日只允许使用 ``end_date < T`` 的快照。
      * 数值有效期: ``stale_days <= max_stale_days`` 才保留,否则置 NaN。
      * coverage_state: current/stale/never_seen/unknown。
      * pledge_observed: 某条源端记录首次变得可见的交易日为 True,ffill 日为 False。
    """
    trade_idx = pd.DatetimeIndex(pd.to_datetime(trade_dates)).astype("datetime64[ns]")
    if df.empty:
        return _empty_pledge_panels(trade_idx, codes, state="unknown")

    df = df.copy()
    df["code"] = df["ts_code"].astype(str).str.split(".").str[0]
    df["end_dt"] = pd.to_datetime(df["end_date"], errors="coerce").astype("datetime64[ns]")
    df["pledge_share_amount"] = (
        pd.to_numeric(df.get("unrest_pledge"), errors="coerce").fillna(0)
        + pd.to_numeric(df.get("rest_pledge"), errors="coerce").fillna(0)
    )
    df = df.dropna(subset=["code", "end_dt"])
    if codes:
        df = df[df["code"].isin(codes)]
        cols = list(codes)
    else:
        cols = sorted(df["code"].dropna().unique())
    panels = _empty_pledge_panels(trade_idx, cols, state="never_seen")
    if not cols:
        return panels

    numeric_fields = ["pledge_ratio", "pledge_count", "pledge_share_amount"]
    df["avail_dt"] = df["end_dt"] + pd.Timedelta(nanoseconds=1)
    sub = (df[["avail_dt", "code", "end_dt"] + numeric_fields]
           .sort_values("avail_dt")
           .drop_duplicates(["code", "avail_dt"], keep="last"))
    avail_idx = pd.DatetimeIndex(sub["avail_dt"].dropna().unique()).sort_values()
    align_idx = avail_idx.union(trade_idx).sort_values()

    aligned_fields = {}
    for field in numeric_fields:
        pivot = sub.pivot_table(index="avail_dt", columns="code", values=field, aggfunc="last")
        aligned_fields[field] = pivot.reindex(align_idx).ffill().reindex(trade_idx).reindex(columns=cols)

    end_num = sub.assign(end_num=sub["end_dt"].astype("int64"))
    end_pivot = end_num.pivot_table(index="avail_dt", columns="code", values="end_num", aggfunc="last")
    end_aligned = end_pivot.reindex(align_idx).ffill().reindex(trade_idx).reindex(columns=cols)
    last_end = end_aligned.apply(pd.to_datetime)
    last_end[end_aligned.isna()] = pd.NaT

    trade_arr = trade_idx.to_numpy(dtype="datetime64[ns]")[:, None]
    end_arr = last_end.to_numpy(dtype="datetime64[ns]")
    stale_arr = (trade_arr - end_arr) / np.timedelta64(1, "D")
    stale_days = pd.DataFrame(stale_arr, index=trade_idx, columns=cols, dtype=float)
    stale_days[last_end.isna()] = np.nan
    valid = stale_days.le(max_stale_days)

    for field in numeric_fields:
        panels[field] = aligned_fields[field].where(valid)
    panels["pledge_stale_days"] = stale_days

    seen_codes = set(sub["code"].unique())
    state = pd.DataFrame("never_seen", index=trade_idx, columns=cols, dtype=object)
    for code in seen_codes.intersection(cols):
        state.loc[:, code] = "unknown"
    state[last_end.notna() & valid] = "current"
    state[last_end.notna() & ~valid] = "stale"
    panels["pledge_coverage_state"] = state

    prev_trade = pd.Series(trade_idx, index=trade_idx).shift(1)
    newly_visible = last_end.notna() & valid
    newly_visible &= (
        prev_trade.isna().to_numpy()[:, None]
        | (last_end.ge(prev_trade, axis=0) & last_end.lt(pd.Series(trade_idx, index=trade_idx), axis=0))
    )
    panels["pledge_observed"] = newly_visible.astype(object)
    return panels


def load_pledge_stat_panel(trade_dates, codes=None, max_stale_days=30):
    """加载 pledge_stat 专用状态面板。"""
    fp = LAKE / "institutional/pledge_stat_all.parquet"
    trade_idx = pd.DatetimeIndex(trade_dates)
    if not fp.exists():
        return _empty_pledge_panels(trade_idx, codes, state="unknown")
    return align_pledge_stat(pd.read_parquet(fp), trade_idx, codes, max_stale_days)


# ── 宏观时序层(市场级单时序,防未来 lag)──
def align_macro(df, trade_idx):
    """宏观长表 → 对齐到交易日(防未来)。纯函数,可测。

    monthly(含 'month' 列):参考月 M 的值 **M+2 月初才可见**(发布滞后,保守安全),ffill。
    daily(含 'date'/'trade_date'):当日对齐(利率/北向 EOD 已知),ffill。
    """
    df = df.copy()
    if "month" in df.columns:
        m = pd.to_datetime(df["month"].astype(str), format="%Y%m")
        df = df.drop(columns=["month"])
        df.index = m + pd.offsets.MonthBegin(2)          # 防未来:M+2 月初可见
    else:
        dcol = "trade_date" if "trade_date" in df.columns else "date"
        df.index = pd.to_datetime(df[dcol].astype(str))
        df = df.drop(columns=[dcol])
    df = df.apply(pd.to_numeric, errors="coerce").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df.reindex(df.index.union(trade_idx)).ffill().reindex(trade_idx)


def load_macro(name, trade_dates, fields=None):
    """加载宏观时序 → date×field(防未来对齐)。name: cn_cpi/cn_ppi/cn_m/shibor/moneyflow_hsgt。"""
    fp = LAKE / "macro" / f"{name}.parquet"
    trade_idx = pd.DatetimeIndex(trade_dates)
    if not fp.exists():
        return pd.DataFrame(index=trade_idx)
    out = align_macro(pd.read_parquet(fp), trade_idx)
    return out[[c for c in fields if c in out.columns]] if fields else out


# ── 一站式加载 ──
def load_panel(codes=None, start="2010-01-01", with_fundamental=True):
    """返回统一的多维度面板 dict：close/volume/amount (+财务+估值)"""
    px = load_prices(codes, start)
    panel = dict(px)
    if with_fundamental:
        trade_dates = px["close"].index
        fund = load_fundamental_panel(trade_dates, codes)
        panel.update({f"fund_{k}": v for k, v in fund.items()})
        if "eps_ttm" in fund and "bps" in fund:
            raw_close = load_raw_close(codes, start)   # 不复权价算估值(防量纲错误)
            price_for_val = raw_close.reindex(index=px["close"].index) if not raw_close.empty else px["close"]
            val = compute_valuation(price_for_val, fund["eps_ttm"], fund["bps"])  # 用TTM EPS
            panel.update(val)
    return panel
