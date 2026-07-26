"""Alpha Audit 测试:合成面板下判决正确(已知真相)。

Run:
    cd factor_research && python3 tests/test_alpha_audit.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research_toolkit import Verdict, audit_factor, newey_west_icir  # noqa: E402


def _zrow(df):
    return df.sub(df.mean(axis=1), axis=0).div(df.std(axis=1) + 1e-9, axis=0)


def _synthetic(T=400, N=60, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=T)
    cols = [f"{600000+i:06d}" for i in range(N)]
    s1 = pd.DataFrame(rng.normal(0, 1, (T, N)), index=idx, columns=cols)
    s2 = pd.DataFrame(rng.normal(0, 1, (T, N)), index=idx, columns=cols)
    noise = pd.DataFrame(rng.normal(0, 1, (T, N)), index=idx, columns=cols)
    # 前向收益由 s1 与 s2 共同(正交)驱动 + 噪声
    fwd = 0.4 * _zrow(s1) + 0.4 * _zrow(s2) + 0.7 * noise
    return s1, s2, noise, fwd


def test_newey_west_corrects_autocorrelation():
    rng = np.random.default_rng(7)
    wn = rng.normal(0.05, 0.1, 1500)
    assert abs(newey_west_icir(wn, 20) - abs(wn.mean()) / wn.std()) / (abs(wn.mean()) / wn.std()) < 0.2
    ar = np.zeros(1500); ar[0] = 0.05
    for i in range(1, 1500):
        ar[i] = 0.95 * ar[i - 1] + rng.normal(0.0025, 0.03)
    assert newey_west_icir(ar, 20) < abs(ar.mean()) / ar.std() * 0.5
    print("✅ NW 校正:白噪声≈raw,强自相关显著压低")


def test_audit_orthogonal_predictor_is_real():
    s1, s2, _, fwd = _synthetic()
    rep = audit_factor(s2, fwd, {"s1": s1}, candidate_id="orthogonal", horizon=20, n_perm=4)
    # s2 与 s1 正交且都预测收益 → 对 base={s1} 有真增量
    assert rep.verdict == Verdict.REAL, (rep.verdict, rep.true_increment)
    assert rep.true_increment > 0.015
    print(f"✅ 正交预测因子判 REAL(真增量 {rep.true_increment:+.3f})")


def test_audit_redundant_clone_is_noise():
    s1, _, _, fwd = _synthetic()
    rep = audit_factor(s1.copy(), fwd, {"s1": s1}, candidate_id="clone", horizon=20, n_perm=4)
    # 候选 = base 中已有因子的克隆 → 无增量 → NOISE
    assert rep.verdict == Verdict.NOISE, (rep.verdict, rep.true_increment)
    print(f"✅ 在册因子克隆判 NOISE(真增量 {rep.true_increment:+.3f})")


def test_audit_pure_noise_is_noise():
    s1, _, _, fwd = _synthetic()
    # 与 fwd 完全无关的独立噪声(不是 fwd 的成分)
    indep = pd.DataFrame(np.random.default_rng(999).normal(0, 1, s1.shape),
                         index=s1.index, columns=s1.columns)
    rep = audit_factor(indep, fwd, {"s1": s1}, candidate_id="noise", horizon=20, n_perm=4)
    assert rep.verdict == Verdict.NOISE, (rep.verdict, rep.true_increment)
    assert rep.to_dict()["verdict"] == "noise"  # to_dict 可序列化
    print(f"✅ 纯噪声判 NOISE(真增量 {rep.true_increment:+.3f})")


def test_audit_undecidable_on_tiny_sample():
    s1, s2, _, fwd = _synthetic(T=120)  # 训练样本 < min_train_obs
    rep = audit_factor(s2, fwd, {"s1": s1}, horizon=20)
    assert rep.verdict == Verdict.UNDECIDABLE
    print("✅ 样本不足判 UNDECIDABLE")


if __name__ == "__main__":
    test_newey_west_corrects_autocorrelation()
    test_audit_orthogonal_predictor_is_real()
    test_audit_redundant_clone_is_noise()
    test_audit_pure_noise_is_noise()
    test_audit_undecidable_on_tiny_sample()
    print("\n🎉 Alpha Audit tests passed!")
