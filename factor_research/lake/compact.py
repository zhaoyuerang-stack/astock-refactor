"""逐只 parquet 合并为 date×code 大宽表，加速加载。"""
from app_config.log import get_logger

logger = get_logger(__name__)

from pathlib import Path

import pandas as pd

from lake.invariants import assert_price_panel_sane


def _compact_dir(daily_dir, out_path, columns, code_col="code", close_col=None):
    """通用合并：读取 daily_dir 下所有 *.parquet，追加 code 列，合并为长表后输出。"""
    daily_dir = Path(daily_dir)
    out_path = Path(out_path)
    files = sorted(daily_dir.glob("*.parquet"))
    if not files:
        return

    frames = []
    for fp in files:
        df = pd.read_parquet(fp, columns=columns)
        df[code_col] = fp.stem
        frames.append(df)

    long = pd.concat(frames, ignore_index=True)
    # 确保 date 为 datetime
    long["date"] = pd.to_datetime(long["date"])
    # 去重：同一只同一天的保留最后一条
    long = long.drop_duplicates(["date", code_col], keep="last")
    if close_col is not None:
        # 写路径强制体检:逐只文件混入错口径(如不复权进后复权湖)在此拦截,
        # 违规抛 LakeInvariantError,旧大表保留(2026-06-12 假崩盘事故防复发)
        assert_price_panel_sane(long, close_col=close_col)
    long.to_parquet(out_path, index=False)
    logger.info(f"[compact] {out_path.name}: {len(files)} 只 → {len(long)} 行")


def compact_prices(daily_dir, out_path):
    """将逐只 daily parquet 合并为 date×code 大宽表 daily_all.parquet。

    列：date, code, open, high, low, close, volume, amount
    """
    _compact_dir(daily_dir, out_path,
                 columns=["date", "open", "high", "low", "close", "volume", "amount"],
                 close_col="close")


def compact_raw_prices(daily_raw_dir, out_path):
    """将逐只 daily_raw parquet 合并为 date×code 大宽表 daily_raw_all.parquet。

    列：date, code, raw_open, raw_high, raw_low, raw_close
    """
    _compact_dir(daily_raw_dir, out_path,
                 columns=["date", "raw_open", "raw_high", "raw_low", "raw_close"],
                 close_col="raw_close")
