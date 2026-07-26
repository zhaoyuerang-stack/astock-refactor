"""lake/fundamental_repair.py 的对抗探针测试(DQ-2026-001)。

覆盖两个纯函数:cascade_true_dates(真值来源优先级级联)与 repair(重铺 ann_date/
avail_date/ann_source)。均不碰磁盘,fixture 全内存构造。
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lake.fundamental_repair import STATUTORY_MAX_DAYS, cascade_true_dates, repair


def _dates(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    """rows: (code, end_date, true_ann)"""
    return pd.DataFrame({
        "code": [r[0] for r in rows],
        "end_date": pd.to_datetime([r[1] for r in rows]),
        "true_ann": pd.to_datetime([r[2] for r in rows]),
    })


def _empty_dates() -> pd.DataFrame:
    return pd.DataFrame(columns=["code", "end_date", "true_ann"])


# ── cascade_true_dates:主源覆盖时必须用主源,即使 fallback 更早 ──
# 这是 DQ-2026-001 修复过程中实测踩过的坑:早期实现把两源无差别取 MIN,
# fallback(侧车)偶尔早于 primary(三表)会被守卫误判为新的"早知"违规。
def test_cascade_primary_wins_even_when_fallback_earlier():
    primary = _dates([("000001", "2025-12-31", "2026-04-28")])
    fallback = _dates([("000001", "2025-12-31", "2026-03-12")])  # 更早,但不应被采信
    out = cascade_true_dates(primary, fallback)
    assert out.iloc[0]["true_ann"] == pd.Timestamp("2026-04-28")


# ── cascade_true_dates:主源缺失该行时,fallback 补位 ──
def test_cascade_fallback_fills_primary_gap():
    primary = _dates([("000001", "2024-06-30", "2024-08-16")])
    fallback = _dates([("000002", "2024-06-30", "2024-08-20")])  # 000002 主源没有
    out = cascade_true_dates(primary, fallback).sort_values("code").reset_index(drop=True)
    assert out.loc[out.code == "000001", "true_ann"].iloc[0] == pd.Timestamp("2024-08-16")
    assert out.loc[out.code == "000002", "true_ann"].iloc[0] == pd.Timestamp("2024-08-20")


# ── cascade_true_dates:两源都缺失该 (code,end_date) → 不在结果集里(留给 repair 兜底)──
def test_cascade_neither_source_absent_key():
    primary = _dates([("000001", "2024-06-30", "2024-08-16")])
    fallback = _empty_dates()
    out = cascade_true_dates(primary, fallback)
    assert set(zip(out.code, out.end_date.dt.strftime("%Y-%m-%d"))) == {("000001", "2024-06-30")}


# ── cascade_true_dates:两源都空,不报错,返回空表 ──
def test_cascade_both_empty():
    out = cascade_true_dates(_empty_dates(), _empty_dates())
    assert len(out) == 0


# ── repair:命中 true_map → ann_date=avail_date=true_ann,ann_source=cross_table_min ──
def test_repair_matched_row_uses_true_ann():
    fb = pd.DataFrame({"code": ["000001"], "report_date": ["2024-06-30"]})
    true_map = _dates([("000001", "2024-06-30", "2024-08-16")])
    out, stat = repair(fb, true_map)
    row = out.iloc[0]
    assert row["ann_date"] == pd.Timestamp("2024-08-16")
    assert row["avail_date"] == pd.Timestamp("2024-08-16")
    assert row["ann_source"] == "cross_table_min"
    assert stat["cross_table_min"] == 1 and stat["proxy_statutory"] == 0


# ── repair:盲区(true_map 无该行)→ ann_date=NaT,avail_date=法定上限代理,诚实未知 ──
def test_repair_blind_spot_uses_statutory_proxy():
    fb = pd.DataFrame({"code": ["999999"], "report_date": ["2024-12-31"]})
    out, stat = repair(fb, _empty_dates())
    row = out.iloc[0]
    assert pd.isna(row["ann_date"])
    expected = pd.Timestamp("2024-12-31") + pd.Timedelta(days=STATUTORY_MAX_DAYS["1231"])
    assert row["avail_date"] == expected
    assert row["ann_source"] == "proxy_statutory"
    assert stat["proxy_statutory"] == 1


# ── repair:R1 物理零容忍必守——四种报告期代理日全部 ≥ report_date ──
def test_repair_proxy_never_precedes_report_date():
    fb = pd.DataFrame({
        "code": ["000001", "000002", "000003", "000004"],
        "report_date": ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31"],
    })
    out, _ = repair(fb, _empty_dates())
    assert (out["avail_date"] >= pd.to_datetime(out["report_date"])).all()


# ── repair:em_last_touch_date 已存在则保留原样(不被真值覆盖)──
def test_repair_preserves_existing_em_last_touch_date():
    fb = pd.DataFrame({
        "code": ["000001"], "report_date": ["2024-06-30"],
        "em_last_touch_date": [pd.Timestamp("2025-08-23")],
    })
    true_map = _dates([("000001", "2024-06-30", "2024-08-16")])
    out, _ = repair(fb, true_map)
    assert out.iloc[0]["em_last_touch_date"] == pd.Timestamp("2025-08-23")
    assert out.iloc[0]["ann_date"] == pd.Timestamp("2024-08-16")  # 不受em_last_touch_date干扰


# ── repair:旧口径改标——em_last_touch_date 缺失但 ann_date 存在(未重铺过的存量行)──
def test_repair_derives_em_last_touch_date_from_legacy_ann_date():
    fb = pd.DataFrame({
        "code": ["000001"], "report_date": ["2024-06-30"],
        "ann_date": [pd.Timestamp("2025-08-23")],  # 旧(污染)ann_date,重铺前的存量口径
    })
    true_map = _dates([("000001", "2024-06-30", "2024-08-16")])
    out, _ = repair(fb, true_map)
    assert out.iloc[0]["em_last_touch_date"] == pd.Timestamp("2025-08-23")  # 旧值改标溯源
    assert out.iloc[0]["ann_date"] == pd.Timestamp("2024-08-16")  # 新 ann_date 已重铺


# ── repair:code 补零一致——3位/6位混合 code 都能正确对齐 true_map ──
def test_repair_zfills_code_for_matching():
    fb = pd.DataFrame({"code": ["1"], "report_date": ["2024-06-30"]})
    true_map = _dates([("000001", "2024-06-30", "2024-08-16")])
    out, stat = repair(fb, true_map)
    assert out.iloc[0]["code"] == "000001"
    assert stat["cross_table_min"] == 1


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
