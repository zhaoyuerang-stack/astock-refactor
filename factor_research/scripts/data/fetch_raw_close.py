"""
下载全市场不复权close（估值PE/PB专用）—— 通达信mootdx原始价
（腾讯无真不复权、新浪py_mini_racer坏，只剩通达信可用）
mootdx单次800条，分批6次回溯2010。鲁棒+断点续传(已有则跳过)。
"""
import warnings; warnings.filterwarnings("ignore")
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
import sys

sys.path.insert(0, str(ROOT))
import pandas as pd
from mootdx.quotes import Quotes

from lake.base import Fetcher, RateLimiter
from lake.sources.registry import register


@register("tdx_raw")
class TdxRawFetcher(Fetcher):
    """通达信不复权日线 OHLC。全量:6批×800 回溯2010;增量:只拉最近 inc_offset 条并 merge 现有。"""
    COLS = {"open": "raw_open", "high": "raw_high", "low": "raw_low", "close": "raw_close"}

    def __init__(self, incremental=False, inc_offset=20, **kw):
        super().__init__(name="tdx_raw", out_dir="data_lake/price/daily_raw",
                         limiter=RateLimiter(0.05, (0, 0.05)),
                         max_workers=kw.pop("max_workers", 4), timeout=20, **kw)
        self.incremental = incremental
        self.inc_offset = inc_offset
        self._local = threading.local()

    @property
    def client(self):
        if not hasattr(self._local, "c"):
            self._local.c = Quotes.factory(market="std")
        return self._local.c

    def _to_ohlc(self, frames):
        allf = pd.concat(frames).reset_index(drop=True)
        allf["date"] = pd.to_datetime(allf["datetime"]).dt.floor("D")
        out = (allf[["date"] + list(self.COLS)].rename(columns=self.COLS)
               .drop_duplicates("date").sort_values("date"))
        return out[out["date"] >= pd.Timestamp("2010-01-01")].reset_index(drop=True)

    def fetch_one(self, code):
        if self.incremental:                      # 增量:最近 inc_offset 条 + merge 现有
            df = self.client.bars(symbol=code, frequency=9, start=0, offset=self.inc_offset)
            if df is None or df.empty:
                return None
            new = self._to_ohlc([df])
            fp = self.out_path(code)
            if fp.exists():
                old = pd.read_parquet(fp)
                new = (pd.concat([old, new]).drop_duplicates("date", keep="last")
                       .sort_values("date").reset_index(drop=True))
            return new if len(new) else None
        frames = []                               # 全量:6批×800 回溯2010
        for b in range(6):
            df = self.client.bars(symbol=code, frequency=9, start=b * 800, offset=800)
            if df is None or df.empty:
                break
            frames.append(df)
            if len(df) < 800:
                break
        return self._to_ohlc(frames) if frames else None


def update_raw_prices_tushare():
    """使用 Tushare daily 接口批量增量更新 daily_raw，避免逐股请求，速度快 100 倍。"""
    from pathlib import Path

    import pandas as pd

    from lake.compact import compact_raw_prices
    from lake.sources.tushare import call, to_code

    daily_raw_dir = Path("data_lake/price/daily_raw")
    daily_raw_all_fp = Path("data_lake/price/daily_raw_all.parquet")

    # 1. 找出已有的最新日期
    sample_codes = ["600519", "000001", "300750", "600036", "601398"]
    dates = []
    for sc in sample_codes:
        fp = daily_raw_dir / f"{sc}.parquet"
        if fp.exists():
            try:
                dates.append(pd.read_parquet(fp, columns=["date"])["date"].max())
            except Exception:
                pass
    if dates:
        latest_raw = pd.Timestamp(max(dates))
    else:
        if daily_raw_all_fp.exists():
            try:
                latest_raw = pd.to_datetime(pd.read_parquet(daily_raw_all_fp, columns=["date"])["date"]).max()
            except Exception:
                latest_raw = pd.Timestamp("2010-01-01")
        else:
            latest_raw = pd.Timestamp("2010-01-01")

    # 2. 找出需要补的日期
    cal_fp = Path("data_lake/meta/trade_calendar.parquet")
    if not cal_fp.exists():
        raise FileNotFoundError("trade_calendar.parquet not found")
    cal = pd.to_datetime(pd.read_parquet(cal_fp)["date"]).sort_values()

    # 核心价量最新日期
    daily_all_fp = Path("data_lake/price/daily_all.parquet")
    if daily_all_fp.exists():
        try:
            latest_price = pd.to_datetime(pd.read_parquet(daily_all_fp, columns=["date"])["date"]).max()
        except Exception:
            latest_price = cal.max()
    else:
        latest_price = cal.max()

    today = pd.Timestamp.now().normalize()
    # 最多更新到最新价量的日期或今天
    target_max = min(latest_price, today)
    new_dates = cal[(cal > latest_raw) & (cal <= target_max)].tolist()

    if not new_dates:
        print(f"[raw tushare] 已最新({latest_raw.date()}), 无需增量", flush=True)
        return {"ok": 0, "skipped": True}

    print(f"[raw tushare] Tushare 增量拉取 {len(new_dates)} 个交易日: {new_dates[0].date()} ~ {new_dates[-1].date()}", flush=True)

    all_new_rows = []
    for td in new_dates:
        td_str = td.strftime("%Y%m%d")
        print(f"  [raw tushare] 拉取 {td.date()} 原始日线...", flush=True)
        df = call("daily", {"trade_date": td_str})
        if not df.empty:
            df["date"] = td
            df["code"] = to_code(df["ts_code"])
            df = df.rename(columns={
                "open": "raw_open",
                "high": "raw_high",
                "low": "raw_low",
                "close": "raw_close"
            })
            df = df[["code", "date", "raw_open", "raw_high", "raw_low", "raw_close"]]
            all_new_rows.append(df)
        else:
            print(f"  [raw tushare] {td.date()} 返回空", flush=True)

    if not all_new_rows:
        print("[raw tushare] 未拉取到有效新数据", flush=True)
        return {"ok": 0}

    combined = pd.concat(all_new_rows, ignore_index=True)

    # 3. 写入个股文件
    daily_dir = Path("data_lake/price/daily")
    daily_codes = {fp.stem for fp in daily_dir.glob("*.parquet")}

    updated = 0
    for code, grp in combined.groupby("code"):
        if code not in daily_codes:
            continue
        fp = daily_raw_dir / f"{code}.parquet"
        new_df = grp.drop(columns=["code"]).reset_index(drop=True)
        if fp.exists():
            try:
                old = pd.read_parquet(fp)
                old["date"] = pd.to_datetime(old["date"])
                merged = pd.concat([old, new_df]).drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
            except Exception:
                merged = new_df
        else:
            merged = new_df
        merged.to_parquet(fp, index=False)
        updated += 1

    print(f"[raw tushare] 增量写入 {updated} 只股票", flush=True)

    # 4. 重建大表
    if updated > 0:
        print("重新合并 daily_raw_all.parquet ...", flush=True)
        compact_raw_prices(str(daily_raw_dir), str(daily_raw_all_fp))

    return {"ok": updated}


def update_raw_prices(inc_offset=20, max_workers=4):
    """增量更新全市场 daily_raw 到最新(带 OHLC),消除 raw 滞后(供每日 daily_update 调用)。

    更新个股文件后重建 daily_raw_all.parquet，保证 load_raw_close() 读大表时不滞后。
    （对应 update_prices() 更新 daily 后调用 compact_prices() 的逻辑。）
    """
    # ── 1. 尝试使用 Tushare 日线批量快速增量更新 ──
    try:
        print("[raw] 尝试使用 Tushare 接口快速增量更新...", flush=True)
        stats = update_raw_prices_tushare()
        if stats and (stats.get("ok", 0) > 0 or stats.get("skipped", False)):
            ok_count = stats.get("ok", 0)
            print(f"[raw] Tushare 增量完成: ok={ok_count} 只", flush=True)
            return {"ok": ok_count, "empty": 0, "error": 0}
    except Exception as exc:
        print(f"[raw] Tushare 快速更新失败: {exc}，将回退到通达信逐股增量更新...", flush=True)

    # ── 2. 回退到通达信逐股增量更新（mootdx） ──
    codes = sorted(fp.stem for fp in Path("data_lake/price/daily_raw").glob("*.parquet"))
    print(f"通达信增量更新不复权 OHLC (mootdx 逐股备份版): {len(codes)}只", flush=True)
    f = TdxRawFetcher(incremental=True, inc_offset=inc_offset, max_workers=max_workers)
    stats = f.run(codes, skip_existing=False)
    if stats.get("failures"):
        f.retry_failures(stats["failures"])
    print(f"[raw] 增量完成 ok={stats['ok']} empty={stats['empty']} err={stats['error']}", flush=True)

    # 重建大表（load_raw_close 优先读 daily_raw_all.parquet，不重建则滞后一日）
    if stats.get("ok", 0) > 0:
        from lake.compact import compact_raw_prices
        compact_raw_prices("data_lake/price/daily_raw", "data_lake/price/daily_raw_all.parquet")

    return stats


if __name__ == "__main__":
    import sys
    if "--incremental" in sys.argv:
        update_raw_prices()
    else:
        codes = sorted(fp.stem for fp in Path("data_lake/price/daily").glob("*.parquet"))
        print(f"通达信全量下载不复权 OHLC: {len(codes)}只", flush=True)
        f = TdxRawFetcher()
        stats = f.run(codes, skip_existing=False)   # 强制重拉,补全历史 OHLC(旧文件只有 raw_close)
        if stats["failures"]:
            f.retry_failures(stats["failures"])
        print(f"✅ 不复权 OHLC: {len(list(Path('data_lake/price/daily_raw').glob('*.parquet')))}只", flush=True)
        # 全量重建大表
        from lake.compact import compact_raw_prices
        compact_raw_prices("data_lake/price/daily_raw", "data_lake/price/daily_raw_all.parquet")
