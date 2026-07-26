"""Gate7 延迟执行移位 + Gate3 禁未来回填 回归测试(2026-07-11 review)。

对抗性说明:
  · 旧代码 Gate7 用 weights.shift(delay) 按行移——稀疏决策面板(每 20 天一行)
    等于延迟一个调仓周期;新实现 _shift_decision_weights 按交易日历精确移 delay 天。
  · 旧代码 Gate3 neutral_factor ffill().bfill() 把未来残差回填到更早日期。
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.analysis.nine_gates import _shift_decision_weights  # noqa: E402


def _sparse_weights(trade_index, every=20, n_stocks=3):
    cols = [f"00000{i}" for i in range(1, n_stocks + 1)]
    rows = trade_index[::every]
    return pd.DataFrame(1.0 / n_stocks, index=rows, columns=cols)


def test_delay_shift_moves_exactly_n_trading_days():
    idx = pd.bdate_range("2023-01-02", periods=300)
    w = _sparse_weights(idx, every=20)
    for delay in (1, 2):
        shifted = _shift_decision_weights(w, idx, delay)
        assert len(shifted) == len(w) or len(shifted) == len(w) - 1  # 末行可能越界丢弃
        for orig, new in zip(w.index, shifted.index, strict=False):
            gap = idx.searchsorted(new) - idx.searchsorted(orig)
            assert gap == delay, (
                f"决策日 {orig.date()} 应移 {delay} 个交易日,实际移了 {gap} 个"
                f"(旧代码按行移会得到 20)")


def test_delay_shift_drops_out_of_range_rows():
    idx = pd.bdate_range("2023-01-02", periods=40)
    w = _sparse_weights(idx, every=39)  # 第二行落在最后一个交易日,+1 越界
    shifted = _shift_decision_weights(w, idx, 1)
    assert all(d in idx for d in shifted.index)
    assert len(shifted) == 1  # 末行越界被丢弃,不得 fillna(0) 污染


def test_gate3_source_has_no_bfill():
    """Gate3 中性化面板禁止 bfill(未来残差回填到更早日期 = 未来函数隐患)。

    与仓库 CI 守卫同风格的源码级机械断言:旧代码含 .bfill() 必失败。
    """
    import inspect

    from core.analysis.nine_gates import NineGatesEvaluator

    src = inspect.getsource(NineGatesEvaluator.run_gate3_neutralization)
    assert ".bfill(" not in src, "run_gate3_neutralization 不得对中性化面板做未来回填(bfill)"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
