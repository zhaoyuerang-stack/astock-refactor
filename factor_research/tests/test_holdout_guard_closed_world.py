"""Holdout 守卫必须识别无论函数如何命名的直接金库访问。

守卫审计 #2:ISO 日期字面量泛化 + factory/workflow/services/actions 扫描面。
ADR-038 决策二:MONITORED_EXEMPT 显式豁免 + 自检。
"""
import pytest

from scripts.ci import check_holdout_compliance as holdout_guard
from scripts.ci.check_holdout_compliance import (
    EXPECTED_BOUNDARY,
    MONITORED_EXEMPT,
    PENDING_REMEDIATION,
    main,
    scan_direct_holdout_access,
    scan_direct_holdout_dirs,
    validate_monitored_exempt,
)


@pytest.mark.parametrize(
    "source",
    [
        """
HOLDOUT_START = "2025-01-01"
def main(returns):
    return returns.loc[HOLDOUT_START:]
""",
        """
HOLDOUT_START = "2025-01-01"
def evaluate(returns):
    return evaluate_window(returns, HOLDOUT_START, None)
""",
        """
from governance.holdout import boundary
def report(returns):
    b = boundary()
    return returns.loc[returns.index >= b]
""",
        """
def report(returns):
    return returns.loc["2025-01-01":]
""",
        """
START = "2025-01-01"
def report(returns):
    return returns.loc[START:]
""",
        """
from governance.holdout import boundary
BOUNDARY = boundary()
START = BOUNDARY
def report(returns):
    return returns.loc[START:]
""",
        """
from governance.holdout import boundary
def report(returns):
    start = boundary()
    return evaluate_window(returns, start, None)
""",
    ],
)
def test_direct_holdout_access_is_rejected(source):
    assert scan_direct_holdout_access(source, "probe.py")


def test_search_window_truncated_before_boundary_is_allowed():
    source = """
from governance.holdout import boundary
def search(returns):
    b = boundary()
    return returns.loc[returns.index < b]
"""
    assert scan_direct_holdout_access(source, "search.py") == []


def test_mid_vault_iso_date_rejected():
    """审计 #2 对抗:df[df.index >= "2025-06-01"] 老守卫抓不到,新守卫必红。"""
    source = '''
def peek(df):
    return df[df.index >= "2025-06-01"]
'''
    v = scan_direct_holdout_access(source, "peek.py")
    assert v, '>= "2025-06-01" 金库内日期必须被抓'
    assert any("holdout boundary" in m or ">=" in m for m in v)


def test_strict_before_boundary_allowed():
    """df[df.index < "2025-01-01"] 截断语义不变,必绿。"""
    source = '''
def search(df):
    return df[df.index < "2025-01-01"]
'''
    assert scan_direct_holdout_access(source, "search.py") == []


def test_pre_boundary_start_date_not_flagged():
    """起始日常量 "2018-01-01"(< boundary)不误报。"""
    source = '''
START = "2018-01-01"
def load(df):
    return df[df.index >= START]
'''
    assert scan_direct_holdout_access(source, "hist.py") == []


def test_exact_boundary_still_flagged():
    """精确 EXPECTED_BOUNDARY 字面量仍红(回归)。"""
    source = f'''
def peek(df):
    return df[df.index >= "{EXPECTED_BOUNDARY}"]
'''
    assert scan_direct_holdout_access(source, "x.py")


def test_factory_dir_is_scanned(tmp_path):
    """审计 #2 对抗:fixture 放在 factory/ 模拟路径下也必须被扫到。"""
    factory = tmp_path / "factory"
    factory.mkdir()
    evil = factory / "rogue_search.py"
    evil.write_text(
        'def rank(df):\n    return df[df.index >= "2025-06-01"]\n',
        encoding="utf-8",
    )
    # 合法截断对照
    (factory / "clean.py").write_text(
        'def rank(df):\n    return df[df.index < "2025-01-01"]\n',
        encoding="utf-8",
    )
    hits = scan_direct_holdout_dirs(tmp_path)
    rels = {rel for rel, _ in hits}
    assert "factory/rogue_search.py" in rels
    assert "factory/clean.py" not in rels
    assert main(tmp_path) == 1


def test_live_repo_clean_with_pending():
    """真实仓库:配置锁 + 显式豁免 → 无新增即绿;holdout PENDING 清零。"""
    assert main() == 0
    assert PENDING_REMEDIATION == {}


def test_monitored_exempt_missing_adr_fails_guard():
    """ADR-038 对抗②:缺 adr 键的豁免条目守卫必红。"""
    bad = {
        "scripts/research/foo.py": {
            "rationale": "有理由但无 ADR",
        },
    }
    errs = validate_monitored_exempt(bad)
    assert errs, "缺 adr 必须自检失败"
    assert any("adr" in e for e in errs)

    orig = dict(holdout_guard.MONITORED_EXEMPT)
    try:
        holdout_guard.MONITORED_EXEMPT.clear()
        holdout_guard.MONITORED_EXEMPT.update(bad)
        assert main() == 1
    finally:
        holdout_guard.MONITORED_EXEMPT.clear()
        holdout_guard.MONITORED_EXEMPT.update(orig)


def test_monitored_exempt_paper_forward_not_reported():
    """ADR-038 对抗②:豁免文件 paper_forward 不再报金库访问。"""
    rel = "scripts/research/paper_forward_smallcap.py"
    assert rel in MONITORED_EXEMPT
    assert MONITORED_EXEMPT[rel].get("adr") == "ADR-024"
    assert MONITORED_EXEMPT[rel].get("rationale")
    hits = scan_direct_holdout_dirs()  # 默认跳过 MONITORED_EXEMPT
    assert rel not in {r for r, _ in hits}
    assert rel not in PENDING_REMEDIATION


def test_non_exempt_vault_access_still_flagged(tmp_path):
    """ADR-038 对抗②:非豁免文件金库访问仍必红。"""
    research = tmp_path / "scripts" / "research"
    research.mkdir(parents=True)
    (research / "rogue_peek.py").write_text(
        'def peek(df):\n    return df[df.index >= "2025-06-01"]\n',
        encoding="utf-8",
    )
    hits = scan_direct_holdout_dirs(tmp_path)
    assert "scripts/research/rogue_peek.py" in {r for r, _ in hits}
    assert main(tmp_path) == 1


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
