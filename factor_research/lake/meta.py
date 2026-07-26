"""
元数据构建：股票列表 / 交易日历 / 上市日
（交易日历和上市日依赖价量数据，下载完成后执行）
"""
from app_config.log import get_logger

logger = get_logger(__name__)

from pathlib import Path

import akshare as ak
import pandas as pd

META = Path("data_lake/meta")
META.mkdir(parents=True, exist_ok=True)
PRICE = Path("data_lake/price/daily")


def build_stock_list():
    """全市场股票列表（code, name）"""
    df = ak.stock_info_a_code_name()
    df["code"] = df["code"].astype(str)
    df.to_parquet(META / "codes.parquet", index=False)
    logger.info(f"[meta] 股票列表: {len(df)} 只")
    return df


def build_calendar():
    """
    交易日历 = 几只从不停牌的大盘股日期并集（茅台/平安/招商/浦发）。
    作为完整性校验和缺失检测的基准。
    """
    anchors = ["600519", "000001", "600036", "600000", "601398"]
    dates = set()
    for c in anchors:
        fp = PRICE / f"{c}.parquet"
        if fp.exists():
            dates |= set(pd.read_parquet(fp, columns=["date"])["date"])
    cal = pd.DatetimeIndex(sorted(dates))
    pd.DataFrame({"date": cal}).to_parquet(META / "trade_calendar.parquet", index=False)
    logger.info(f"[meta] 交易日历: {len(cal)} 个交易日 {cal.min().date()}~{cal.max().date()}")
    return cal


def rebuild_trade_calendar_from_prices(
    *,
    root: Path | str = Path("."),
    anchors: list[str] | None = None,
    min_anchor_count: int = 5,
) -> pd.Timestamp | None:
    """Rebuild ``data_lake/meta/trade_calendar.parquet`` from anchor price files.

    This is the sanctioned write path used by production scheduling. Keeping
    the parquet write in ``lake`` prevents ops scripts from directly mutating
    core lake metadata.
    """
    from collections import Counter

    root = Path(root)
    anchors = anchors or ["600519", "601398", "000001", "600036", "600000", "601988"]
    counter = Counter()
    for code in anchors:
        fp = root / "data_lake" / "price" / "daily" / f"{code}.parquet"
        if fp.exists():
            counter.update(pd.read_parquet(fp, columns=["date"])["date"].tolist())
    if not counter:
        return None
    cal = pd.DatetimeIndex(sorted(date for date, count in counter.items() if count >= min_anchor_count))
    if not len(cal):
        return None
    out = root / "data_lake" / "meta" / "trade_calendar.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": cal}).to_parquet(out, index=False)
    return cal.max()


def update_trade_calendar(*, root: Path | str = Path(".")) -> dict:
    """从 tushare 更新交易日历到最新（trade_cal 接口，2000积分可用）。
    日历过时会导致 update_prices() 不知道新交易日从而跳过更新。
    """
    from lake.sources.tushare import call
    root = Path(root)
    cal_fp = root / "data_lake" / "meta" / "trade_calendar.parquet"
    if not cal_fp.exists():
        return {"ok": False, "error": "calendar file not found"}

    old_cal = pd.read_parquet(cal_fp)
    old_cal["date"] = pd.to_datetime(old_cal["date"])
    old_max = old_cal["date"].max()
    today = pd.Timestamp.now().normalize()

    # 只在日历比今天旧时才更新
    if old_max >= today:
        logger.info(f"[calendar] 已最新({old_max.date()})")
        return {"ok": True, "latest": str(old_max.date()), "updated": False}

    today_str = today.strftime("%Y%m%d")
    df = call("trade_cal", {
        "exchange": "SSE",
        "start_date": old_max.strftime("%Y%m%d"),
        "end_date": today_str,
        "is_open": "1",
    }, fields="cal_date")

    if df.empty:
        return {"ok": True, "latest": str(old_max.date()), "updated": False}

    df["date"] = pd.to_datetime(df["cal_date"])
    new_cal = (pd.concat([old_cal, df[["date"]]])
               .drop_duplicates("date").sort_values("date").reset_index(drop=True))
    new_cal.to_parquet(cal_fp, index=False)
    new_max = new_cal["date"].max()
    added = len(new_cal) - len(old_cal)
    logger.info(f"[calendar] 更新: {old_max.date()} → {new_max.date()} (+{added}个交易日)")
    return {"ok": True, "latest": str(new_max.date()), "updated": True, "added": added}


def build_list_dates():
    """
    上市日（近似）= 每只价量首日。
    注：回溯起点2010，2010前上市的首日被截断为2010-01-04（标记为'≤2010'）；
    2010后上市的为真实上市日（用于识别次新股）。真实上市日可后续用个股信息接口补。
    """
    rows = []
    for fp in PRICE.glob("*.parquet"):
        df = pd.read_parquet(fp, columns=["date"])
        if len(df):
            first = df["date"].min()
            rows.append({
                "code": fp.stem,
                "first_date": first,
                "truncated": first <= pd.Timestamp("2010-01-05"),  # 可能2010前已上市
            })
    out = pd.DataFrame(rows).sort_values("code").reset_index(drop=True)
    out.to_parquet(META / "list_date.parquet", index=False)
    logger.info(f"[meta] 上市日: {len(out)} 只 (其中{out['truncated'].sum()}只可能2010前上市)")
    return out


def build_all():
    build_stock_list()
    build_calendar()
    build_list_dates()
    logger.info("[meta] 元数据构建完成")


if __name__ == "__main__":
    import os
    os.chdir(Path(__file__).parent.parent)
    build_all()
