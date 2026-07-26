"""WS2 对抗测试:组合配置发现层(portfolio/composite_search)。

护栏 C 覆盖:
① 金库真不参与择优(§5.2)——注入腿故意在 2025+ 段塞爆炸收益:含金库输入与
   预截断输入的输出必须逐位一致;若实现漏截,金库段会改变 Δsharpe 排序,此测试必红;
② 洗白防线——默认腿源锁死 run_active()(已验真在册腿),台账外腿进不来;
③ 记账真生效(§5.1)——len(configs) 必须落 trial 账本(注入 tmp,绝不碰真账本);
④ 小盘 reload 真被拒——与小盘参考腿 0.99 相关的配置必须标 WARN 且不得 SHADOW 推荐;
⑤ 负边际不推荐;⑥ 确定性;⑦ regime_adaptive 真接线(WS6 regime 路由落点)。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from portfolio.composite_search import search_composite_allocations  # noqa: E402


def _legs(n_days=1000, seed=2, end_2025=False):
    """合成在册腿:三条低相关腿;end_2025=True 时延到 2025-06(跨 holdout 金库)。"""
    rng = np.random.default_rng(seed)
    periods = n_days + (120 if end_2025 else 0)
    idx = pd.bdate_range("2020-01-02", periods=periods)
    legs = {
        "small_illiq": pd.Series(rng.normal(0.0008, 0.012, periods), index=idx),
        "large_mom": pd.Series(rng.normal(0.0004, 0.009, periods), index=idx),
        "bond_trend": pd.Series(rng.normal(0.0002, 0.003, periods), index=idx),
    }
    return legs


def test_holdout_never_participates_in_selection(tmp_path):
    """§5.2 对抗:一条腿在 2025+ 金库段塞入爆炸正收益(日均 +2%)。
    若实现漏截,该腿相关配置的 Δsharpe 会被金库段拉爆、排序翻转;
    正确实现下,含金库输入与预截断输入的输出必须逐位一致。"""
    legs_full = _legs(end_2025=True)
    boost = legs_full["large_mom"].copy()
    boost[boost.index >= "2025-01-01"] = 0.02  # 金库段爆炸(诱饵)
    legs_full["large_mom"] = boost
    legs_clipped = {k: v[v.index < "2025-01-01"] for k, v in legs_full.items()}

    out_full = search_composite_allocations(legs_full, ledger_path=tmp_path / "a.jsonl")
    out_clip = search_composite_allocations(legs_clipped, ledger_path=tmp_path / "b.jsonl")
    assert out_full["configs"] == out_clip["configs"], "金库段改变了择优结果 = holdout 泄露"
    assert out_full["baseline"]["sharpe"] == out_clip["baseline"]["sharpe"]


def test_default_leg_source_is_run_active(monkeypatch):
    """洗白防线:legs=None 必须从 run_active()(已验真在册腿)取,台账外腿无入口。"""
    called = {}

    def fake_run_active(start="2018-01-01"):
        called["yes"] = True
        return _legs()

    import portfolio.strategy_runners as sr

    monkeypatch.setattr(sr, "run_active", fake_run_active)
    out = search_composite_allocations(None, ledger_path=None)
    assert called.get("yes") is True, "默认腿源未走 run_active = 洗白入口"
    assert out["baseline"] is not None


def test_sweep_width_recorded_to_trial_ledger(tmp_path):
    """§5.1 对抗:best-of-k 配置择优是多重检验,len(configs) 必须进账本。"""
    from governance.trial_ledger import honest_n_trials

    ledger = tmp_path / "trial_ledger.jsonl"
    out = search_composite_allocations(_legs(), ledger_path=ledger)
    n_configs = len(out["configs"])
    assert n_configs >= 5  # 2 全腿方法 + 3 leave-one-out
    assert honest_n_trials("composite_search", path=ledger) == n_configs, \
        "配置扫描未计入 n_trials = 隐性 p-hacking"


def test_smallcap_reload_is_flagged_and_not_recommended(tmp_path):
    """小盘陷阱对抗:参考腿 = small_illiq 自身 → 含它的高相关配置必须标 reload 且不推荐。"""
    legs = _legs()
    ref = legs["small_illiq"]
    out = search_composite_allocations(legs, smallcap_ref=ref, ledger_path=tmp_path / "l.jsonl")
    # leave-one-out 掉 bond+large 之外,任何 corr>0.9 的配置都必须被拦
    reloads = [c for c in out["configs"] if c.get("smallcap_reload")]
    for c in reloads:
        assert c["shadow_recommend"] is False, f"reload 配置被推荐: {c}"
    # 至少"只剩小盘主导"的某配置应被披露(drop bond_trend+large 后组合≈小盘)
    high_corr = [c for c in out["configs"]
                 if c.get("smallcap_corr") is not None and c["smallcap_corr"] > 0.9]
    assert all(c["smallcap_reload"] for c in high_corr)


def test_negative_marginal_not_recommended(tmp_path):
    """负边际配置(Δsharpe<阈值)不得 SHADOW 推荐。"""
    out = search_composite_allocations(_legs(), shadow_min_dsharpe=0.05,
                                       ledger_path=tmp_path / "l.jsonl")
    for c in out["configs"]:
        if c.get("d_sharpe") is not None and c["d_sharpe"] < 0.05:
            assert c["shadow_recommend"] is False


def test_deterministic(tmp_path):
    a = search_composite_allocations(_legs(), ledger_path=tmp_path / "a.jsonl")
    b = search_composite_allocations(_legs(), ledger_path=tmp_path / "b.jsonl")
    assert a["configs"] == b["configs"]


def test_regime_adaptive_wired_when_signal_given(tmp_path):
    """WS6 regime 路由落点:给 regime_signal → regime_adaptive 配置真的被搜并产出指标。"""
    legs = _legs()
    idx = next(iter(legs.values())).index
    sig = pd.Series((np.arange(len(idx)) // 60) % 2, index=idx, dtype=float)  # 交替牛熊
    out = search_composite_allocations(legs, regime_signal=sig, ledger_path=tmp_path / "l.jsonl")
    ra = [c for c in out["configs"] if c["method"] == "regime_adaptive"]
    assert ra and "sharpe" in ra[0] and "error" not in ra[0]
    # 无信号时该方法不得出现(不静默用空信号假装 regime 路由)
    out2 = search_composite_allocations(legs, ledger_path=tmp_path / "l2.jsonl")
    assert not [c for c in out2["configs"] if c["method"] == "regime_adaptive"]


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
