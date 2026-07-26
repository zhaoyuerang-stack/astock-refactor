"""alpha101 搜索白名单不得含机械退化/近重复因子(n_trials 诚实)。

护栏:
  · AST 含 close-close 等恒常子项 → 不得在 ALLOWED_FACTORS
  · 已知退化/短收益簇重复项 → 不得在 ALLOWED_FACTORS 与 DSL _FACTOR_CALLS
  · **持续扫描**:可搜索 alpha 两两截面平均 |秩相关| 不得 ≥ 0.98(虚增 n_trials)
  · 实现可保留在 alpha101.py 供对照,但不可再被工厂搜索
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factors import alpha101
from factors.autoresearch_dsl import _FACTOR_CALLS
from factory.autoresearch.registry import ALLOWED_FACTORS

# 机械扫描 + 数值近重复审计后移出搜索宇宙的因子
_BANNED_SEARCHABLE = frozenset({
    "alpha_005",   # close-close 常数子项 → price_to_ma 同信息
    "alpha_020",   # 与 alpha_009 短收益簇 |秩相关|≈1
    "alpha_022",
    "alpha_024",
    "alpha_033",
    "alpha_049",   # alpha_024 逐字双胞胎
})

# 截面平均 |秩相关| 超过此阈值 = 同一信息换皮,不得同时 searchable
_NEAR_DUP_RANK_CORR = 0.98


def test_banned_alphas_not_in_search_whitelist():
    present = sorted(_BANNED_SEARCHABLE & set(ALLOWED_FACTORS))
    assert not present, f"退化/近重复 alpha 仍在 ALLOWED_FACTORS: {present}"


def test_banned_alphas_not_in_dsl_calls():
    present = sorted(_BANNED_SEARCHABLE & set(_FACTOR_CALLS))
    assert not present, f"退化/近重复 alpha 仍在 DSL _FACTOR_CALLS: {present}"


def test_no_close_minus_close_in_searchable_alphas():
    """可搜索的 alpha_* 源码不得含 close-close 恒常子项。"""
    src = inspect.getsource(alpha101)
    tree = ast.parse(src)
    offenders = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("alpha_"):
            continue
        if node.name not in ALLOWED_FACTORS:
            continue
        for n in ast.walk(node):
            if (
                isinstance(n, ast.BinOp)
                and isinstance(n.op, ast.Sub)
                and isinstance(n.left, ast.Name)
                and isinstance(n.right, ast.Name)
                and n.left.id == n.right.id
            ):
                offenders.append(f"{node.name}: {n.left.id}-{n.right.id}")
    assert not offenders, f"可搜索 alpha 含恒常自减子项: {offenders}"


def test_alpha_005_still_exists_for_audit():
    """实现保留(R-ARCH-005:废弃可留源,但不可作新搜索入口)。"""
    assert hasattr(alpha101, "alpha_005")
    assert "alpha_005" not in ALLOWED_FACTORS


def _synthetic_panels(n_dates: int = 260, n_codes: int = 50, seed: int = 0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2018-01-01", periods=n_dates)
    codes = [f"{i:06d}.SZ" for i in range(n_codes)]
    rets = rng.normal(0.001, 0.02, size=(n_dates, n_codes))
    close = pd.DataFrame(rets.cumsum(axis=0) + 20.0, index=dates, columns=codes)
    volume = pd.DataFrame(
        rng.uniform(1e5, 1e6, size=(n_dates, n_codes)),
        index=dates,
        columns=codes,
    )
    return close, volume


def _mean_abs_rank_corr(a: pd.DataFrame, b: pd.DataFrame, min_start: int = 40) -> float | None:
    corrs: list[float] = []
    for t in range(min_start, len(a)):
        x, y = a.iloc[t], b.iloc[t]
        m = x.notna() & y.notna()
        if int(m.sum()) < 15:
            continue
        rx, ry = x[m].rank(), y[m].rank()
        if float(rx.std()) < 1e-12 or float(ry.std()) < 1e-12:
            continue
        c = rx.corr(ry)
        if c == c:  # not NaN
            corrs.append(float(c))
    if not corrs:
        return None
    return float(np.nanmean(np.abs(corrs)))


def test_searchable_alphas_pairwise_rank_corr_below_near_dup():
    """持续对抗:白名单内任意两 alpha 不得近重复(|秩相关|≥0.98)。

    固定合成面板 + 固定 seed → 确定性;阈值 0.98 对应「同一信息换皮虚增 n_trials」。
    新增 alpha 进白名单若与已有成员撞车,本测试红。
    """
    close, volume = _synthetic_panels()
    names = sorted(k for k in ALLOWED_FACTORS if k.startswith("alpha_"))
    panels: dict[str, pd.DataFrame] = {}
    for name in names:
        fn = getattr(alpha101, name, None)
        if fn is None:
            continue
        try:
            panel = fn(close, volume)
        except Exception:
            continue
        if isinstance(panel, pd.DataFrame) and panel.notna().to_numpy().any():
            panels[name] = panel

    assert len(panels) >= 10, f"可计算 alpha 过少,扫描失效: {len(panels)}"

    near_dups: list[str] = []
    keys = list(panels)
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            mac = _mean_abs_rank_corr(panels[a], panels[b])
            if mac is not None and mac >= _NEAR_DUP_RANK_CORR:
                near_dups.append(f"{a}~{b}: |ρ|={mac:.4f}")

    assert not near_dups, (
        "可搜索 alpha 近重复(虚增 n_trials),请移出 ALLOWED_FACTORS/DSL:\n  "
        + "\n  ".join(near_dups)
    )


def test_banned_cluster_still_near_dup_when_computed():
    """负向对照:已移出的短收益簇在数值上仍应 |ρ|≥0.98(证明扫描阈值有牙)。"""
    close, volume = _synthetic_panels()
    a = alpha101.alpha_024(close, volume)
    b = alpha101.alpha_049(close, volume)
    mac = _mean_abs_rank_corr(a, b)
    assert mac is not None and mac >= _NEAR_DUP_RANK_CORR, (
        f"alpha_024~049 应近重复,实际 |ρ|={mac}"
    )


if __name__ == "__main__":
    test_banned_alphas_not_in_search_whitelist()
    test_banned_alphas_not_in_dsl_calls()
    test_no_close_minus_close_in_searchable_alphas()
    test_alpha_005_still_exists_for_audit()
    test_searchable_alphas_pairwise_rank_corr_below_near_dup()
    test_banned_cluster_still_near_dup_when_computed()
    print("ok")
