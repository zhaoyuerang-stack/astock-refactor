"""check_lake_writers 守卫对抗测试(审计 #4 + ADR-038 AST 级)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci.check_lake_writers import (
    PENDING_REMEDIATION,
    file_is_violation,
    main,
    scan,
    source_has_lake_write,
    source_is_violation_for_module,
)

# ADR-038 决策一:文件级共现误报 6 件,AST 后真实扫描不得再命中
AST_FALSE_POSITIVE_FILES = (
    "scripts/research/archive/hmm_exit_smallcap.py",
    "scripts/research/archive/hmm_stress_guard_smallcap.py",
    "scripts/research/build_largecap_value_quality.py",
    "scripts/research/build_quality_growth.py",
    "scripts/research/fundamental_midcap.py",
    "scripts/research/northbound_factor.py",
)


def test_financials_to_csv_flagged():
    """盲区目录 data_lake/financials + to_csv 组合必红。"""
    src = (
        "from pathlib import Path\n"
        "p = Path('data_lake/financials/income.parquet')\n"
        "df.to_csv(p)\n"
    )
    assert file_is_violation(src)


def test_path_component_data_lake_to_csv_flagged():
    """Path 组件式 ROOT / 'data_lake' / 'version_returns' + to_csv 必红。"""
    src = (
        "store = ROOT / 'data_lake' / 'version_returns'\n"
        "rets.to_csv(store / 'x.csv')\n"
    )
    assert file_is_violation(src)


def test_to_pickle_and_write_table_flagged():
    assert file_is_violation("df.to_pickle('data_lake/event/x.pkl')\n")
    assert file_is_violation("import pyarrow as pa\nwrite_table(t, 'data_lake/meta/x')\n")


def test_lake_package_write_allowed_via_prefix(tmp_path):
    """lake/ 内的写必绿(ALLOWED_PREFIXES)。"""
    lake = tmp_path / "lake"
    lake.mkdir()
    (lake / "writer.py").write_text(
        "df.to_parquet('data_lake/price/daily_all.parquet')\n",
        encoding="utf-8",
    )
    assert main(tmp_path) == 0


def test_read_parquet_not_flagged():
    """读路径(read_parquet)不误报。"""
    src = "df = pd.read_parquet('data_lake/price/daily_all.parquet')\nprint(df)\n"
    assert not file_is_violation(src)


def test_rogue_writer_outside_allowed_fails(tmp_path):
    """非允许前缀目录写湖 → 守卫失败。"""
    apps = tmp_path / "apps"
    apps.mkdir()
    (apps / "evil.py").write_text(
        "df.to_csv('data_lake/holder/x.csv')\n",
        encoding="utf-8",
    )
    assert main(tmp_path) == 1
    assert "apps/evil.py" in scan(tmp_path)


def test_indirect_var_path_true_write_flagged():
    """ADR-038 对抗①:真写湖(变量间接路径)必红。"""
    src = (
        "from pathlib import Path\n"
        "base = Path('data_lake') / 'capital'\n"
        "out = base / 'flow.parquet'\n"
        "df.to_parquet(out)\n"
    )
    assert source_has_lake_write(src)
    assert file_is_violation(src)


def test_read_lake_write_report_not_flagged():
    """ADR-038 对抗①:读湖 + to_csv 到 reports/ 必绿(非写湖)。"""
    src = (
        "import pandas as pd\n"
        "df = pd.read_parquet('data_lake/price/daily_all.parquet')\n"
        "df.to_csv('reports/summary.csv')\n"
    )
    assert not source_has_lake_write(src)
    assert not file_is_violation(src)


def test_six_cooccurrence_false_positives_not_in_live_scan():
    """ADR-038 对抗:6 件共现误报在真实扫描下不再命中(非靠基线掩盖)。"""
    hits = set(scan())
    for rel in AST_FALSE_POSITIVE_FILES:
        assert rel not in hits, f"AST 升级后不应再命中: {rel}"
        assert rel not in PENDING_REMEDIATION, f"误报应已从 PENDING 移除: {rel}"


def test_live_repo_clean_with_pending():
    """真实仓库:存量在 PENDING → 无新增即绿。"""
    assert main() == 0


def test_legacy_version_returns_direct_write_fixture_fails_guard(tmp_path):
    """模拟老直写路径 fixture 在 lake 守卫下必红(审计 #5 销账后无 PENDING 庇护)。"""
    from scripts.ci import check_lake_writers as guard

    # 局部 PENDING 清空:销账后直写 version_returns 即硬红
    orig = dict(guard.PENDING_REMEDIATION)
    guard.PENDING_REMEDIATION.clear()
    try:
        rogue = tmp_path / "workflow"
        rogue.mkdir()
        (rogue / "promote_composite.py").write_text(
            "store = ROOT / 'data_lake' / 'version_returns'\n"
            "store.mkdir(parents=True, exist_ok=True)\n"
            "rets.to_csv(store / 'composite-portfolio__v1.csv', header=True)\n",
            encoding="utf-8",
        )
        research = tmp_path / "scripts" / "research"
        research.mkdir(parents=True)
        (research / "run_nine_gates_all.py").write_text(
            "store = Path('data_lake/version_returns')\n"
            "rets.rename('ret').to_csv(store / f'{fam}__{ver}.csv', header=True)\n",
            encoding="utf-8",
        )
        assert main(tmp_path) == 1
        hits = scan(tmp_path)
        assert "workflow/promote_composite.py" in hits
        assert "scripts/research/run_nine_gates_all.py" in hits
    finally:
        guard.PENDING_REMEDIATION.update(orig)


def test_migrated_writers_produce_valid_sidecar(tmp_path):
    """迁移后写路径产出合法 sidecar(hermetic,不跑真数据)。"""
    import pandas as pd

    from lake.version_returns import (
        config_hash,
        load_verified_version_returns,
        write_version_returns,
    )

    idx = pd.bdate_range("2020-01-02", periods=5)
    rets = pd.Series([0.01, -0.02, 0.005, 0.0, 0.003], index=idx)

    # 模拟 run_nine_gates_all --persist(有 spec_hash)
    p1 = write_version_returns(
        "small-cap-size", "v2.0", rets,
        source="run_nine_gates_all --persist",
        spec_hash="spec-nine-gates-fixture",
        data_fingerprint="fp-hermetic",
        cost_hash="cost-hermetic",
        root=tmp_path,
    )
    assert p1["identity_tier"] == "spec"
    assert p1["source"] == "run_nine_gates_all --persist"
    s1, pr1, r1 = load_verified_version_returns(
        "small-cap-size", "v2.0", root=tmp_path,
    )
    assert r1 == "" and s1 is not None and pr1["series_hash"] == p1["series_hash"]

    # 模拟 promote_composite(config-only)
    p2 = write_version_returns(
        "composite-portfolio", "v1.0", rets,
        source="promote_composite",
        config_hash=config_hash({"allocation": {"a": 0.4}, "rebalance_days": 1}),
        data_fingerprint="fp-hermetic",
        cost_hash="cost-hermetic",
        root=tmp_path,
    )
    assert p2["identity_tier"] == "config-only"
    assert p2["source"] == "promote_composite"
    s2, pr2, r2 = load_verified_version_returns(
        "composite-portfolio", "v1.0", root=tmp_path,
    )
    assert r2 == "" and s2 is not None and pr2["identity_tier"] == "config-only"


def test_pending_no_longer_lists_migrated_version_returns_writers():
    """销账:PENDING 不得再庇护 promote_composite / run_nine_gates_all。"""
    assert "workflow/promote_composite.py" not in PENDING_REMEDIATION
    assert "scripts/research/run_nine_gates_all.py" not in PENDING_REMEDIATION


def test_external_module_write_factor_store_flagged(tmp_path):
    """ADR-038 对抗③:外部模块写 data_lake/factor_store/ 必红。"""
    apps = tmp_path / "apps"
    apps.mkdir()
    (apps / "rogue_fs.py").write_text(
        "df.to_parquet('data_lake/factor_store/panels/x.parquet')\n",
        encoding="utf-8",
    )
    assert main(tmp_path) == 1
    assert "apps/rogue_fs.py" in scan(tmp_path)
    src = "df.to_parquet('data_lake/factor_store/panels/x.parquet')\n"
    assert source_is_violation_for_module(src, "factors/autoresearch_dsl.py")
    assert source_is_violation_for_module(src, "apps/rogue_fs.py")


def test_factor_store_write_price_flagged(tmp_path):
    """ADR-038 对抗③:factor_store 模块写 data_lake/price/ 必红。"""
    fs = tmp_path / "factor_store"
    fs.mkdir()
    (fs / "evil.py").write_text(
        "df.to_parquet('data_lake/price/daily_all.parquet')\n",
        encoding="utf-8",
    )
    assert main(tmp_path) == 1
    assert "factor_store/evil.py" in scan(tmp_path)
    src = "df.to_parquet('data_lake/price/x.parquet')\n"
    assert source_is_violation_for_module(src, "factor_store/store.py")


def test_factor_store_write_own_zone_allowed(tmp_path):
    """factor_store 模块写 data_lake/factor_store/ 子树必绿。"""
    fs = tmp_path / "factor_store"
    fs.mkdir()
    (fs / "ok.py").write_text(
        "df.to_parquet('data_lake/factor_store/panels/cache.parquet')\n",
        encoding="utf-8",
    )
    assert main(tmp_path) == 0
    assert scan(tmp_path) == []


def test_lake_pending_cleared():
    """ADR-038:lake PENDING 清零。"""
    assert PENDING_REMEDIATION == {}
    assert "factor_store/store.py" not in PENDING_REMEDIATION
    assert "factors/autoresearch_dsl.py" not in PENDING_REMEDIATION


def test_write_panel_cache_path_key_equivalence(tmp_path):
    """ADR-038 对抗③:write_panel_cache 与 to_parquet 路径/内容逐位等价。"""
    import pandas as pd

    from factor_store.store import write_panel_cache
    from factors.autoresearch_dsl import _get_cache_path

    idx = pd.bdate_range("2020-01-02", periods=3)
    panel = pd.DataFrame(
        [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]],
        index=idx,
        columns=["000001.SZ", "000002.SZ"],
    )
    # 模拟 DSL 缓存 key 命名(路径公式不变)
    name, params = "volatility", {"window": 20}
    # _get_cache_path 依赖 data mtime;用显式 path 验证 writer 本身
    cache_path = tmp_path / "data_lake" / "factor_store" / "panels" / "vol_w20_srcabc_mt1.parquet"
    direct = tmp_path / "direct.parquet"

    panel.to_parquet(direct)
    out = write_panel_cache(panel, cache_path)
    assert out == cache_path
    assert cache_path.exists()
    loaded_direct = pd.read_parquet(direct)
    loaded_api = pd.read_parquet(cache_path)
    pd.testing.assert_frame_equal(loaded_direct, loaded_api)

    # _get_cache_path 仍指向 data_lake/factor_store/panels(契约未变)
    p = _get_cache_path(name, params, data_signature=None, source_hash="deadbeef")
    assert "data_lake" in p.parts
    assert "factor_store" in p.parts
    assert "panels" in p.parts
    assert name in p.name


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
