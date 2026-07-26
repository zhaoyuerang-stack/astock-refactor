"""Alpha Audit — 因子/策略测谎机(host-agnostic, market-agnostic)。

输入:候选因子日值面板 (T,N) + 前向收益 (T,N) + 已知因子池(M-base)。
输出:四种判决之一 + 真增量 ± 置换带宽 + 诚实绝对量级(NW 校正)。

核心武器(均为"拒绝假 alpha"的机制,不是发现 alpha):
- **NW 重叠校正**:horizon>1 的每日 IC 序列强自相关 → raw ICIR=mean/std 系统性虚高
  (实测 h=20 raw/nw≈3.5x)。NW 用 Bartlett 核长期方差给出诚实绝对量级。
- **RidgeCV 联合增量 + 置换**:单因子 ICIR 高 ≠ 对组合有贡献。真增量 = 表面增量(real)
  − 表面增量(permuted),置换保留 NaN 模式销毁预测力,隔离结构/冗余假增量。
- **四种判决**:把不同种类的"零"分开——REAL / NOISE(price-in) / TRUE_BUT_SMALL /
  UNDECIDABLE,每种对应不同应对。

纯函数:不加载数据、不碰 data_lake/factors,任何市场任何因子库都能接。
机制 port 自姊妹系统(自進化因子挖掘系統)的防自欺武器库,结论一律本地重算。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd
from scipy import stats


class Verdict(Enum):
    REAL = "real"                  # 真增量 ≥ 经济阈值且统计显著 → 新 alpha
    TRUE_BUT_SMALL = "true_but_small"  # 统计显著非零但低于经济阈值 → 真但无投资价值
    NOISE = "noise"                # 与置换不可区分 → 噪声/price-in
    UNDECIDABLE = "undecidable"    # 样本不足,统计模型无法判定


# ── NW 重叠校正 ──────────────────────────────────────────────────────────
def newey_west_icir(daily_ic, max_lag: int | None = None) -> float:
    """Bartlett 核长期方差校正的 ICIR。max_lag 按 IC 序列自相关长度(≈horizon)设。"""
    ic = np.asarray(daily_ic, dtype=float)
    ic = ic[~np.isnan(ic)]
    n = len(ic)
    if n < 2:
        return float("nan")
    if max_lag is None:
        max_lag = int(n ** 0.25)
    max_lag = max(1, min(max_lag, n - 1))
    mean, var = ic.mean(), ic.var()
    lr_var = var
    for lag in range(1, max_lag + 1):
        w = 1.0 - lag / (max_lag + 1)
        ac = np.corrcoef(ic[:-lag], ic[lag:])[0, 1]
        if not np.isnan(ac):
            lr_var += 2 * w * ac * var
    return abs(mean) / np.sqrt(max(lr_var, 1e-12))


def _daily_ic(fv: np.ndarray, fwd: np.ndarray, min_n: int, warmup: int = 60) -> np.ndarray:
    out = []
    for t in range(warmup, fv.shape[0]):
        v = ~np.isnan(fv[t]) & ~np.isnan(fwd[t])
        if v.sum() < min_n:
            continue
        ic, _ = stats.spearmanr(fv[t][v], fwd[t][v])
        if not np.isnan(ic):
            out.append(ic)
    return np.asarray(out)


def corrected_icir(candidate: pd.DataFrame, forward_returns: pd.DataFrame,
                   *, horizon: int = 20, min_cross_section: int = 30) -> dict:
    """raw / nonoverlap / nw 三口径一站式(无默认键:调用方必须自选口径并留理由)。"""
    fv = candidate.to_numpy(dtype=float)
    fwd = forward_returns.reindex(index=candidate.index, columns=candidate.columns).to_numpy(dtype=float)
    daily = _daily_ic(fv, fwd, min_cross_section)
    raw = abs(daily.mean()) / daily.std() if len(daily) > 1 and daily.std() > 0 else 0.0
    # 非重叠:每 horizon 步一个独立截面
    no = []
    for t in range(60, fv.shape[0] - horizon, horizon):
        v = ~np.isnan(fv[t]) & ~np.isnan(fwd[t])
        if v.sum() >= min_cross_section:
            c, _ = stats.spearmanr(fv[t][v], fwd[t][v])
            if not np.isnan(c):
                no.append(c)
    no = np.asarray(no)
    return {
        "raw_icir": round(float(raw), 4),
        "nonoverlap_icir": round(float(abs(no.mean()) / no.std()) if len(no) > 1 and no.std() > 0 else 0.0, 4),
        "nw_icir": round(float(newey_west_icir(daily, max_lag=horizon)), 4),
        "n_daily": len(daily), "n_nonoverlap": len(no),
    }


# ── RidgeCV 联合增量 + 置换 ────────────────────────────────────────────────
def _rank_icir(signal: np.ndarray, labels: np.ndarray, mask: np.ndarray, min_n: int) -> float:
    ics = []
    for t in np.where(mask)[0]:
        v = ~np.isnan(signal[t]) & ~np.isnan(labels[t])
        if v.sum() < min_n:
            continue
        c, _ = stats.spearmanr(signal[t][v], labels[t][v])
        if not np.isnan(c):
            ics.append(c)
    ics = np.asarray(ics)
    return abs(ics.mean()) / ics.std() if len(ics) > 1 and ics.std() > 0 else 0.0


def _ridge_signal(features: list[np.ndarray], labels: np.ndarray, train: np.ndarray):
    from sklearn.linear_model import RidgeCV  # lazy:保持 package 不强依赖 sklearn

    T, N = labels.shape
    X = np.stack([p.reshape(-1) for p in features], axis=1)
    y = labels.reshape(-1)
    tmask = np.repeat(train, N)
    ok = tmask & ~np.isnan(y) & ~np.isnan(X).any(axis=1)
    if ok.sum() < 100:
        return np.full((T, N), np.nan)
    m = RidgeCV(alphas=[0.1, 1.0, 10.0, 100.0]).fit(X[ok], y[ok])
    pred = m.predict(np.nan_to_num(X, nan=0.0)).reshape(T, N)
    pred[np.isnan(np.stack(features, axis=0)).any(axis=0)] = np.nan
    return pred


def ridge_joint_increment(candidate: np.ndarray, base: list[np.ndarray], labels: np.ndarray,
                          train: np.ndarray, test: np.ndarray, *, n_perm: int = 5,
                          min_n: int = 10, seed0: int = 11) -> dict:
    """真增量 = 表面增量(real) − 表面增量(permuted);返回置换分布供置信带宽。"""
    m_base = _rank_icir(_ridge_signal(base, labels, train), labels, test, min_n)
    m_full = _rank_icir(_ridge_signal(base + [candidate], labels, train), labels, test, min_n)
    real_inc = m_full - m_base
    perm = []
    for s in range(n_perm):
        rng = np.random.default_rng(seed0 + s)
        cp = candidate.copy()
        for t in range(cp.shape[0]):  # 保留 NaN 模式,逐日打乱非 NaN 值
            idx = np.where(~np.isnan(cp[t]))[0]
            if len(idx) > 1:
                cp[t, idx] = cp[t, rng.permutation(idx)]
        perm.append(_rank_icir(_ridge_signal(base + [cp], labels, train), labels, test, min_n) - m_base)
    perm = np.asarray(perm)
    return {"m_base": round(float(m_base), 4), "m_full": round(float(m_full), 4),
            "surface_inc": round(float(real_inc), 4), "perm_inc": round(float(perm.mean()), 4),
            "true_inc": round(float(real_inc - perm.mean()), 4),
            "perm_std": round(float(perm.std()), 4)}


# ── 审计报告 ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class AuditReport:
    candidate_id: str
    verdict: Verdict
    true_increment: float        # 真增量 = real − permuted(核心判据)
    surface_increment: float     # 表面增量(M-full − M-base);与 true 偏离>0.01 → 结构/NaN 污染
    permuted_increment: float
    nw_icir: float               # NW 校正 ICIR(诚实绝对量级)
    raw_icir: float              # 原始 ICIR(重叠虚高,仅参考)
    n_samples: int
    real_threshold: float = 0.015
    notes: tuple = ()

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "verdict": self.verdict.value,
            "true_increment": self.true_increment,
            "surface_increment": self.surface_increment,
            "permuted_increment": self.permuted_increment,
            "nw_icir": self.nw_icir,
            "raw_icir": self.raw_icir,
            "n_samples": self.n_samples,
            "real_threshold": self.real_threshold,
            "notes": list(self.notes),
        }


def audit_factor(
    candidate: pd.DataFrame,
    forward_returns: pd.DataFrame,
    base_panels: dict[str, pd.DataFrame],
    *,
    candidate_id: str = "candidate",
    horizon: int = 20,
    train_frac: float = 0.70,
    real_threshold: float = 0.015,
    n_perm: int = 5,
    min_cross_section: int = 30,
    min_train_obs: int = 200,
) -> AuditReport:
    """审一个候选因子:对已知池(base_panels)的诚实边际增量 + 四种判决。

    candidate / forward_returns / base_panels 各项都是同 index/columns 的 (T,N) 面板;
    candidate 已应用 direction(高=做多)。本函数不加载数据,任何市场任何因子库可接。
    """
    idx, cols = candidate.index, candidate.columns
    fwd = forward_returns.reindex(index=idx, columns=cols)
    ic = corrected_icir(candidate, fwd, horizon=horizon, min_cross_section=min_cross_section)

    cand_np = candidate.to_numpy(dtype=float)
    base_np = [p.reindex(index=idx, columns=cols).to_numpy(dtype=float) for p in base_panels.values()]
    fwd_np = fwd.to_numpy(dtype=float)
    split = int(len(idx) * train_frac)
    train = np.zeros(len(idx), bool); train[:split] = True
    test = ~train

    notes = []
    if split < min_train_obs or ic["n_daily"] < min_cross_section:
        return AuditReport(candidate_id, Verdict.UNDECIDABLE, 0.0, 0.0, 0.0,
                           ic["nw_icir"], ic["raw_icir"], ic["n_daily"], real_threshold,
                           ("训练样本/IC 样本不足,无法判定",))

    inc = ridge_joint_increment(cand_np, base_np, fwd_np, train, test,
                                n_perm=n_perm, min_n=10)
    true_inc, perm_std = inc["true_inc"], inc["perm_std"]
    # 判决:置换分布做显著性带宽,经济阈值做大小判定
    z = true_inc / (perm_std + 1e-9)
    if abs(inc["surface_inc"] - true_inc) > 0.01:
        notes.append("表面增量与真增量偏离>0.01:NaN 模式/口径差污染")
    if true_inc >= real_threshold and z >= 2.0:
        verdict = Verdict.REAL
    elif z >= 2.0:  # 统计显著但 < 经济阈值
        verdict = Verdict.TRUE_BUT_SMALL
    else:
        verdict = Verdict.NOISE

    return AuditReport(
        candidate_id=candidate_id, verdict=verdict,
        true_increment=true_inc, surface_increment=inc["surface_inc"],
        permuted_increment=inc["perm_inc"], nw_icir=ic["nw_icir"], raw_icir=ic["raw_icir"],
        n_samples=ic["n_daily"], real_threshold=real_threshold, notes=tuple(notes),
    )
