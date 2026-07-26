"""生产日信号选股与回测同源回归测试(2026-07-11 review)。

历史缺陷:run_daily 第④步手写 veto 过滤(quantile 0.30 字面量)+ nlargest,
与 research_toolkit.apply_veto_filter 语义等价但是复制品——strategies/executable.py
的存在意义就是杜绝公式复制。现收敛为 strategies.executable.select_holdings 唯一入口。
旧代码上本文件 import select_holdings 即失败(对抗性)。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research_toolkit import apply_veto_filter  # noqa: E402
from strategies.executable import select_holdings  # noqa: E402


def _rows(n=100, seed=3):
    rng = np.random.RandomState(seed)
    idx = [f"{i:06d}" for i in range(1, n + 1)]
    factor = pd.Series(rng.randn(n), index=idx)
    veto = pd.Series(rng.randn(n), index=idx)
    return factor, veto


def test_select_holdings_matches_apply_veto_filter():
    factor, veto = _rows()
    got = select_holdings(factor, veto, top_n=10, veto_q=0.30)
    expect = apply_veto_filter(factor, veto, top_n=10, veto_q=0.30).index.tolist()
    assert got == expect, "生产选股必须与回测 apply_veto_filter 逐位一致"


def test_empty_veto_degrades_to_nlargest():
    factor, _ = _rows()
    got = select_holdings(factor, pd.Series(dtype=float), top_n=10, veto_q=0.30)
    assert got == factor.nlargest(10).index.tolist()


def test_zero_veto_q_means_no_policy():
    factor, veto = _rows()
    got = select_holdings(factor, veto, top_n=10, veto_q=0.0)
    assert got == factor.nlargest(10).index.tolist(), "veto_q<=0(policy=none)不得误删最小分位"


def test_insufficient_survivors_returns_empty():
    factor, veto = _rows(n=12)
    # veto_q=0.5 过滤后仅 6 只存活 < top_n=10 → 与回测语义一致:凑不满不出仓
    got = select_holdings(factor, veto, top_n=10, veto_q=0.5)
    assert got == []


def test_run_daily_has_no_handwritten_quantile():
    src = (ROOT / "run_daily.py").read_text(encoding="utf-8")
    assert ".quantile(0.30)" not in src, "run_daily 不得再手写 veto 分位过滤(须走 select_holdings)"
    assert "select_holdings(" in src


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
