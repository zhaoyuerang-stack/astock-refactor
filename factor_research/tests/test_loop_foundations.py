"""Loop 防自欺地基测试:trial 账本(§5.1)+ holdout 金库(§5.2)。"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from governance import holdout as HO
from governance import trial_ledger as TL


# ---------------- trial 账本 ----------------
def test_cumulative_grows_and_penalizes(tmp_path):
    p = tmp_path / "tl.jsonl"
    assert TL.cumulative_trials("famX", path=p) == 0
    TL.record_trials("famX", 50, context="param grid", path=p)
    TL.record_trials("famX", 4, context="structural arms", path=p)
    TL.record_trials("famY", 10, path=p)
    assert TL.cumulative_trials("famX", path=p) == 54     # 只算本 scope
    assert TL.cumulative_trials(path=p) == 64             # 全局
    assert TL.honest_n_trials("famX", path=p) == 54


def test_honest_n_trials_unknown_is_zero(tmp_path):
    p = tmp_path / "tl.jsonl"
    assert TL.honest_n_trials("none", path=p) == 0


def test_record_rejects_zero(tmp_path):
    with pytest.raises(ValueError):
        TL.record_trials("f", 0, path=tmp_path / "x.jsonl")


def test_dsr_penalty_uses_honest_count(tmp_path):
    # 账本累计越大 → DSR p 越大(惩罚越重),证明地基真接上了 DSR
    from core.analysis.walk_forward import deflated_sharpe
    p = tmp_path / "tl.jsonl"
    TL.record_trials("fam", 1, path=p)
    p1 = deflated_sharpe(observed_sr=1.5, n_trials=TL.honest_n_trials("fam", path=p),
                         n_periods=1500, skew=0.5, kurt=5.0)["p_value"]
    TL.record_trials("fam", 200, path=p)
    p2 = deflated_sharpe(observed_sr=1.5, n_trials=TL.honest_n_trials("fam", path=p),
                         n_periods=1500, skew=0.5, kurt=5.0)["p_value"]
    assert p2 > p1


# ---------------- holdout 金库 ----------------
def _series(start, end):
    idx = pd.date_range(start, end, freq="B")
    return pd.Series(np.random.default_rng(0).normal(0.001, 0.01, len(idx)), index=idx)


def test_boundary_reads_settings():
    b = HO.boundary()
    assert isinstance(b, pd.Timestamp)
    assert b == pd.Timestamp("2025-01-01")  # 当前 settings.yaml


def test_assert_search_clean_blocks_holdout_touch():
    b = HO.boundary()
    clean = _series("2018-01-01", b - pd.Timedelta(days=1))
    HO.assert_search_clean(clean, label="search")        # < boundary → 通过
    dirty = _series("2018-01-01", "2026-01-01")           # 跨进金库 → 抛
    with pytest.raises(HO.HoldoutBreach):
        HO.assert_search_clean(dirty, label="search")


def test_validate_on_holdout_retry_is_idempotent(tmp_path):
    p = tmp_path / "ho.jsonl"
    r = _series("2018-01-01", "2026-01-01")
    kwargs = {"spec_hash": "spec-1", "data_fingerprint": "data-1", "path": p}
    v1 = HO.validate_on_holdout("cand-1", r, **kwargs)
    assert v1["peek_count"] == 1 and "warning" not in v1
    assert v1["n"] > 0                                     # holdout 段确有数据
    v2 = HO.validate_on_holdout("cand-1", r, **kwargs)
    assert v2["peek_count"] == 1 and v2["idempotent_retry"] is True


# ---------------- §5.3 边际 alpha ----------------
from governance import decay as DC
from governance import marginal as MG


def test_marginal_flags_redundant():
    rng = np.random.default_rng(0)
    base = pd.Series(rng.normal(0.001, 0.01, 600),
                     index=pd.date_range("2020-01-01", periods=600, freq="B"))
    # 候选 = base 的轻微变体(高相关、无独立信号)→ 冗余
    cand = base * 1.02 + rng.normal(0, 0.0005, 600)
    cand.index = base.index
    out = MG.marginal_alpha(cand, {"illiq": base})
    assert out["corr_to_book"] > 0.7
    assert "冗余" in out["marginal_verdict"]


def test_marginal_first_leg():
    r = pd.Series(np.random.default_rng(1).normal(0.001, 0.01, 300),
                  index=pd.date_range("2020-01-01", periods=300, freq="B"))
    out = MG.marginal_alpha(r, {})
    assert "首腿" in out["marginal_verdict"]


def test_marginal_independent_leg_has_alpha():
    rng = np.random.default_rng(2)
    idx = pd.date_range("2020-01-01", periods=800, freq="B")
    book = pd.Series(rng.normal(0.0005, 0.01, 800), index=idx)
    # 候选 = 独立正收益,与 book 不相关 → 有边际 alpha
    cand = pd.Series(rng.normal(0.0015, 0.008, 800), index=idx)
    out = MG.marginal_alpha(cand, {"book": book})
    assert abs(out["corr_to_book"]) < 0.3
    assert out["residual_sharpe"] > 0


# ---------------- §5.4 衰减监控 ----------------
def test_decay_flags_low_rolling_sharpe():
    idx = pd.date_range("2018-01-01", periods=1000, freq="B")
    weak = pd.Series(np.random.default_rng(3).normal(-0.0005, 0.01, 1000), index=idx)  # 负漂移→夏普<0.5
    out = DC.decay_check(weak)
    assert out["decayed"] is True and any("滚动3年夏普" in r for r in out["reasons"])


def test_decay_healthy_strong_returns():
    idx = pd.date_range("2018-01-01", periods=1000, freq="B")
    strong = pd.Series(np.random.default_rng(4).normal(0.0015, 0.008, 1000), index=idx)
    out = DC.decay_check(strong)
    assert out["decayed"] is False


def test_decay_ic_consecutive_negative():
    idx = pd.date_range("2018-01-01", periods=1000, freq="B")
    strong = pd.Series(np.random.default_rng(5).normal(0.0015, 0.008, 1000), index=idx)
    icq = pd.Series([0.05, 0.03, -0.01, -0.02, -0.03, -0.01])  # 连续4季<0
    out = DC.decay_check(strong, ic_quarterly=icq)
    assert out["decayed"] is True and any("Rank IC" in r for r in out["reasons"])


# ---------------- #4 alpha/overlay 分账 ----------------
from governance import alpha_overlay as AO


def _ret(mu, sd, n, seed):
    idx = pd.date_range("2018-01-01", periods=n, freq="B")
    return pd.Series(np.random.default_rng(seed).normal(mu, sd, n), index=idx)


def test_split_flags_overlay_manufacturing():
    # 裸因子≈0(负漂移),完整(加 overlay)强正 → 判 overlay 造假
    bare = _ret(-0.0003, 0.012, 800, 10)   # 夏普<0.3
    full = _ret(0.0015, 0.008, 800, 11)    # 夏普>0.8
    out = AO.split_alpha_overlay(bare, full)
    assert out["overlay_manufactures_alpha"] is True
    assert "造假" in out["overlay_contribution"]["role"]


def test_split_real_alpha_legit_overlay():
    # 裸因子自身即真 alpha → overlay 记风控(合法)
    bare = _ret(0.0014, 0.009, 800, 12)    # 夏普>0.8
    full = _ret(0.0016, 0.007, 800, 13)
    out = AO.split_alpha_overlay(bare, full)
    assert out["bare_is_real_alpha"] is True
    assert "合法" in out["overlay_contribution"]["role"]
    assert out["overlay_manufactures_alpha"] is False


def test_split_separates_accounting():
    bare = _ret(0.0014, 0.009, 800, 14)
    full = _ret(0.0016, 0.007, 800, 15)
    out = AO.split_alpha_overlay(bare, full)
    # bare_alpha 与 overlay_contribution 是两本账,不混
    assert "sharpe" in out["bare_alpha"] and "sharpe_delta" in out["overlay_contribution"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
