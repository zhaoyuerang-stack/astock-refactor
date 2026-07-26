"""check_no_legacy_data 守卫(R-DATA-001)的 fixture 测试。

检测器吃 (path, source),不依赖磁盘真实文件:验证「真 import/真路径加载」判违规,
而注释/docstring/裸口径标签/skip-list 等合法提及一律放过(零误报是该守卫能挂 CI 的前提)。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci.check_no_legacy_data import scan_source

P = Path("dummy.py")


def _flagged(src: str) -> bool:
    return len(scan_source(P, src)) > 0


# —— 应判违规(真使用旧口径) ——
def test_import_data_full_flagged():
    assert _flagged("import data_full\n")


def test_from_data_full_flagged():
    assert _flagged("from data_full.prices import load\n")


def test_path_load_flagged():
    assert _flagged('import pandas as pd\npd.read_parquet("data_full/2023/p.parquet")\n')


def test_abs_path_segment_flagged():
    assert _flagged('D = "/Users/x/data_full"\n')


# —— 应放过(合法提及) ——
def test_bare_label_allowed():
    # data_scope 里诚实声明历史口径,非新加载
    assert not _flagged('scope = {"source": "data_full", "bias": True}\n')


def test_skip_set_allowed():
    assert not _flagged('SKIP = {"data_lake", "data_full", "logs"}\n')


def test_comment_allowed():
    assert not _flagged("# legacy data_full retired\nx = 1\n")


def test_docstring_path_mention_allowed():
    # 模块 docstring 里写 "data_full/data_lake 是版本属性" 不该被路径段误判
    assert not _flagged('"""kou jing data_full/data_lake shi ban ben shu xing"""\nx = 1\n')


def test_syntax_error_skipped():
    # 语法错文件不是本守卫职责,跳过(返回空)
    assert scan_source(P, "def broken(:\n") == []


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
