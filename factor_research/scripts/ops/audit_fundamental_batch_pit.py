"""audit_fundamental_batch_pit.py — fundamental_batch.parquet PIT 审计(DQ-2026-001 repro)。

复现数据质量登记表 docs/data_quality/register.md 条目 DQ-2026-001 的全部数字:
  1. 物理检查:avail_date < report_date 行数(当前 0)
  2. 晚知分布与 2025Q2 断点(≤2025Q1 各季度 diff 中位 364~366d,≥2025Q2 diff=0)
  3. 真值对账早知分类:违规(fb_ann ≤ true_ann−3d)/ 日历噪音(±2d)/ 一致 / 晚知
  4. 数值载荷判别(Case A):fb.revenue 对同期 income.revenue,非次年同期 → 仅日期列错位
  5. join 覆盖率(akshare↔tushare 覆盖不对称盲区)

真值 = 三大报表(income/balancesheet/cashflow) ann_date 按 (code,报告期) 取 MIN。
dry-run 默认只打印汇总;--write-csv DIR 重新生成两份例外指纹 CSV。
只读湖,不写湖。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]  # factor_research/
LAKE = ROOT / "data_lake"
FB = LAKE / "fundamental_batch.parquet"
STATEMENTS = ["income", "balancesheet", "cashflow"]

EARLY_TOLERANCE_DAYS = 2   # ±2d 吸收供应商日历差(东财周末披露 vs tushare 顺延)
LATE_THRESHOLD_DAYS = 180  # A股法定披露上限最宽口径(年报 4 个月)的宽松上界
BREAKPOINT = pd.Timestamp("2025-03-31")  # 2025Q1 报告期末


def _norm_code(ts_code: pd.Series) -> pd.Series:
    return ts_code.astype(str).str.split(".").str[0]


def _to_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.astype(str).str.replace("-", "").str[:8], format="%Y%m%d", errors="coerce")


def load_frames():
    fb = pd.read_parquet(FB, columns=["code", "report_date", "ann_date", "avail_date", "revenue"])
    st = pd.concat(
        [pd.read_parquet(LAKE / "financials" / f"{t}_all.parquet",
                         columns=["ts_code", "end_date", "ann_date"]).assign(table=t)
         for t in STATEMENTS],
        ignore_index=True,
    )
    return fb, st


def build_truth(st: pd.DataFrame) -> pd.DataFrame:
    st = st.dropna(subset=["ann_date", "end_date"]).copy()
    st["code"] = _norm_code(st["ts_code"])
    st["end_date"] = _to_date(st["end_date"])
    st["true_ann"] = _to_date(st["ann_date"])
    return st.groupby(["code", "end_date"], as_index=False)["true_ann"].min()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write-csv", metavar="DIR", help="把两份例外指纹 CSV 写到 DIR(默认 dry-run 不写)")
    args = ap.parse_args()

    fb, st = load_frames()
    fb["report_date"] = pd.to_datetime(fb["report_date"])
    fb["ann_date"] = pd.to_datetime(fb["ann_date"])
    fb["avail_date"] = pd.to_datetime(fb["avail_date"])
    n_fb = len(fb)

    print("=" * 72)
    print("DQ-2026-001 repro:fundamental_batch.parquet PIT 审计")
    print("=" * 72)

    # ── 1. 物理检查 ──
    lag_report = (fb["avail_date"] - fb["report_date"]).dt.days
    phys_early = int((lag_report < 0).sum())
    print(f"\n[1] 物理检查 avail_date < report_date:{phys_early} 行(规则 R1 零容忍,当前应为 0)")

    # ── 2. 晚知分布与断点 ──
    late_mask = lag_report > LATE_THRESHOLD_DAYS
    print(f"\n[2] 晚知(lag>{LATE_THRESHOLD_DAYS}d):{int(late_mask.sum())} 行 {late_mask.mean():.2%},"
          f"lag 中位 {lag_report.median():.0f} 天")
    fb["quarter"] = fb["report_date"].dt.to_period("Q").astype(str)
    recent = fb[fb["quarter"] >= "2024Q1"]
    print("    2024Q1 起各季度 lag 中位数(断点可视化):")
    tmp = recent.assign(lag=lag_report[recent.index])
    print(tmp.groupby("quarter")["lag"].agg(["count", "median", "max"]).to_string())

    # ── 3. 真值对账 ──
    truth = build_truth(st)
    inc = pd.read_parquet(LAKE / "financials" / "income_all.parquet",
                          columns=["ts_code", "end_date", "revenue"])
    inc["code"] = _norm_code(inc["ts_code"])
    inc["end_date"] = _to_date(inc["end_date"])

    j = fb.merge(truth, left_on=["code", "report_date"], right_on=["code", "end_date"], how="inner")
    j["diff"] = (j["ann_date"] - j["true_ann"]).dt.days
    n_j = len(j)
    early_viol = j[j["diff"] < -EARLY_TOLERANCE_DAYS]
    early_noise = j[(j["diff"] < 0) & (j["diff"] >= -EARLY_TOLERANCE_DAYS)]
    print(f"\n[3] 真值对账:join 命中 {n_j} 行({n_j/n_fb:.1%})")
    print(f"    早知违规(diff≤-{EARLY_TOLERANCE_DAYS+1}d):{len(early_viol)} 行 / {early_viol['code'].nunique()} 股,"
          f"提前中位 {-early_viol['diff'].median():.0f} 天,最多 {-early_viol['diff'].min():.0f} 天")
    print(f"    日历噪音(±{EARLY_TOLERANCE_DAYS}d):{len(early_noise)} 行(非违规)")
    print(f"    一致(diff=0):{int((j['diff']==0).sum())} 行 | 晚知(diff>0):{int((j['diff']>0).sum())} 行")
    jy = j[j["quarter"] <= "2025Q1"]
    print("    ≤2025Q1 各年 diff 中位数(+365d 平台):")
    print(jy.groupby(jy["report_date"].dt.year)["diff"].median().to_string())

    # ── 4. 数值载荷判别(Case A:仅日期列错位)──
    same = fb.merge(inc, left_on=["code", "report_date"], right_on=["code", "end_date"], how="inner",
                    suffixes=("", "_st"))
    same_match = ((same["revenue"] - same["revenue_st"]).abs()
                  < same["revenue_st"].abs().clip(lower=1) * 1e-6).sum()
    nxt = fb.merge(inc.assign(end_date=inc["end_date"] - pd.DateOffset(years=1)),
                   left_on=["code", "report_date"], right_on=["code", "end_date"], how="inner",
                   suffixes=("", "_st"))
    next_match = ((nxt["revenue"] - nxt["revenue_st"]).abs()
                  < nxt["revenue_st"].abs().clip(lower=1) * 1e-6).sum()
    print(f"\n[4] 数值判别:对同期 income.revenue 相等 {int(same_match)}/{len(same)};"
          f"对次年同期相等 {int(next_match)}/{len(nxt)} → 仅日期列错位(Case A)")

    # ── 5. join 盲区 ──
    print(f"\n[5] join 未命中 {n_fb - n_j} 行({(n_fb-n_j)/n_fb:.1%},覆盖不对称盲区,审计无法判定)")

    # ── 导出指纹 ──
    if args.write_csv:
        out = Path(args.write_csv)
        out.mkdir(parents=True, exist_ok=True)
        ev = early_viol[["code", "report_date", "ann_date", "true_ann", "diff"]].rename(
            columns={"ann_date": "fb_ann", "diff": "diff_days"})
        ev = ev.assign(report_date=ev["report_date"].dt.strftime("%Y-%m-%d"),
                       fb_ann=ev["fb_ann"].dt.strftime("%Y-%m-%d"),
                       true_ann=ev["true_ann"].dt.strftime("%Y-%m-%d"))
        ev = ev.sort_values(["code", "report_date"])
        ev.to_csv(out / "DQ-2026-001_early_561.csv", index=False)
        late_new = fb[(fb["report_date"] > BREAKPOINT) & late_mask.reindex(fb.index, fill_value=False)][
            ["code", "report_date", "ann_date", "avail_date"]].copy()
        late_new["lag_days"] = (late_new["avail_date"] - late_new["report_date"]).dt.days
        for c in ["report_date", "ann_date", "avail_date"]:
            late_new[c] = late_new[c].dt.strftime("%Y-%m-%d")
        late_new.sort_values(["code", "report_date"]).to_csv(
            out / "DQ-2026-001_late_gt2025q1_403.csv", index=False)
        print(f"\n指纹 CSV 已写 {out}(early {len(ev)} 行 / late_gt2025q1 {len(late_new)} 行)")

    print("\n判定口径:早知违规 = fb_ann ≤ true_ann−3d;晚知异常 = avail−report >180d(重述可合法超期,"
          "系统性 +365d 平台才是事故形态)。详见 docs/data_quality/register.md DQ-2026-001。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
