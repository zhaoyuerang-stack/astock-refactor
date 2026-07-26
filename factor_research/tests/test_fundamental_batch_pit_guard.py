"""check_fundamental_batch_pit 守卫(DQ-2026-001,R-DATA-003)的对抗探针测试。

检测器吃 DataFrame + 例外集,不依赖磁盘真实湖:每类违规先探针抓红(守卫能识别
违规),再验证已登记例外与日历容忍不误伤(守卫能挂 CI 的前提是两类都过得去)。
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci.check_fundamental_batch_pit import find_violations


def _fb(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    """rows: (code, report_date, avail_date)"""
    return pd.DataFrame({
        "code": [r[0] for r in rows],
        "report_date": pd.to_datetime([r[1] for r in rows]),
        "avail_date": pd.to_datetime([r[2] for r in rows]),
    })


def _truth(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    """rows: (code, end_date, true_ann)"""
    return pd.DataFrame({
        "code": [r[0] for r in rows],
        "end_date": pd.to_datetime([r[1] for r in rows]),
        "true_ann": pd.to_datetime([r[2] for r in rows]),
    })


def _run(fb, truth, early_ex=frozenset(), late_ex=frozenset()):
    return find_violations(fb, truth, set(early_ex), set(late_ex))


# ── 干净基线:正常公告(报告期后 28 天,与真值一致)──
def test_clean_baseline_passes():
    fb = _fb([("000001", "2024-03-31", "2024-04-28")])
    truth = _truth([("000001", "2024-03-31", "2024-04-28")])
    violations, notes, stats = _run(fb, truth)
    assert violations == []
    assert stats["r1"] == stats["r2"] == stats["r3"] == 0


# ── 探针 R1:物理不可能(avail 早于报告期末 1 天)──
def test_probe_r1_physical_early_flagged():
    fb = _fb([("000001", "2024-03-31", "2024-03-30")])
    violations, _, stats = _run(fb, _truth([]))
    assert stats["r1"] == 1
    assert any(v.startswith("R1") for v in violations)


# ── 探针 R2:真值早知 30 天(未登记)→ 红 ──
def test_probe_r2_truth_early_unregistered_flagged():
    fb = _fb([("000001", "2023-12-31", "2024-03-01")])
    truth = _truth([("000001", "2023-12-31", "2024-03-31")])
    violations, _, stats = _run(fb, truth)
    assert stats["r2_unreg"] == 1
    assert any("R2" in v and "R-DATA-003" in v for v in violations)


# ── R2 已登记例外:同一违规行在例外集 → 放过;例外之外再加一行 → 仍抓 ──
def test_r2_registered_exception_passes_but_new_row_flagged():
    fb = _fb([
        ("000001", "2023-12-31", "2024-03-01"),  # 已登记事故行
        ("000002", "2023-12-31", "2024-02-01"),  # 新增早知(未登记)
    ])
    truth = _truth([
        ("000001", "2023-12-31", "2024-03-31"),
        ("000002", "2023-12-31", "2024-03-31"),
    ])
    violations, _, stats = _run(fb, truth, early_ex={("000001", "2023-12-31")})
    assert stats["r2"] == 2 and stats["r2_unreg"] == 1
    assert len(violations) == 1 and "000002" in violations[0]


# ── R2 容忍:±2d 供应商日历差判非违规 ──
def test_r2_calendar_tolerance_not_flagged():
    fb_ok = _fb([("000001", "2024-06-30", "2024-08-28")])
    truth = _truth([("000001", "2024-06-30", "2024-08-30")])  # 早 2 天
    violations, _, _ = _run(fb_ok, truth)
    assert violations == []
    fb_bad = _fb([("000001", "2024-06-30", "2024-08-27")])   # 早 3 天
    violations, _, stats = _run(fb_bad, truth)
    assert stats["r2_unreg"] == 1
    assert violations


# ── 探针 R3:断点后新增晚知行(未登记)→ 红 ──
def test_probe_r3_post_breakpoint_late_flagged():
    fb = _fb([("000001", "2025-06-30", "2026-01-15")])  # 断点后 lag 199d
    violations, _, stats = _run(fb, _truth([]))
    assert stats["r3_unreg"] == 1
    assert any(v.startswith("R3") for v in violations)


# ── R3 事故窗口豁免:≤2025Q1 的 +365d 行放过(已登记事故形态)──
def test_r3_incident_window_exempt():
    fb = _fb([("000001", "2024-03-31", "2025-03-31")])  # lag 365d,窗口内
    violations, _, stats = _run(fb, _truth([]))
    assert violations == []
    assert stats["r3"] == 0


# ── R3 已登记例外:断点后晚知行在 403 指纹内 → 放过 ──
def test_r3_registered_exception_passes():
    fb = _fb([("000001", "2025-06-30", "2026-01-15")])
    violations, notes, stats = _run(fb, _truth([]), late_ex={("000001", "2025-06-30")})
    assert violations == []
    assert stats["r3"] == 1 and stats["r3_unreg"] == 0


# ── 收紧信号:例外行已修复(不再违规)→ NOTE 提示收紧,不阻断 ──
def test_stale_exception_emits_note_not_violation():
    fb = _fb([("000001", "2023-12-31", "2024-03-31")])  # 已修复:与真值一致
    truth = _truth([("000001", "2023-12-31", "2024-03-31")])
    violations, notes, _ = _run(fb, truth, early_ex={("000001", "2023-12-31")})
    assert violations == []
    assert any("收紧" in n for n in notes)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
