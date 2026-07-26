"""对抗性测试:metasearch 机器可读导出(冗余簇/空白区)与生成端消费的契约。

Run:  cd factor_research && python3 tests/test_metasearch_exports.py

护栏 C 关注点:
  簇映射不得虚增(同基础因子塌缩后单成员簇必须丢弃,防"假冗余"错误降权);
  frontier 排序必须真反映"距 LIVE 锚最远"(近锚候选不得混进空白区);
  LIVE 锚自身绝不出现在 frontier(否则算力倾斜会回灌已部署方向);
  消费端 knowledge.directions 与导出 schema 的契约(factor_clusters/factors 字段)。
纯函数 + fixture,不依赖数据湖。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import knowledge.directions as kd
from metasearch.factor_mi_audit import factor_clusters_from
from metasearch.information_map import frontier_from_distances


def test_factor_clusters_collapse_and_drop_singletons():
    clusters = [
        # 6 个窗口变体 → 同一基础因子:塌缩成单成员 → 必须丢弃(不是真跨因子冗余)
        ["small_cap__w20", "small_cap__w30", "small_cap__w60"],
        # 真跨因子冗余簇 → 保留且排序
        ["illiq__n40", "size_proxy__x"],
        # 映射不到的名字 → 丢弃;剩单成员 → 丢弃
        ["mystery_hyp", "roe__q"],
    ]
    name_to_base = {
        "small_cap__w20": "small_cap_factor", "small_cap__w30": "small_cap_factor",
        "small_cap__w60": "small_cap_factor",
        "illiq__n40": "illiquidity", "size_proxy__x": "size_proxy",
        "roe__q": "roe",
    }
    out = factor_clusters_from(clusters, name_to_base)
    assert out == [["illiquidity", "size_proxy"]], f"意外产物: {out}"


def test_frontier_ranks_by_distance_to_live_anchors_and_excludes_live():
    names = ["live_a", "cand_far", "cand_near", "cand_mid"]
    dist = pd.DataFrame(0.0, index=names, columns=names)
    for a, b, v in [
        ("live_a", "cand_far", 2.9), ("live_a", "cand_near", 0.4),
        ("live_a", "cand_mid", 1.5), ("cand_far", "cand_near", 2.0),
        ("cand_far", "cand_mid", 2.0), ("cand_near", "cand_mid", 1.0),
    ]:
        dist.loc[a, b] = dist.loc[b, a] = v
    status = {"live_a": "live_active"}
    base = {"cand_far": "bp_proxy", "cand_near": "momentum", "cand_mid": "volatility"}

    top, factors = frontier_from_distances(dist, status, base, k=2)
    ranked = [r["name"] for r in top]
    assert ranked == ["cand_far", "cand_mid"], f"必须按距 LIVE 锚降序: {ranked}"
    assert "live_a" not in ranked, "LIVE 锚不得混进空白区(算力会回灌已部署方向)"
    assert "cand_near" not in ranked, "近锚候选不是空白区"
    assert factors == ["bp_proxy", "volatility"]


def test_frontier_without_live_anchor_falls_back_to_mean_distance():
    names = ["a", "b", "c"]
    dist = pd.DataFrame(0.0, index=names, columns=names)
    dist.loc["a", "b"] = dist.loc["b", "a"] = 1.0
    dist.loc["a", "c"] = dist.loc["c", "a"] = 3.0
    dist.loc["b", "c"] = dist.loc["c", "b"] = 2.0
    top, _ = frontier_from_distances(dist, {}, {}, k=1)
    assert top[0]["name"] == "c", "无 LIVE 锚时取平均两两距离最大者"


def test_directions_consume_exported_schemas():
    """消费端契约:导出 schema 变更必须让本测试失败(防静默断链)。"""
    with tempfile.TemporaryDirectory() as td:
        clusters_p = Path(td) / "redundancy_clusters.json"
        clusters_p.write_text(json.dumps({
            "generated_at": "2026-07-02", "threshold": 2.0, "n_hypotheses": 3,
            "clusters": [["h1", "h2"]],
            "factor_clusters": [["illiquidity", "size_proxy"], ["momentum"]],
        }), encoding="utf-8")
        cl = kd.redundancy_clusters(str(clusters_p))
        assert cl == [{"illiquidity", "size_proxy"}], "单成员簇必须被消费端丢弃"
        assert kd.same_cluster("illiquidity", "size_proxy", clusters=cl)
        assert not kd.same_cluster("illiquidity", "momentum", clusters=cl)

        frontier_p = Path(td) / "frontier.json"
        frontier_p.write_text(json.dumps({
            "generated_at": "2026-07-02", "k": 2,
            "signals": [{"name": "cand_far", "distance": 2.9}],
            "factors": ["bp_proxy", "volatility"],
        }), encoding="utf-8")
        assert kd.frontier_factors(str(frontier_p)) == {"bp_proxy", "volatility"}
        # 坏文件 fail-open
        bad = Path(td) / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        assert kd.frontier_factors(str(bad)) == set()
        assert kd.redundancy_clusters(str(bad)) == []


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
