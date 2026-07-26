"""残差相关(扣市场偏相关) —— 根因#2 续:搜索 fitness 的边际罚改用残差法。

raw_corr 把"两腿都只是在跟大盘"误判成"冗余",也可能让"靠抵消市场暴露藏起共同赌注"
的一对漏判。partial_correlation_to_book 先扣掉对市场代理的共同暴露,再看候选与在册
腿是否还同涨同跌——市场来源相同的相关不再罚,真正的策略层冗余才罚。
"""
import numpy as np
import pandas as pd

from factory.autoresearch.novelty import partial_correlation_to_book


def _idx(n=200):
    return pd.bdate_range("2024-01-01", periods=n)


def test_partial_corr_falls_back_to_raw_when_market_flat():
    idx = _idx()
    rng = np.random.default_rng(1)
    cand = pd.Series(rng.normal(0, 1, len(idx)), index=idx)
    book = cand.copy()  # 自相关
    market = pd.Series(0.0, index=idx)  # 市场无方差 → 退回 raw corr
    assert abs(partial_correlation_to_book(cand, [book], market) - 1.0) < 1e-9


def test_partial_corr_nets_out_shared_market_exposure():
    idx = _idx()
    rng = np.random.default_rng(2)
    market = pd.Series(rng.normal(0, 1, len(idx)), index=idx)
    noise_a = pd.Series(rng.normal(0, 0.05, len(idx)), index=idx)
    noise_b = pd.Series(rng.normal(0, 0.05, len(idx)), index=idx)
    # 候选和在册腿都 = 1.0x市场 + 各自独立小噪声 —— 共同点纯是市场暴露,无策略层重叠
    cand = market * 1.0 + noise_a
    book = market * 1.0 + noise_b
    raw = float(cand.corr(book))
    partial = partial_correlation_to_book(cand, [book], market)
    assert raw > 0.9  # raw 看起来高度"冗余"
    assert abs(partial) < 0.3  # 扣市场后基本不冗余(独立噪声主导残差)


def test_partial_corr_catches_offsetting_market_exposure_hiding_common_bet():
    idx = _idx()
    rng = np.random.default_rng(3)
    market = pd.Series(rng.normal(0, 1, len(idx)), index=idx)
    shared_bet = pd.Series(rng.normal(0, 1, len(idx)), index=idx)
    # 候选 = +市场 + 共同赌注;在册腿 = -市场(防御腿) + 同一个共同赌注
    cand = market * 1.0 + shared_bet
    book = market * -1.0 + shared_bet
    raw = float(cand.corr(book))
    partial = partial_correlation_to_book(cand, [book], market)
    assert raw < 0.3  # raw 因为市场暴露反向被"漂白"成低相关
    assert partial > 0.6  # 扣市场后暴露出真正共享的赌注


def test_partial_corr_bounded_and_degenerate_inputs_return_zero():
    idx = _idx()
    flat = pd.Series(0.0, index=idx)
    market = pd.Series(np.random.default_rng(4).normal(0, 1, len(idx)), index=idx)
    assert partial_correlation_to_book(flat, [flat], market) == 0.0
    assert partial_correlation_to_book(flat, [], market) == 0.0
    rng = np.random.default_rng(5)
    cand = pd.Series(rng.normal(0, 1, len(idx)), index=idx)
    book = pd.Series(rng.normal(0, 1, len(idx)), index=idx)
    v = partial_correlation_to_book(cand, [book], market)
    assert -1.0 <= v <= 1.0


if __name__ == "__main__":
    import sys

    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
