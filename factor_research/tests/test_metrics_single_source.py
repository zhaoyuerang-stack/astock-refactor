"""绩效公式单一权威锁(架构评审 2026-07-18:口径静默分叉风险)。

锁三件事(全合成数据,零 data_lake 依赖):
1. 行为不变:BacktestResult 五个标量 property == 历史内联公式逐位复算;
2. 委托为真:BacktestResult.metrics == engine.metrics.metrics()(仅 hit 按
   config 覆盖语义差异),且 monkeypatch canonical 函数必须传播到 property——
   在"各自内联一套公式"的旧实现上本测试必然失败;
3. n<100 哨兵与空序列语义保持。

运行:cd factor_research && python3 -m pytest tests/test_metrics_single_source.py -q
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core.engine as ce  # noqa: E402
import engine.metrics as em  # noqa: E402
from core.engine import BacktestConfig, BacktestResult  # noqa: E402


def _make_result(n=300, seed=7, config=None):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=n)
    ret = pd.Series(rng.normal(0.0005, 0.01, n), index=idx)
    zero = pd.Series(0.0, index=idx)
    return BacktestResult(returns=ret, turnover=zero, cost=zero, config=config), ret


def test_scalar_properties_match_historical_inline_formulas():
    res, ret = _make_result()
    cum = (1 + ret).cumprod()
    maxdd_inline = float((cum / cum.cummax() - 1).min())
    assert res.annual == float(ret.mean() * 252)
    assert res.vol == float(ret.std() * np.sqrt(252))
    assert res.sharpe == res.annual / res.vol
    assert res.maxdd == maxdd_inline
    expected_calmar = res.annual / abs(maxdd_inline) if maxdd_inline < 0 else 0.0
    assert res.calmar == expected_calmar


def test_metrics_dict_delegates_to_canonical_metrics():
    res, ret = _make_result()
    m = res.metrics
    cm = em.metrics(ret)
    cm["hit"] = res.hit  # 唯一允许的差异:hit 按 config 可覆盖阈值
    assert m == cm


def test_hit_honors_config_thresholds_over_canonical_defaults():
    # 构造一条年化远超默认门槛的序列;调高 config 门槛后 hit 必须翻转
    idx = pd.bdate_range("2022-01-03", periods=300)
    ret = pd.Series(0.002, index=idx)  # 年化 ~50%,回撤 0
    zero = pd.Series(0.0, index=idx)
    easy = BacktestResult(returns=ret, turnover=zero, cost=zero,
                          config=BacktestConfig(target_annual=0.15, target_maxdd=0.20))
    hard = BacktestResult(returns=ret, turnover=zero, cost=zero,
                          config=BacktestConfig(target_annual=9.99, target_maxdd=0.20))
    assert easy.metrics["hit"] is True
    assert hard.metrics["hit"] is False


def test_short_series_sentinel_preserved():
    res, _ = _make_result(n=50)
    assert res.metrics == {
        "annual": -1.0, "vol": 0.0, "sharpe": -1.0,
        "maxdd": -1.0, "calmar": 0.0, "hit": False, "n": 50,
    }


def test_empty_series_semantics():
    idx = pd.DatetimeIndex([], dtype="datetime64[ns]")
    empty = pd.Series(dtype=float, index=idx)
    res = BacktestResult(returns=empty, turnover=empty, cost=empty)
    assert np.isnan(res.maxdd)
    assert res.sharpe == 0.0 and res.calmar == 0.0


def test_mutation_propagates_proving_delegation(monkeypatch):
    """对抗:canonical 函数被替换后 property 必须跟着变。

    旧实现(BacktestResult 内联公式)下 core.engine 没有 annual_return 名字,
    monkeypatch 直接 AttributeError——本测试在旧码上机械失败,防止有人把
    委托改回内联拷贝后测试依然全绿。
    """
    res, _ = _make_result()
    monkeypatch.setattr(ce, "annual_return", lambda r: 123.456)
    assert res.annual == 123.456
    monkeypatch.setattr(ce, "max_drawdown", lambda r: -0.5)
    assert res.maxdd == -0.5
    monkeypatch.setattr(ce, "canonical_metrics", lambda r: {"annual": 1.0, "n": len(r)})
    assert res.metrics["annual"] == 1.0


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
