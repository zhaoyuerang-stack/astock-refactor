"""path_analysis 标准输出契约 + 反自欺对抗（突变必须红）。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.research.strategy_path_report import (
    HTML_SECTIONS,
    OVERVIEW_KPI_SPECS,
    REPORT_SCHEMA_VERSION,
    _assert_html_contract,
    _scan_forbidden_claims,
    build_analysis,
    format_pct,
    render_html,
    validate_analysis,
    verify_metrics_match_returns,
)


def _synthetic_returns(n=300, seed=0) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-02", periods=n)
    return pd.Series(rng.normal(0.0004, 0.012, size=n), index=idx)


def _meta():
    return {
        "family": "t",
        "version": "v0",
        "status": "参考",
        "path": "executable_spec",
        "leverage": 1.0,
        "timing_type": "ma_trend",
        "nine_gate": {"passed_all": False, "dsr_p": 0.5},
    }


def test_schema_version_and_kpi_order():
    a = build_analysis(_synthetic_returns(), _meta(), skip_timing=True)
    assert a["schema_version"] == REPORT_SCHEMA_VERSION
    assert REPORT_SCHEMA_VERSION == "1.3.1"
    labels = [k["label"] for k in a["overview"]["kpis"]]
    assert labels == [s[1] for s in OVERVIEW_KPI_SPECS]
    assert a["honesty"]["can_claim_valid"] is False
    assert a["honesty"]["metrics_verified_against_returns"] is True
    assert a["honesty"]["registry_metrics_not_used_as_kpi"] is True
    assert a["honesty"]["returns_sha256"]
    assert a["honesty"]["returns_provenance"] in (
        "live_rerun", "stale_csv", "caller_series", "unknown"
    )
    assert "economic_logic" in a
    assert a["economic_logic"]["disclaimer"]
    assert len(a["economic_logic"]["mechanism_map"]) >= 1
    assert "registry_audit" in a
    assert a["registry_audit"]["disclaimer"]
    assert len(HTML_SECTIONS) == 8
    assert "经济逻辑" in HTML_SECTIONS[1]
    validate_analysis(a)


def test_registry_audit_material_mismatch():
    """台账年化与路径差 >1pp → has_material_mismatch，且 KPI 仍用路径数。"""
    from scripts.research.strategy_path_report import build_registry_audit

    full = {
        "n": 100, "annual": 0.20, "vol": 0.15, "sharpe": 1.0,
        "maxdd": -0.18, "calmar": 1.1, "end_nav": 2.0,
        "start": "2018-01-01", "end": "2020-01-01", "hit": True,
    }
    meta = {
        "registry_metrics": {
            "annual": 0.08,  # 差 12pp
            "maxdd": -0.18,
            "sharpe": 1.0,
            "hit": True,
        }
    }
    ra = build_registry_audit(full, meta)
    assert ra["has_material_mismatch"] is True
    annual_row = next(r for r in ra["rows"] if r["key"] == "annual")
    assert annual_row["material"] is True
    assert annual_row["status"] == "mismatch"
    # 路径值不得被台账覆盖
    assert annual_row["path_value"] == 0.20
    assert annual_row["registry_value"] == 0.08


def test_stale_csv_requires_flag():
    """CLI：仅 --returns-csv 无 --allow-stale-csv 必须拒绝。"""
    import tempfile
    from pathlib import Path

    from scripts.research.strategy_path_report import main

    ret = _synthetic_returns()
    with tempfile.TemporaryDirectory() as td:
        csv_path = Path(td) / "r.csv"
        ret.to_csv(csv_path, header=["ret"])
        with pytest.raises(SystemExit):
            main([
                "--family", "t", "--version", "v0",
                "--returns-csv", str(csv_path),
                "--out-dir", td,
                "--no-html",
                "--skip-timing",
            ])


def test_attach_path_metrics_live_only_and_archives_old(tmp_path, monkeypatch):
    """台账 metrics 纠偏：只接受 live_rerun；旧数进 history；hit 重算。"""
    import strategy_registry as R

    reg = tmp_path / "sv.json"
    monkeypatch.setattr(R, "REGISTRY", reg)
    R.register_family("factfam", "事实族")
    R.register(
        "factfam", "v1", "d", {"x": 1}, {},
        {"annual": 0.30, "maxdd": -0.10, "sharpe": 1.5, "hit": True},
        status="参考",
        nine_gate={"passed_all": False, "dsr_p": 0.5},
    )
    with pytest.raises(ValueError, match="live_rerun"):
        R.attach_path_metrics(
            "factfam", "v1",
            {"annual": 0.11, "maxdd": -0.19, "sharpe": 0.8},
            provenance="stale_csv",
        )
    out = R.attach_path_metrics(
        "factfam", "v1",
        {"annual": 0.11, "maxdd": -0.19, "sharpe": 0.8},
        provenance="live_rerun",
        window="2018→2026",
        n=2000,
        returns_sha256="abc",
        path="strategies.x",
    )
    assert out["hit"] is False  # 11% / -19% 不满足 hit
    v = R._load()["families"][0]["versions"][0]
    assert v["status"] == "参考"  # 不改生命周期
    assert abs(v["metrics"]["annual"] - 0.11) < 1e-12
    assert v["metrics"]["source"] == "path_live_rerun"
    assert v["metrics"]["hit"] is False
    assert v["evidence"]["metrics_history"]
    assert v["evidence"]["metrics_history"][-1]["metrics"]["annual"] == 0.30
    assert v["nine_gate"].get("stale_vs_path_metrics") is True


def test_economic_logic_missing_fails():
    a = build_analysis(_synthetic_returns(), _meta(), skip_timing=True)
    del a["economic_logic"]
    with pytest.raises(ValueError, match="economic_logic"):
        validate_analysis(a)


def test_economic_logic_from_registry_size_earnings():
    """真实 family 应拉到 hypothesis 并做 MaxDD 边界对照。"""
    from scripts.research.strategy_path_report import build_economic_logic

    meta = {
        "family": "size-earnings",
        "version": "v1.0",
        "status": "参考",
        "path": "strategies.size_earnings.run_strategy",
        "leverage": 1.1,
        "timing_type": "pure_trend*vol_target",
    }
    full = {
        "n": 100, "annual": 0.12, "vol": 0.15, "sharpe": 0.8,
        "maxdd": -0.26, "calmar": 0.46, "end_nav": 2.0,
        "start": "2018-01-01", "end": "2026-01-01", "hit": False,
    }
    dd = {"maxdd": -0.26, "peak": "2023-03-01", "trough": "2024-09-23",
          "under_10pct_days": 0, "under_20pct_days": 0}
    yearly = [
        {"year": 2018, "ret": -0.12, "ann": -0.12, "vol": 0.2, "sharpe": -0.5,
         "maxdd": -0.2, "n": 240, "nav_ye": 0.88, "comment": "x"},
        {"year": 2025, "ret": 0.5, "ann": 0.5, "vol": 0.2, "sharpe": 2.0,
         "maxdd": -0.1, "n": 240, "nav_ye": 1.5, "comment": "x"},
    ]
    econ = build_economic_logic(meta, full, dd, yearly, None)
    assert "散户" in (econ["hypothesis"] or "") or len(econ["hypothesis"]) > 10
    assert econ["failure_boundaries"]
    ids = {r["id"] for r in econ["path_vs_thesis"]}
    assert "failure_boundary_maxdd" in ids
    assert "year_skew" in ids
    # size-earnings failure max_drawdown is -0.25; observed -0.26 → breached
    mdd_row = next(r for r in econ["path_vs_thesis"] if r["id"] == "failure_boundary_maxdd")
    assert mdd_row.get("status") == "breached"


def test_validate_rejects_wrong_kpi_order():
    a = build_analysis(_synthetic_returns(), _meta(), skip_timing=True)
    a["overview"]["kpis"][0]["label"] = "年化"
    a["overview"]["kpis"][0]["key"] = "annual"
    with pytest.raises(ValueError, match="overview.kpis"):
        validate_analysis(a)


def test_format_pct_unicode_minus_and_unsigned_vol():
    assert format_pct(-0.1234, 2).startswith("−")
    assert "+" not in format_pct(0.181, 1, signed=False)
    assert format_pct(0.2315, 2).startswith("+")


def test_display_must_match_value_mutation():
    """对抗：美化 display 必须被抓。"""
    a = build_analysis(_synthetic_returns(), _meta(), skip_timing=True)
    a["overview"]["kpis"][1]["display"] = "+99.99%"  # annual 卡
    with pytest.raises(ValueError, match="display"):
        validate_analysis(a)


def test_metrics_tamper_must_fail():
    """对抗：手改 full.annual 与收益序列不一致 → 红。"""
    ret = _synthetic_returns()
    a = build_analysis(ret, _meta(), skip_timing=True)
    a["full"]["annual"] = 0.99
    with pytest.raises(ValueError, match="不一致"):
        verify_metrics_match_returns(ret, a["full"])


def test_forbidden_claim_in_one_liner():
    a = build_analysis(_synthetic_returns(), _meta(), skip_timing=True)
    a["overview"]["one_liner"] = "该策略有效，建议在册"
    with pytest.raises(ValueError, match="禁词"):
        validate_analysis(a)


def test_can_claim_valid_must_stay_false():
    a = build_analysis(_synthetic_returns(), _meta(), skip_timing=True)
    a["honesty"]["can_claim_valid"] = True
    with pytest.raises(ValueError, match="can_claim_valid"):
        validate_analysis(a)


def test_html_contract_rejects_missing_section(tmp_path):
    a = build_analysis(_synthetic_returns(), _meta(), skip_timing=True)
    html_path = tmp_path / "t.html"
    render_html(a, html_path)
    bad = html_path.read_text(encoding="utf-8").replace(HTML_SECTIONS[0], "1. 随便")
    with pytest.raises(ValueError, match="缺冻结章节"):
        _assert_html_contract(bad, a)


def test_html_contains_honesty_banner(tmp_path):
    a = build_analysis(_synthetic_returns(), _meta(), skip_timing=True)
    html_path = tmp_path / "t.html"
    render_html(a, html_path)
    html = html_path.read_text(encoding="utf-8")
    assert "反自欺声明" in html
    assert "can_claim_valid" in html
    assert a["schema_version"] in html
    _assert_html_contract(html, a)


def test_scan_forbidden_direct():
    with pytest.raises(ValueError, match="禁词"):
        _scan_forbidden_claims("这是神策略，稳赚", "x")


def test_honesty_banner_not_false_positive():
    """横幅含「有效」等词的否定语境不得误伤。"""
    from scripts.research.strategy_path_report import HONESTY_BANNER
    # 横幅本身允许；扫的是肯定宣称
    _scan_forbidden_claims(HONESTY_BANNER, "banner_self")


def test_not_admission_flags_immutable():
    a = build_analysis(_synthetic_returns(), _meta(), skip_timing=True)
    a["honesty"]["not_alpha_evidence"] = False
    with pytest.raises(ValueError, match="not_alpha_evidence"):
        validate_analysis(a)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
