"""对抗性测试:周度组合再构成(portfolio/recompose.py)+ 收件箱 recompose 源。

Run:  cd factor_research && python3 tests/test_portfolio_recompose.py

护栏 C 关注点(不只 happy-path):
  排名不是单一收益最大化(R-OBJECTIVE-001):全样本夏普最高但近三年衰减的腿必须
  垫底且被提案排除,健康的低夏普腿反而入选——单看夏普的实现必挂本测试;
  冗余真拒:与已选腿 |corr|≥0.7 的同质变体必须被跳过并留痕(redundant_with);
  样本不足诚实拒判(不做绩效结论,非降级);
  全灭 → 诚实空提案(不硬凑组合);
  确定性:同输入恒同输出(R-PROD-001 排名可复现的前提);
  收件箱:新鲜提案 info 级不计入待裁决数;过期提案不入箱(比没提案更误导);
  源读取爆炸 → source_error 显式入箱。
全程合成收益序列,不读 data_lake/registry。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contracts.views import DataQualityView, PromotionReadinessView, SystemTruthView
from portfolio.recompose import RANKING_VERSION, propose_weights, rank_strategies, recompose
from services.read.decision_inbox import get_decision_inbox

_IDX = pd.bdate_range("2019-01-04", periods=1600)
_CHINA_TZ = ZoneInfo("Asia/Shanghai")


def _series(mean: float, std: float, seed: int, idx=_IDX) -> pd.Series:
    rng = np.random.RandomState(seed)
    return pd.Series(mean + std * rng.randn(len(idx)), index=idx)


def _healthy(seed: int, mean=0.0012, std=0.008) -> pd.Series:
    return _series(mean, std, seed)


def _decayed_but_high_overall(seed: int) -> pd.Series:
    """前半段暴利、近三年持续亏——全样本夏普仍高,但 rolling-3y 衰减(decay_check 必触发)。"""
    rng = np.random.RandomState(seed)
    early = 0.004 + 0.008 * rng.randn(800)
    late = -0.001 + 0.008 * rng.randn(len(_IDX) - 800)
    return pd.Series(np.concatenate([early, late]), index=_IDX)


# ── 排名:多目标而非单一收益(R-OBJECTIVE-001) ────────────────────────────────

def test_decayed_high_sharpe_leg_demoted_and_excluded():
    returns = {
        "zombie.v1": _decayed_but_high_overall(1),
        "steady.v1": _healthy(2),
        "steady2.v1": _series(0.0009, 0.009, 3),
    }
    ranked = rank_strategies(returns)
    zombie = next(e for e in ranked if e["name"] == "zombie.v1")
    # 前置自检:zombie 全样本夏普确实不低(否则本测试失去"高收益也得死"的意义)
    assert zombie["sharpe"] > 0.5, f"构造失败:zombie 全样本夏普 {zombie['sharpe']} 不够高"
    assert zombie["decayed"] and zombie["tier"] == 1, "近三年衰减的腿必须强制垫底(tier 1)"
    assert all(e["rank"] < zombie["rank"] for e in ranked if e["tier"] == 0), \
        "衰减腿排名必须在全部健康腿之后——单看全样本夏普的排名实现必挂这里"

    prop = propose_weights(ranked, returns, top_n=3)
    assert "zombie.v1" not in prop["weights"], "衰减腿不得进提案"
    assert "steady.v1" in prop["weights"], "健康腿必须入选"


def test_redundant_twin_skipped_with_provenance():
    base = _healthy(4)
    returns = {
        "orig.v1": base,
        "clone.v1": base + _series(0.0, 0.0005, 5),  # 同质变体(corr≈0.99)
        "other.v1": _series(0.0008, 0.012, 6),
    }
    ranked = rank_strategies(returns)
    prop = propose_weights(ranked, returns, top_n=3)
    picked = set(prop["weights"])
    assert len(picked & {"orig.v1", "clone.v1"}) == 1, "同质双胞胎只能入选一条(§5.3 冗余非分散)"
    assert prop["skipped"], "被跳过的冗余腿必须留痕"
    sk = prop["skipped"][0]
    assert sk["redundant_with"] in {"orig.v1", "clone.v1"} and abs(sk["corr"]) >= 0.7
    assert "other.v1" in picked


def test_insufficient_sample_honest_refusal():
    returns = {
        "steady.v1": _healthy(7),
        "newborn.v1": _series(0.002, 0.008, 8, idx=pd.bdate_range("2026-01-01", periods=100)),
    }
    ranked = rank_strategies(returns)
    nb = next(e for e in ranked if e["name"] == "newborn.v1")
    assert nb["tier"] == 2 and "不做绩效结论" in nb["reason"], "样本不足必须诚实拒判,不是打低分"
    assert "sharpe" not in nb, "拒判的腿不得输出绩效数字(半截结论比没有更危险)"
    prop = propose_weights(ranked, returns, top_n=3)
    assert "newborn.v1" not in prop["weights"]


def test_all_dead_gives_honest_empty_proposal():
    returns = {"z1.v1": _decayed_but_high_overall(9), "z2.v1": _decayed_but_high_overall(10)}
    out = recompose(returns)
    assert out["proposal"]["status"] == "no_eligible_legs"
    assert out["proposal"]["weights"] == {} and out["paper_candidates"] == []
    assert "不硬凑" in out["proposal"]["note"]


def test_deterministic_and_weights_sum_to_one():
    returns = {"a.v1": _healthy(11), "b.v1": _series(0.0009, 0.012, 12),
               "c.v1": _series(0.0007, 0.015, 13)}
    out1, out2 = recompose(returns), recompose(returns)
    assert json.dumps(out1, sort_keys=True, default=float) == \
           json.dumps(out2, sort_keys=True, default=float), "同输入必须恒同输出(排名可复现)"
    w = out1["proposal"]["weights"]
    # artifact 权重四舍五入到 4 位小数(人读提案),容差取舍入量级而非浮点精度
    assert abs(sum(w.values()) - 1.0) < 1e-3
    assert out1["ranking_version"] == RANKING_VERSION
    assert out1["proposal"]["composite_metrics"]["sharpe"] is not None
    assert "decayed" in out1["proposal"]["composite_decay"], "组合自身必须过 decay_check(§5.4)"
    assert [c["name"] for c in out1["paper_candidates"]] == list(w.keys()), \
        "paper 名单=提案入选腿(R-PROD-001)"


# ── 收件箱 recompose 源 ──────────────────────────────────────────────────────

def _artifact(age_days: int = 0) -> dict:
    return {
        "generated_at": (datetime.now(_CHINA_TZ) - timedelta(days=age_days)).isoformat(timespec="seconds"),
        "ranking_version": RANKING_VERSION,
        "paper_candidates": ["a.v1", "b.v1"],
        "proposal": {"status": "ok", "weights": {"a.v1": 0.6, "b.v1": 0.4},
                     "composite_metrics": {"sharpe": 1.1, "maxdd": -0.12},
                     "composite_decay": {"decayed": False}},
    }


def _inbox_kwargs(**overrides):
    base = dict(
        gate_verdicts=[], system_truth=SystemTruthView(declared_present=False),
        review_pending=[], decay=None,
        data_quality_view=DataQualityView(total=100, clean=100, clean_ratio=1.0,
                                          verdict="可用", severe_count=0),
        promotion=PromotionReadinessView(), exhaustion={"state": "healthy"},
        recompose=None,
    )
    base.update(overrides)
    return base


def test_inbox_fresh_proposal_is_info_not_pending():
    v = get_decision_inbox(**_inbox_kwargs(recompose=_artifact(age_days=2)))
    item = next(i for i in v.items if i.kind == "portfolio_recompose")
    assert item.severity == "info", "常设建议必须 info 级,不制造假紧迫"
    assert v.pending_count == 0 and "无需你介入" in v.headline
    assert any("R-OBJECTIVE-001" in e for e in item.evidence)


def test_inbox_stale_or_empty_proposal_creates_no_item():
    for rec in (_artifact(age_days=15), None,
                {"generated_at": "not-a-date", "proposal": {"status": "ok", "weights": {"x": 1.0}}},
                {**_artifact(), "proposal": {"status": "no_eligible_legs", "weights": {}}}):
        v = get_decision_inbox(**_inbox_kwargs(recompose=rec))
        assert not [i for i in v.items if i.kind == "portfolio_recompose"], \
            f"过期/缺失/坏时间戳/空提案不得入箱: {rec and str(rec)[:60]}"


def test_inbox_recompose_source_explosion_surfaces():
    class _Boom(dict):
        def get(self, *a, **k):  # noqa: D102
            raise RuntimeError("recompose source exploded")

    v = get_decision_inbox(**_inbox_kwargs(recompose=_Boom()))
    assert [i for i in v.items if i.kind == "source_error" and "portfolio_recompose" in i.key]
    assert not v.all_sources_readable


def _run_all():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
        except AssertionError as e:
            failed += 1
            print(f"  ❌ {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)


# ── v2 防守帽(ADR-036):inverse-vol 跨资产失效修复的对抗测试 ──────────
def test_defensive_cap_prevents_bond_domination():
    """波动悬殊池(债 vol~2.5% + 股 vol~20%):v1 的 inverse-vol 会给债 ~89%
    (v0.2 探针实证 87%),v2 必须压到 DEFENSIVE_CAP——旧实现本测试必挂。"""
    import numpy as np
    import pandas as pd

    from portfolio.recompose import DEFENSIVE_CAP, recompose
    rng = np.random.default_rng(9)
    idx = pd.bdate_range("2020-01-01", periods=700)
    rets = {
        "bond/bh":  pd.Series(rng.normal(0.00012, 0.0016, 700), index=idx),  # ~2.5% 年波动
        "stock-a/v1": pd.Series(rng.normal(0.0008, 0.0125, 700), index=idx),
        "stock-b/v1": pd.Series(rng.normal(0.0006, 0.0125, 700), index=idx),
    }
    w = recompose(rets, top_n=3)["proposal"]["weights"]
    assert "bond/bh" in w, "防守腿应入选(低相关)"
    assert w["bond/bh"] <= DEFENSIVE_CAP + 1e-9, \
        f"防守腿权重 {w['bond/bh']:.1%} 超帽 {DEFENSIVE_CAP:.0%}(v1 行为 = 债基化)"
    assert abs(sum(w.values()) - 1.0) < 1e-6, "权重必须仍归一"


def test_all_equity_pool_unaffected_by_cap():
    """全股票池(无防守腿):v2 行为必须与 v1 完全一致(帽不触发)。"""
    import numpy as np
    import pandas as pd

    from portfolio.recompose import recompose
    rng = np.random.default_rng(11)
    idx = pd.bdate_range("2020-01-01", periods=700)
    rets = {f"s{i}/v1": pd.Series(rng.normal(0.0006, 0.010 + 0.003 * i, 700), index=idx)
            for i in range(3)}
    w = recompose(rets, top_n=3)["proposal"]["weights"]
    assert abs(sum(w.values()) - 1.0) < 1e-6
    assert max(w.values()) < 0.6, "同类资产 inverse-vol 不应出现极端权重"


def test_all_defensive_pool_no_cap_deadlock():
    """全防守池(无非防守腿接收释放权重):帽不适用,正常归一不死锁。"""
    import numpy as np
    import pandas as pd

    from portfolio.recompose import recompose
    rng = np.random.default_rng(13)
    idx = pd.bdate_range("2020-01-01", periods=700)
    rets = {f"b{i}/bh": pd.Series(rng.normal(0.0001, 0.0015 + 0.0004 * i, 700), index=idx)
            for i in range(2)}
    w = recompose(rets, top_n=2)["proposal"]["weights"]
    assert abs(sum(w.values()) - 1.0) < 1e-6


# ── 守卫审计 #5:version_returns provenance 选择路径 fail-closed ──────────

def test_mixed_provenance_excludes_no_stamp_and_passes_tier():
    """三腿混合(spec / config-only / 无戳):无戳排除并如实列出,tier 透传,RANKING_VERSION 不变。"""
    from portfolio.recompose import RANKING_VERSION, recompose

    # 纯 recompose 层:returns 只含已通过校验的腿;excluded 由编排层填入
    returns = {
        "spec-leg.v1": _healthy(21),
        "cfg-leg.v1": _healthy(22),
        # no-stamp 不进 returns
    }
    tiers = {"spec-leg.v1": "spec", "cfg-leg.v1": "config-only"}
    excluded = [{"leg": "orphan.v1", "reason": "missing_provenance_sidecar: orphan__v1.provenance.json"}]
    out = recompose(returns, identity_tiers=tiers, excluded_no_provenance=excluded)

    assert out["ranking_version"] == RANKING_VERSION, "排名口径本身不得因 provenance 改动"
    names_in_legs = {e["name"] for e in out["legs"]}
    assert "orphan.v1" not in names_in_legs
    assert "spec-leg.v1" in names_in_legs and "cfg-leg.v1" in names_in_legs
    by_name = {e["name"]: e for e in out["legs"]}
    assert by_name["spec-leg.v1"]["identity_tier"] == "spec"
    assert by_name["cfg-leg.v1"]["identity_tier"] == "config-only"
    assert out["excluded_no_provenance"] == excluded
    # paper_candidates 条目透传 tier
    for c in out["paper_candidates"]:
        assert "identity_tier" in c and c["name"] in returns
        assert c["identity_tier"] == tiers[c["name"]]


def test_all_no_provenance_gives_empty_rank_with_full_exclusion_list():
    """全无戳 → 空排名 + 全员列出(不硬凑)。"""
    excluded = [
        {"leg": "a.v1", "reason": "missing_provenance_sidecar: a__v1.provenance.json"},
        {"leg": "b.v1", "reason": "series_hash_mismatch: ..."},
    ]
    out = recompose({}, excluded_no_provenance=excluded)
    assert out["legs"] == []
    assert out["paper_candidates"] == []
    assert out["proposal"]["status"] == "no_eligible_legs"
    assert out["excluded_no_provenance"] == excluded
    assert out["ranking_version"] == RANKING_VERSION


def test_load_registered_returns_mixed_provenance_hermetic(tmp_path, monkeypatch):
    """编排层 load_registered_returns:三腿混合 → 无戳排除、tier 正确(hermetic root)。"""
    import strategy_registry
    from lake.version_returns import write_version_returns
    from scripts.ops import scheduled_portfolio_recompose as spr

    # 合成台账:三腿均 status=在册
    reg = {
        "families": [{
            "id": "mix-fam",
            "versions": [
                {"version": "spec-v", "status": "在册"},
                {"version": "cfg-v", "status": "在册"},
                {"version": "bare-v", "status": "在册"},
            ],
        }]
    }
    reg_fp = tmp_path / "strategy_versions.json"
    reg_fp.write_text(json.dumps(reg), encoding="utf-8")
    monkeypatch.setattr(strategy_registry, "REGISTRY", reg_fp)

    write_version_returns(
        "mix-fam", "spec-v", _healthy(31),
        source="t", spec_hash="spec-aaa",
        data_fingerprint="fp", cost_hash="c", root=tmp_path,
    )
    write_version_returns(
        "mix-fam", "cfg-v", _healthy(32),
        source="t", config_hash="cfg" + "0" * 61,
        data_fingerprint="fp", cost_hash="c", root=tmp_path,
    )
    # bare-v:只写 CSV 无 sidecar(存量硬切场景)
    bare_dir = tmp_path / "data_lake" / "version_returns"
    bare_dir.mkdir(parents=True, exist_ok=True)
    _healthy(33).rename("ret").to_csv(bare_dir / "mix-fam__bare-v.csv", header=True)

    returns, missing, excluded, tiers = spr.load_registered_returns(root=tmp_path)
    assert "mix-fam.spec-v" in returns and tiers["mix-fam.spec-v"] == "spec"
    assert "mix-fam.cfg-v" in returns and tiers["mix-fam.cfg-v"] == "config-only"
    assert "mix-fam.bare-v" not in returns
    assert missing == []
    assert len(excluded) == 1
    assert excluded[0]["leg"] == "mix-fam.bare-v"
    assert "missing_provenance" in excluded[0]["reason"]

    out = recompose(
        returns, identity_tiers=tiers, excluded_no_provenance=excluded,
    )
    assert out["ranking_version"] == RANKING_VERSION
    assert "mix-fam.bare-v" not in {e["name"] for e in out["legs"]}
    assert out["excluded_no_provenance"][0]["leg"] == "mix-fam.bare-v"
