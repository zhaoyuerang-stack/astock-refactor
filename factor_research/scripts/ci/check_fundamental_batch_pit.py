"""check_fundamental_batch_pit.py — DQ-2026-001 双向 PIT 守卫(R-DATA-003)。

对 data_lake/fundamental_batch.parquet 执法三条(消费端统一信任 avail_date,
见 load_lake.load_fundamental_panel / factors/* / strategies/industry_rotation):

  R1 物理零容忍:avail_date < report_date → 违规,无例外(当前 0 行)。
  R2 真值对账:avail_date < true_ann − 2d → 违规(未来函数);±2d 容忍吸收供应商
    日历差(东财周末披露 vs tushare 顺延)。例外 = docs/data_quality/
    DQ-2026-001_early_561.csv 已登记指纹。
  R3 晚知形态:avail_date − report_date > 180d → 违规(重述可合法超期,单行不按
    违规;事故形态是 ≤2025Q1 系统性 +365d 平台)。例外 = 报告期 ≤ 2025-03-31
    事故窗口整段豁免 + DQ-2026-001_late_gt2025q1_403.csv 逐行豁免。

例外只减不增:例外集之外出现任何新违规 → exit 1;已登记例外不再违规 → NOTE
提示收紧(不阻断)。kill condition:根因修复重铺 ann_date 后例外清零、守卫退化
为纯规则,见 docs/data_quality/register.md DQ-2026-001(状态转 CLOSED)。

真值 = 三大报表(income/balancesheet/cashflow) ann_date 按 (code, 报告期) 取 MIN。
检测函数吃 DataFrame + 例外集,fixture 可测;main() 读真实湖,缺文件 fail-closed。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]  # factor_research/
LAKE = ROOT / "data_lake"
FB_FP = LAKE / "fundamental_batch.parquet"
DQ_DIR = ROOT / "docs" / "data_quality"
EARLY_CSV = DQ_DIR / "DQ-2026-001_early_561.csv"
LATE_CSV = DQ_DIR / "DQ-2026-001_late_gt2025q1_403.csv"
STATEMENTS = ["income", "balancesheet", "cashflow"]

EARLY_TOLERANCE_DAYS = 2          # ±2d 供应商日历差容忍(判非违规噪音)
LATE_THRESHOLD_DAYS = 180         # A股法定披露上限最宽口径的宽松上界
INCIDENT_WINDOW_END = pd.Timestamp("2025-03-31")  # DQ-2026-001 事故窗口(2025Q2 断点)


def load_exception_keys(path: Path) -> set[tuple[str, str]]:
    """例外指纹 CSV → {(code, 'YYYY-MM-DD')}。"""
    df = pd.read_csv(path, dtype={"code": str})
    return set(zip(df["code"].str.zfill(6), df["report_date"], strict=True))


def find_violations(
    fb: pd.DataFrame,
    truth: pd.DataFrame,
    early_ex: set[tuple[str, str]],
    late_ex: set[tuple[str, str]],
) -> tuple[list[str], list[str], dict]:
    """执法 R1/R2/R3。fb 需 code/report_date/avail_date(datetime);
    truth 需 code/end_date/true_ann(datetime)。返回 (violations, notes, stats)。"""
    violations: list[str] = []
    notes: list[str] = []
    fb = fb.copy()
    fb["rkey"] = list(
        zip(fb["code"], fb["report_date"].dt.strftime("%Y-%m-%d"), strict=True)
    )

    # R1 物理零容忍:公告早于报告期末,物理不可能
    r1 = fb[fb["avail_date"] < fb["report_date"]]
    for r in r1.itertuples():
        violations.append(
            f"R1 物理早知 {r.code} report={r.report_date:%Y-%m-%d} avail={r.avail_date:%Y-%m-%d}")

    # R2 真值对账:公告早于真实披露日 ≥3 天 = 未来函数(±2d 容忍)
    j = fb.merge(truth, left_on=["code", "report_date"], right_on=["code", "end_date"], how="inner")
    j["diff"] = (j["avail_date"] - j["true_ann"]).dt.days
    r2 = j[j["diff"] < -EARLY_TOLERANCE_DAYS]
    r2_unreg = r2[~r2["rkey"].isin(early_ex)]
    for r in r2_unreg.itertuples():
        violations.append(
            f"R2 真值早知 {r.code} report={r.report_date:%Y-%m-%d} avail={r.avail_date:%Y-%m-%d} "
            f"true={r.true_ann:%Y-%m-%d} 提前{-r.diff}天(R-DATA-003)")
    stale2 = early_ex - set(r2["rkey"])
    if stale2:
        notes.append(f"R2 例外中 {len(stale2)} 行已不构成违规(数据已修复?),"
                     f"可收紧 {EARLY_CSV.name}(只减不增)")

    # R3 晚知形态:断点后仍有超期行(事故窗口整段豁免)
    lag = (fb["avail_date"] - fb["report_date"]).dt.days
    r3 = fb[(lag > LATE_THRESHOLD_DAYS) & (fb["report_date"] > INCIDENT_WINDOW_END)]
    r3_unreg = r3[~r3["rkey"].isin(late_ex)]
    for r in r3_unreg.itertuples():
        violations.append(
            f"R3 晚知 {r.code} report={r.report_date:%Y-%m-%d} "
            f"lag={(r.avail_date - r.report_date).days}天(>{LATE_THRESHOLD_DAYS}d,断点后新增超期)")
    stale3 = late_ex - set(r3["rkey"])
    if stale3:
        notes.append(f"R3 例外中 {len(stale3)} 行已不在断点后晚知集,可收紧 {LATE_CSV.name}")

    stats = {
        "total": len(fb), "join": len(j),
        "r1": len(r1), "r2": len(r2), "r2_unreg": len(r2_unreg),
        "r3": len(r3), "r3_unreg": len(r3_unreg),
    }
    return violations, notes, stats


def _to_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.astype(str).str.replace("-", "").str[:8],
                          format="%Y%m%d", errors="coerce")


def load_real() -> tuple[pd.DataFrame, pd.DataFrame]:
    fb = pd.read_parquet(FB_FP, columns=["code", "report_date", "avail_date"])
    fb["report_date"] = pd.to_datetime(fb["report_date"])
    fb["avail_date"] = pd.to_datetime(fb["avail_date"])
    st = pd.concat(
        [pd.read_parquet(LAKE / "financials" / f"{t}_all.parquet",
                         columns=["ts_code", "end_date", "ann_date"]) for t in STATEMENTS],
        ignore_index=True,
    )
    st = st.dropna(subset=["ann_date", "end_date"])
    st["code"] = st["ts_code"].astype(str).str.split(".").str[0]
    st["end_date"] = _to_date(st["end_date"])
    st["true_ann"] = _to_date(st["ann_date"])
    truth = st.groupby(["code", "end_date"], as_index=False)["true_ann"].min()
    return fb, truth


def main() -> int:
    for fp in (FB_FP, EARLY_CSV, LATE_CSV):
        if not fp.exists():
            print(f"❌ {fp} 不存在 — 守卫无法执法,fail-closed 判红")
            return 1
    fb, truth = load_real()
    early_ex = load_exception_keys(EARLY_CSV)
    late_ex = load_exception_keys(LATE_CSV)
    violations, notes, stats = find_violations(fb, truth, early_ex, late_ex)
    print(f"DQ-2026-001 PIT 守卫:全表 {stats['total']} 行,真值 join {stats['join']} 行;"
          f"R1={stats['r1']} R2={stats['r2']}(未登记 {stats['r2_unreg']}) "
          f"R3={stats['r3']}(未登记 {stats['r3_unreg']})")
    for msg in notes:
        print(f"NOTE: {msg}")
    if violations:
        print(f"❌ 例外集之外新增 {len(violations)} 处 PIT 违规(例外只减不增):")
        for v in violations[:50]:
            print(f"  {v}")
        if len(violations) > 50:
            print(f"  … 其余 {len(violations) - 50} 处略")
        return 1
    print("通过:当前违规全部在 DQ-2026-001 已登记例外集内,无新增。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
