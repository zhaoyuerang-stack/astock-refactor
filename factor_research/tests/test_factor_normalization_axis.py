"""回归防护(ADR-021):canonical 归一化变换必须是【横截面】(axis=1,逐日跨股票),
而非【时间轴】(axis=0 / expanding) —— 后者会把 T+1..T+N 的统计量泄露进 T 日因子值。

审计曾把「l3_walk_forward 先在 full close 上算因子再切片」误判为时间轴泄露;实际安全的
前提正是这些变换逐日独立(横截面)。本测试把该前提钉死:任何人若把 zscore 改成 expanding
或 axis=0,这里立刻红。

判别法:用非对称矩阵——
  · 横截面 zscore(axis=1):每【行】(某一天跨所有股票)均值≈0;
  · 时间轴 zscore(axis=0):每【列】(某只股票跨时间)均值≈0。
两者在非对称数据上结果不同,据此区分。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.neutralize import zscore_series
from factors.alpha.transforms import mad_clip, rank_transform, zscore, zscore_cross_section
from factors.utils import safe_zscore

# 行=日期、列=股票;刻意非对称,使横截面/时间轴结果可区分。
_DF = pd.DataFrame(
    [[1.0, 2.0, 3.0, 10.0],
     [4.0, 4.0, 9.0, -1.0],
     [7.0, 0.0, 2.0, 5.0]],
    index=pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"]),
    columns=["s1", "s2", "s3", "s4"],
)


def _rows_centered(out: pd.DataFrame) -> bool:
    return bool(np.allclose(out.mean(axis=1).values, 0.0, atol=1e-9))


def _cols_centered(out: pd.DataFrame) -> bool:
    return bool(np.allclose(out.mean(axis=0).values, 0.0, atol=1e-9))


def test_zscore_is_cross_sectional():
    out = zscore(_DF)
    assert _rows_centered(out), "zscore 不是逐行(横截面)归一化 → 疑似时间轴泄露"
    assert not _cols_centered(out), "zscore 看起来在 axis=0(时间轴)归一化 → 泄露"


def test_zscore_cross_section_alias_is_cross_sectional():
    out = zscore_cross_section(_DF)
    assert _rows_centered(out), "zscore_cross_section 不是逐行(横截面)归一化"
    assert not _cols_centered(out)
    pd.testing.assert_frame_equal(out, zscore(_DF))


def test_zscore_series_is_one_dimensional():
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    out = zscore_series(s)
    assert abs(float(out.mean())) < 1e-10
    assert abs(float(out.std()) - 1.0) < 1e-8


def test_safe_zscore_is_cross_sectional():
    out = safe_zscore(_DF)
    assert _rows_centered(out), "safe_zscore 不是横截面归一化 → 疑似时间轴泄露"
    assert not _cols_centered(out)


def test_mad_clip_is_cross_sectional():
    # mad_clip 用逐行 median 截尾:每行被裁到 [row_med ± n·row_mad]。
    # 时间轴版会用逐列 median,导致同一行不同股票的上下界不同——这里验证上下界逐行一致。
    out = mad_clip(_DF, n=1.0)
    for dt in _DF.index:
        row_in, row_out = _DF.loc[dt], out.loc[dt]
        med = row_in.median()
        mad = (row_in - med).abs().median()
        lo, hi = med - 1.0 * mad, med + 1.0 * mad
        assert np.allclose(row_out.values, row_in.clip(lo, hi).values), \
            f"mad_clip 在 {dt.date()} 行的截尾界非逐行(横截面)计算 → 疑似时间轴"


def test_rank_transform_is_cross_sectional():
    # 横截面 rank:每行内部独立排名(跨股票)。构造两行相反序,验证排名只看行内。
    df = pd.DataFrame(
        [[1.0, 2.0, 3.0],
         [3.0, 2.0, 1.0]],
        index=pd.to_datetime(["2020-01-01", "2020-01-02"]),
        columns=["a", "b", "c"],
    )
    out = rank_transform(df, ascending=True)
    # row0 升序 → a<b<c;row1 → c<b<a。若是 axis=0(跨时间)排名,结果会不同。
    assert out.loc["2020-01-01", "a"] < out.loc["2020-01-01", "c"], "rank 非横截面"
    assert out.loc["2020-01-02", "a"] > out.loc["2020-01-02", "c"], "rank 非横截面"
    # 每行的秩集合应相同(pct rank 跨列),与另一行无关 → 行独立。
    assert set(np.round(out.loc["2020-01-01"].values, 6)) == \
           set(np.round(out.loc["2020-01-02"].values, 6))


def test_no_expanding_in_transforms_source():
    # 钉死:transforms 源码不得出现 expanding()(全窗会泄露未来);rolling 是 trailing 合法。
    src = (Path(__file__).resolve().parents[1] / "factors" / "alpha" / "transforms.py").read_text()
    assert ".expanding(" not in src, "transforms.py 出现 expanding() → 时间轴未来泄露风险"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
