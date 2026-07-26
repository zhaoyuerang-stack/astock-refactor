"""fundamental_batch.parquet 的 ann_date/avail_date 重铺逻辑(DQ-2026-001)。

根因(已实锤定位):`lake/schema.py::YJBB_RENAME` 曾把东财 `stock_yjbb_em` 返回的
"最新公告日期"列(公司级、随该公司最近一次披露滚动刷新,不是本报告期专属公告日)
直接改名成 `ann_date` 消费。现场复现:`ak.stock_yjbb_em(date="20240630")` 今日查询,
000001 的"最新公告日期"返回 2025-08-23(该公司最近一次披露日),而 2024-06-30 中报
真实公告日是 2024-08-16(tushare income 现网核实)——是本仓 RENAME 时的语义误用,
不是东财数据本身出错。该列现已改名 `em_last_touch_date`,仅留作审计溯源,禁作 PIT。

本模块是共享核心(R-ARCH-001:lake. 不得 import scripts.,故重铺逻辑必须落在 lake/
而非 scripts/data/,供两处调用方各自在自己的写入点使用):
  · scripts/data/build_fundamental_batch.py(全量重建)
  · scripts/data/repair_fundamental_batch_ann.py(存量湖数据独立修复 CLI)
  · lake/update.py::update_fundamental()(日更增量,同样需要重铺,否则新增报告期
    继续用未修复的粗糙 +45d 兜底)

真值来源,优先级级联(非无脑 MIN,避免不同来源互相打架):
  1. 主源:income/balancesheet/cashflow 三表自带 ann_date 按 (code,end_date) 取 MIN
     ——与 scripts/ci/check_fundamental_batch_pit.py 的真值定义逐字一致,任何三表
     覆盖到的行,此处产出的"真值"与守卫核对时用的必然相同,结构性消灭 R2 打架。
  2. 补位源(仅三表均缺失该 (code,end_date) 时生效):income f_ann_date 侧车
     (financials/f_ann_date_income.parquet,tushare 首次公告日,build_f_ann_sidecar.py
     产出)。三表缺失时守卫的 truth 同样查不到该行(inner join 会丢掉),侧车只填
     盲区,不会与守卫产生分歧。
  3. 兜底(两源均不可得,主要为北交所+退市尾):avail_date = report_date + 法定披露
     上限(Q1+30d/中报+62d/Q3+31d/年报+120d,宁晚不泄),ann_date = NaT(诚实未知)。

早期实现曾把侧车与三表一起无差别取 MIN,导致侧车个别早于三表 ann_date 的行(如
预告式披露)被误判"早知"违规——现级联结构从根上避免。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

LAKE = Path(__file__).resolve().parent.parent / "data_lake"
FANN_FP = LAKE / "financials" / "f_ann_date_income.parquet"
STATEMENT_TABLES = ["income", "balancesheet", "cashflow"]  # 已在盘 canonical 表,与守卫真值定义对齐

# A股法定披露上限(自然日,取宽):一季报 4/30、中报 8/31、三季报 10/31、年报 4/30
STATUTORY_MAX_DAYS = {"0331": 30, "0630": 62, "0930": 31, "1231": 120}


def _to_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.astype(str).str.replace("-", "").str[:8],
                          format="%Y%m%d", errors="coerce")


def cascade_true_dates(primary: pd.DataFrame, fallback: pd.DataFrame) -> pd.DataFrame:
    """纯函数(可测,不碰磁盘):按 (code, end_date) 把 primary(三表 MIN)与
    fallback(侧车)级联——primary 有值就用 primary,只有 primary 缺失该
    (code,end_date) 才落到 fallback。两参数均需含 code/end_date/true_ann 列。

    与"两源一起取 MIN"的区别:即使 fallback 对某行给出比 primary 更早的日期,
    只要 primary 覆盖了该行就不采信 fallback——primary(三表 MIN)与守卫的真值
    定义逐字一致,采信更早但来源不同的 fallback 值会制造"重铺口径 vs 守卫口径"
    的分歧(DQ-2026-001 修复过程中实测踩到:2025-12-31 报告期个别预告式披露,
    侧车早于三表 ann_date,若一起取 MIN 会被守卫判定为新的早知违规)。
    """
    fb = fallback.rename(columns={"true_ann": "fann"})
    merged = primary.merge(fb, on=["code", "end_date"], how="outer")
    if "true_ann" not in merged.columns:
        merged["true_ann"] = pd.NaT
    merged["true_ann"] = merged["true_ann"].fillna(merged.get("fann"))
    return merged[["code", "end_date", "true_ann"]]


def _statement_min() -> pd.DataFrame:
    frames = []
    for name in STATEMENT_TABLES:
        fp = LAKE / "financials" / f"{name}_all.parquet"
        if not fp.exists():
            continue
        df = pd.read_parquet(fp, columns=["ts_code", "ann_date", "end_date"]).dropna(
            subset=["ann_date", "end_date"])
        df["code"] = df["ts_code"].astype(str).str.split(".").str[0]
        df["end_date"] = _to_date(df["end_date"])
        df["true_ann"] = _to_date(df["ann_date"])
        frames.append(df[["code", "end_date", "true_ann"]])
    if not frames:
        return pd.DataFrame(columns=["code", "end_date", "true_ann"])
    allsrc = pd.concat(frames, ignore_index=True).dropna(subset=["true_ann", "end_date"])
    return allsrc.groupby(["code", "end_date"], as_index=False)["true_ann"].min()


def _fann_sidecar() -> pd.DataFrame:
    if not FANN_FP.exists():
        return pd.DataFrame(columns=["code", "end_date", "true_ann"])
    fa = pd.read_parquet(FANN_FP)
    fa = fa[fa["f_ann_date"].astype(str).str.len() >= 8].copy()
    fa["code"] = fa["ts_code"].astype(str).str.split(".").str[0]
    fa["end_date"] = _to_date(fa["end_date"])
    fa["true_ann"] = _to_date(fa["f_ann_date"])
    return fa.groupby(["code", "end_date"], as_index=False)["true_ann"].min()


def build_true_date_map() -> pd.DataFrame:
    """(code, end_date) → 最早可信公告日,见模块 docstring 的三级优先级级联。
    任一输入表缺失时该级静默跳过(不 raise),让下一级兜底接手。I/O 外壳,
    级联逻辑本体见纯函数 cascade_true_dates(便于测试注入 fixture)。"""
    return cascade_true_dates(_statement_min(), _fann_sidecar())


def repair(fb: pd.DataFrame, true_map: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """fb 需 code/report_date,可选已有 em_last_touch_date/ann_date(旧口径改标用)。
    返回 (重铺后 DataFrame 含 ann_date/avail_date/em_last_touch_date/ann_source, 统计dict)。"""
    out = fb.copy()
    out["code"] = out["code"].astype(str).str.zfill(6)
    out["report_date"] = pd.to_datetime(out["report_date"])
    if "em_last_touch_date" not in out.columns:
        if "ann_date" in out.columns:
            out["em_last_touch_date"] = pd.to_datetime(out["ann_date"])  # 旧口径改标溯源
        else:
            out["em_last_touch_date"] = pd.NaT

    merged = out.merge(true_map, left_on=["code", "report_date"],
                       right_on=["code", "end_date"], how="left").drop(columns=["end_date"])
    md = merged["report_date"].dt.strftime("%m%d")
    proxy = merged["report_date"] + pd.to_timedelta(md.map(STATUTORY_MAX_DAYS), unit="D")

    merged["ann_date"] = merged["true_ann"]                     # NaT = 诚实未知(盲区)
    merged["avail_date"] = merged["true_ann"].fillna(proxy)
    merged["ann_source"] = merged["true_ann"].notna().map(
        {True: "cross_table_min", False: "proxy_statutory"})
    merged = merged.drop(columns=["true_ann"])

    stat = {
        "total": len(merged),
        "cross_table_min": int((merged["ann_source"] == "cross_table_min").sum()),
        "proxy_statutory": int((merged["ann_source"] == "proxy_statutory").sum()),
    }
    return merged, stat
