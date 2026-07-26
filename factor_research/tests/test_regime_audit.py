"""WS6 item1 对抗测试:regime 审计层(services/read/regime_audit)。

护栏 C 覆盖:
① 自欺信号真被抓——「压力段反成最佳年」(down 段夏普 > up 段)必须打 WARN 标注,
   反向构造必须不打(不许假阳性刷屏);
② 同日虚假相关真被防——收益只在 vol=high **当日**为正的构造序列,若实现用同日标签
   归因会把全部正收益归入 high 桶(假发现);lagged 口径下必须归入前一日标签的桶。
   用同日口径实现此测试必然失败;
③ 披露诚实——桶样本 < 20 天不产出年化/夏普(insufficient),不给噪声结论;
④ 确定性——同输入两次逐位一致。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.read.regime_audit import (  # noqa: E402
    attribute_returns_by_regime,
    audit_registered_strategies,
    current_regime,
)


def _labels(n=260, seed=3):
    """手工 regime 标签面板(与 RegimeEngine.classify 同列契约,精确可控)。"""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=n)
    lab = pd.DataFrame(index=idx)
    lab["trend"] = np.where(rng.random(n) < 0.5, "up", "down")
    lab["trend_dist"] = rng.normal(0, 0.02, n)
    lab["volatility"] = np.where(rng.random(n) < 0.5, "high", "low")
    lab["vol_value"] = rng.uniform(0.005, 0.03, n)
    lab["liquidity"] = np.where(rng.random(n) < 0.5, "plenty", "dry")
    lab["liq_value"] = rng.uniform(0.5, 1.5, n)
    lab["breadth"] = np.where(rng.random(n) < 0.5, "wide", "narrow")
    lab["breadth_value"] = rng.uniform(0.2, 0.8, n)
    return lab


def test_stress_outperforms_flag_catches_regime_dependence():
    """§7「压力段反成最佳年」:down 段稳定正收益、up 段负 → 必须 WARN。"""
    lab = _labels()
    noise = np.random.default_rng(9).normal(0, 0.001, len(lab))  # 桶内须有方差,夏普才有定义
    ret = pd.Series(np.where(lab["trend"] == "down", 0.004, -0.002) + noise, index=lab.index)
    att = attribute_returns_by_regime(ret, lab)
    assert att["flags"]["stress_outperforms"] is True
    # 反向(正常策略:牛市赚、熊市亏)不得误报
    att2 = attribute_returns_by_regime(-ret, lab)
    assert att2["flags"]["stress_outperforms"] is False
    # 纯噪声(与 regime 无关,真实 A股日收益量级 std≈1%)不得误报——
    # 裸 down>up 比较或拍脑袋固定夏普差都会在此假阳性刷屏(桶间随机夏普差 SD≈2)
    pure_noise = pd.Series(np.random.default_rng(11).normal(0.0005, 0.01, len(lab)), index=lab.index)
    att3 = attribute_returns_by_regime(pure_noise, lab)
    assert att3["flags"]["stress_outperforms"] is False, "纯噪声被标 WARN = 披露信噪失效"
    assert att3["flags"]["stress_z"] is not None  # z 连续值始终披露,供人审视未过线差异


def test_attribution_uses_lagged_labels_not_same_day():
    """同日重叠对抗:收益只在 vol=high 当日为正。同日口径会把全部正收益归 high 桶
    (high 桶年化显著为正、low 桶为负);lagged 口径下收益归前一日标签,两桶应近
    对称。断言 high 桶年化不显著为正 —— 同日口径实现必然在此失败。"""
    rng = np.random.default_rng(7)
    lab = _labels(n=300)
    # vol 标签独立随机 → T 日 high 与 T-1 标签无关
    same_day_signal = np.where(lab["volatility"] == "high", 0.01, -0.01)
    ret = pd.Series(same_day_signal, index=lab.index) + rng.normal(0, 1e-4, len(lab))
    att = attribute_returns_by_regime(ret, lab)
    high = att["dims"]["volatility"]["high"]
    low = att["dims"]["volatility"]["low"]
    assert high["annual"] is not None and low["annual"] is not None
    # lagged 口径:两桶都应远离同日口径的 ±2.5(=0.01×252);若实现偷用同日标签,
    # high≈+2.5 / low≈−2.5,以下断言必炸。
    assert abs(high["annual"]) < 1.0, f"high 桶年化 {high['annual']} ≈ 同日口径,疑似未 lag"
    assert abs(low["annual"]) < 1.0
    # trend 维在 RegimeEngine 内已 lag,本层不得再 shift(否则双重滞后):
    # 构造与 trend 同日对齐的收益,归因应完整呈现(trend 列直接用)。
    ret_trend = pd.Series(np.where(lab["trend"] == "up", 0.01, -0.01), index=lab.index)
    att_t = attribute_returns_by_regime(ret_trend, lab)
    assert att_t["dims"]["trend"]["up"]["annual"] > 2.0  # 直用 trend 列 → 对齐完整


def test_small_buckets_report_insufficient_not_noise():
    lab = _labels(n=60)
    lab["volatility"] = ["high"] * 5 + ["low"] * 55  # high 桶仅 5 天
    ret = pd.Series(0.001, index=lab.index)
    att = attribute_returns_by_regime(ret, lab)
    high = att["dims"]["volatility"]["high"]
    assert high["insufficient"] is True and high["sharpe"] is None


def test_attribution_is_deterministic():
    lab = _labels()
    ret = pd.Series(np.random.default_rng(1).normal(0, 0.01, len(lab)), index=lab.index)
    assert attribute_returns_by_regime(ret, lab) == attribute_returns_by_regime(ret, lab)


def test_current_regime_confidence_bounded_and_labeled():
    lab = _labels()
    cur = current_regime(labels=lab)
    assert set(cur["dims"]) == {"trend", "volatility", "liquidity", "breadth"}
    for d in cur["dims"].values():
        assert d["label"] in {"up", "down", "high", "low", "plenty", "dry", "wide", "narrow"}
        assert d["confidence"] is None or 0.0 <= d["confidence"] <= 1.0


def test_audit_ranks_sunny_strategy_first(tmp_path):
    """端到端(注入 tmp returns_dir):晴天策略(stress_outperforms)必须排在
    全天候策略之前——审计的意义就是把最可疑的顶到最前面。"""
    lab = _labels()
    sunny = pd.Series(
        np.where(lab["trend"] == "down", 0.005, -0.003)
        + np.random.default_rng(11).normal(0, 0.001, len(lab)),
        index=lab.index,
    )
    # 全天候策略:真实量级噪声(std≈1%),桶间夏普差纯随机、过不了 z>2
    steady = pd.Series(np.random.default_rng(12).normal(0.0005, 0.01, len(lab)), index=lab.index)
    pd.DataFrame({"ret": sunny}).to_csv(tmp_path / "sunny-fam__v1.0.csv")
    pd.DataFrame({"ret": steady}).to_csv(tmp_path / "steady-fam__v1.0.csv")
    out = audit_registered_strategies(labels=lab, returns_dir=tmp_path)
    assert [r["version"] for r in out["strategies"]][0] == "sunny-fam/v1.0"
    assert out["strategies"][0]["stress_outperforms"] is True
    assert out["strategies"][1]["stress_outperforms"] is False
    assert "披露层" in out["note"]


def test_regime_engine_contract_smoke():
    """接口兼容烟测:合成 close/amount 走真实 RegimeEngine → 归因不崩、列契约成立。"""
    rng = np.random.default_rng(5)
    idx = pd.bdate_range("2022-01-03", periods=160)
    cols = [f"{i:06d}.SZ" for i in range(30)]
    close = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.02, (160, 30)), axis=0)),
                         index=idx, columns=cols)
    amount = pd.DataFrame(rng.uniform(1e6, 1e8, (160, 30)), index=idx, columns=cols)
    from services.read.regime_audit import load_regime_labels

    lab = load_regime_labels(close, amount)
    assert {"trend", "volatility", "liquidity", "breadth"} <= set(lab.columns)
    ret = pd.Series(rng.normal(0, 0.01, len(lab)), index=lab.index)
    att = attribute_returns_by_regime(ret, lab)
    assert set(att["dims"]) == {"trend", "volatility", "liquidity", "breadth"}


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
