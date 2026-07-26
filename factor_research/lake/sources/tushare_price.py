"""
Tushare 日增量价量源 —— 替换 TencentDailyFetcher 的增量部分。

每个新交易日只需 2 次 tushare API 调用（而非 Tencent 的 5000+ 次逐只请求）：
  1. daily(trade_date)      → 全市场原始 OHLCV + amount + pre_close + pct_chg
  2. adj_factor(start/end)  → 当日 + 上一交易日的复权因子（用于除权校正）

hfq 重建（与 Tencent hfqday 口径兼容）：
  hfq_close = hfq_prev_close × (raw_close / pre_close) × (adj_factor_today / adj_factor_prev)
  对无除权日：adj_ratio=1，退化为简单涨跌幅延伸，与现有历史无缝衔接。
  对除权日：adj_factor 跳升，自动补偿 pre_close 的价格折扣。
"""
from __future__ import annotations

from app_config.log import get_logger

logger = get_logger(__name__)

import pandas as pd

from lake.sources.tushare import call, to_code


def _yyyymmdd(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y%m%d")


def fetch_new_day(
    trade_date: pd.Timestamp,
    prev_trade_date: pd.Timestamp,
    prev_hfq_closes: pd.Series,          # index=code(6位), value=hfq_close
) -> pd.DataFrame:
    """
    拉取 trade_date 的全市场 hfq 价量行，返回 DataFrame：
      columns: date, open, close, high, low, volume, amount, raw_close
      index: 整数（调用方合并到 per-stock parquet）

    数据湖 canonical 单位：volume=股、amount=元；所有板块使用相同口径。
    prev_hfq_closes: 上一交易日的 hfq 收盘价（从 daily_all.parquet 读）。
    没有上一日记录的股票（新上市等）会被跳过，调用方应用 Tencent 补全。
    """
    td  = _yyyymmdd(trade_date)
    ptd = _yyyymmdd(prev_trade_date)

    # ── 1. 原始日线 ──
    raw = call("daily", {"trade_date": td})
    if raw.empty:
        return pd.DataFrame()

    raw["code"] = to_code(raw["ts_code"])
    raw = raw.set_index("code")

    # ── 2. 复权因子（当日 + 前日，一次调用） ──
    adj = call("adj_factor", {"start_date": ptd, "end_date": td})
    adj["code"] = to_code(adj["ts_code"])
    adj_today = (adj[adj["trade_date"] == td]
                 .set_index("code")["adj_factor"])
    adj_prev  = (adj[adj["trade_date"] == ptd]
                 .set_index("code")["adj_factor"])

    rows = []
    skipped_no_prev = 0
    skipped_bad_price = 0

    for code, r in raw.iterrows():
        # 必须有前日 hfq 基准
        hfq_prev = prev_hfq_closes.get(code)
        if hfq_prev is None or pd.isna(hfq_prev) or hfq_prev <= 0:
            skipped_no_prev += 1
            continue

        raw_close = float(r.get("close") or 0)
        pre_close = float(r.get("pre_close") or 0)
        if raw_close <= 0 or pre_close <= 0:
            skipped_bad_price += 1
            continue

        af_today = adj_today.get(code)
        af_prev  = adj_prev.get(code)
        adj_ratio = (af_today / af_prev
                     if (af_today and af_prev and af_prev != 0)
                     else 1.0)

        price_ratio = raw_close / pre_close
        hfq_close   = round(float(hfq_prev) * price_ratio * adj_ratio, 4)
        hfq_factor  = hfq_close / raw_close   # 用于缩放 open/high/low

        volume = float(r.get("vol") or 0) * 100      # Tushare 手 → canonical 股
        amount = float(r.get("amount") or 0) * 1000  # Tushare 千元 → canonical 元

        rows.append({
            "code":   code,
            "date":   trade_date,
            "open":   round(float(r.get("open") or raw_close) * hfq_factor, 4),
            "close":  hfq_close,
            "high":   round(float(r.get("high") or raw_close) * hfq_factor, 4),
            "low":    round(float(r.get("low")  or raw_close) * hfq_factor, 4),
            "volume": volume,
            "amount": amount,
            "raw_close": raw_close,
        })

    if skipped_no_prev or skipped_bad_price:
        logger.warning(f"  [tushare_price] {td} 跳过: 无前日基准={skipped_no_prev} 价格异常={skipped_bad_price}",
              flush=True)

    return pd.DataFrame(rows) if rows else pd.DataFrame()
