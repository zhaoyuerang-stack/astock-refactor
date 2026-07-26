"""对抗性测试:方向级教训登记簿(knowledge/directions.py)真的在改变搜索空间分配。

Run:  cd factor_research && python3 tests/test_direction_registry.py

护栏 C:每条都验证"机制真的咬人",不只 happy-path——
  SKIP 真拒(且空登记簿基线下同因子必须出现,证明因果而非偶然缺席);
  证据门控真拒(无 evidence 条目不产生任何 steering);
  保质期真复活(过期条目失效 = 复活重测);
  排序真变(BOOST 排头 / DEPRIORITIZE 与同 MI 簇排尾);
  自饿保护真兜底(全 SKIP → 退回未过滤);
  term_factor 真修 DSL 盲区(旧 factor_fn_name 匹配对 autoresearch 候选必失配);
  LLM prompt 真注入(且空登记簿时块必须消失,证明由登记簿驱动非硬编码)。
全程 fixture 文件,绝不写真实 knowledge/ 与 metasearch/。
"""
from __future__ import annotations

import json
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import knowledge.directions as kd
from knowledge.graph import SearchGate, load_graph

# ── fixture 工具 ────────────────────────────────────────────────────────────

def _entry(**kw) -> dict:
    base = dict(
        id="e1", direction="测试方向", status="weak", action="DEPRIORITIZE",
        scope_factors=["northbound_accumulation"], evidence=["LESSONS.md#test"],
        revival_condition="", created="2026-07-02", expires="", prompt_note="",
    )
    base.update(kw)
    return base


def _write_registry(tmpdir: str, entries: list[dict], name: str = "registry.json") -> str:
    p = Path(tmpdir) / name
    p.write_text(json.dumps({"version": 1, "entries": entries}, ensure_ascii=False), encoding="utf-8")
    return str(p)


@contextmanager
def _patched_defaults(registry: str, clusters: str | None = None, frontier: str | None = None):
    """把模块默认路径指向 fixture(缺省 clusters/frontier 指向不存在文件 = fail-open 路径)。"""
    old = (kd.DEFAULT_REGISTRY, kd.DEFAULT_CLUSTERS, kd.DEFAULT_FRONTIER)
    try:
        kd.DEFAULT_REGISTRY = registry
        kd.DEFAULT_CLUSTERS = clusters or str(Path(registry).parent / "_no_clusters.json")
        kd.DEFAULT_FRONTIER = frontier or str(Path(registry).parent / "_no_frontier.json")
        yield
    finally:
        kd.DEFAULT_REGISTRY, kd.DEFAULT_CLUSTERS, kd.DEFAULT_FRONTIER = old


def _dsl_hyp(*factors: str) -> SimpleNamespace:
    """autoresearch DSL 候选的 duck-typed Hypothesis(factor_fn_name 恒为封装函数)。"""
    return SimpleNamespace(
        id="h1", name="autoresearch_test",
        factor_fn_name="factors.autoresearch_dsl.compute_dsl_factor",
        timing_fn_name="",
        factor_params={"ast": {"terms": [{"factor": f, "params": {}} for f in factors]}},
    )


def _seed_factor_sets(limit: int = 500) -> list[frozenset]:
    """按生成顺序取每个种子候选的成分因子集合。"""
    from factory.autoresearch.generator import generate_seed_candidates

    out = []
    for c in generate_seed_candidates(limit=limit):
        out.append(frozenset(t["factor"] for t in c.ast["terms"]))
    return out


# ── 证据门控 / 校验 ──────────────────────────────────────────────────────────

def test_evidence_gating_rejects_entry_without_evidence():
    with tempfile.TemporaryDirectory() as td:
        no_ev = _write_registry(td, [_entry(evidence=[]), _entry(id="e2", evidence=["  "])])
        assert kd.load_direction_entries(no_ev) == [], "无证据条目必须被忽略(证据门控)"
        # 对照:同条目补上证据即生效 → 证明拒的是证据缺失,不是解析失败
        with_ev = _write_registry(td, [_entry()], name="ok.json")
        assert len(kd.load_direction_entries(with_ev)) == 1


def test_invalid_action_rejected():
    with tempfile.TemporaryDirectory() as td:
        path = _write_registry(td, [_entry(action="BANISH"), _entry(id="", action="SKIP")])
        assert kd.load_direction_entries(path) == [], "非法 action / 缺 id 条目必须被忽略"


def test_missing_or_corrupt_registry_fails_open():
    with tempfile.TemporaryDirectory() as td:
        assert kd.load_direction_entries(str(Path(td) / "nope.json")) == []
        bad = Path(td) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert kd.load_direction_entries(str(bad)) == []


def test_shipped_registry_is_valid_and_evidence_backed():
    """守住随仓策展内容本身:能解析、全部带证据、因子名在白名单内。"""
    from factory.autoresearch.registry import ALLOWED_FACTORS

    # 显式路径:整目录 pytest 下 test_autoresearch_engine 会把默认路径钉到 /nonexistent(hermetic)
    entries = kd.load_direction_entries(str(ROOT / "knowledge" / "direction_registry.json"))
    assert len(entries) >= 4, "随仓登记簿应含初始策展条目"
    for e in entries:
        assert e.evidence, f"{e.id} 缺证据"
        for f in e.scope_factors:
            assert f in ALLOWED_FACTORS, f"{e.id} 的 scope_factors 含非白名单因子 {f}"


# ── 保质期 = 复活重测 ────────────────────────────────────────────────────────

def test_expired_entry_deactivates_and_seeds_revive():
    with tempfile.TemporaryDirectory() as td:
        expired = _write_registry(td, [_entry(action="SKIP", expires="2020-01-01")])
        assert kd.active_entries(expired) == [], "过期条目必须失效(复活重测语义)"
        with _patched_defaults(expired):
            revived = _seed_factor_sets()
        assert any("northbound_accumulation" in s for s in revived), "条目过期后死路因子必须复活"

        live = _write_registry(td, [_entry(action="SKIP", expires="2099-01-01")], name="live.json")
        act, reason = kd.seed_action(("northbound_accumulation", "roe"), entries=kd.active_entries(live))
        assert act == "SKIP" and "e1" in reason


# ── 生成器真拒 / 真排序 / 真兜底 ─────────────────────────────────────────────

def test_generator_skip_is_causal():
    """SKIP 真拒:空登记簿基线含该因子(因果对照),登记 SKIP 后消失,其他种子不受株连。"""
    with tempfile.TemporaryDirectory() as td:
        empty = _write_registry(td, [])
        with _patched_defaults(empty):
            baseline = _seed_factor_sets()
        assert any("northbound_accumulation" in s for s in baseline), "基线必须含目标因子,否则本测试失去因果意义"

        skip = _write_registry(td, [_entry(action="SKIP")], name="skip.json")
        with _patched_defaults(skip):
            filtered = _seed_factor_sets()
        assert filtered, "过滤后仍须有种子"
        assert not any("northbound_accumulation" in s for s in filtered), "SKIP 方向的种子必须消失"
        assert any("momentum" in s for s in filtered), "无关种子不得被株连"


def test_steer_order_boost_first_deprioritize_last():
    from factory.autoresearch.generator import _steer_seed_order

    seeds = [
        ("momentum", {"window": 20}, "volume_ratio", {"window": 5}),
        ("northbound_accumulation", {"window": 20}, "momentum", {"window": 20}),
        ("bp_proxy", {}, "volatility", {"window": 60}),
    ]
    with tempfile.TemporaryDirectory() as td:
        reg = _write_registry(td, [
            _entry(id="weak", action="DEPRIORITIZE", scope_factors=["northbound_accumulation"]),
            _entry(id="front", action="BOOST", status="frontier", scope_factors=["bp_proxy"]),
        ])
        with _patched_defaults(reg):
            steered = _steer_seed_order(list(seeds))
    assert steered[0][0] == "bp_proxy", "BOOST 种子必须排头(算力倾斜)"
    assert steered[-1][0] == "northbound_accumulation", "DEPRIORITIZE 种子必须排尾"
    assert steered[1][0] == "momentum"


def test_steer_self_starvation_fallback():
    """登记簿把种子全 SKIP → 退回未过滤顺序(生成端 steering 不得阻断搜索)。"""
    from factory.autoresearch.generator import _steer_seed_order

    seeds = [("momentum", {"window": 20}, "roe", {})]
    with tempfile.TemporaryDirectory() as td:
        reg = _write_registry(td, [_entry(action="SKIP", scope_factors=["momentum", "roe"])])
        with _patched_defaults(reg):
            steered = _steer_seed_order(list(seeds))
    assert steered == seeds, "全灭时必须退回未过滤种子(自饿保护)"


def test_mi_cluster_deprioritizes_same_information_pair():
    from factory.autoresearch.generator import _steer_seed_order

    seeds = [
        ("momentum", {"window": 20}, "volume_ratio", {"window": 5}),  # 同簇 → 排尾
        ("roe", {}, "bp_proxy", {}),
    ]
    with tempfile.TemporaryDirectory() as td:
        empty = _write_registry(td, [])
        clusters = Path(td) / "clusters.json"
        clusters.write_text(json.dumps(
            {"factor_clusters": [["momentum", "volume_ratio"]]}), encoding="utf-8")
        with _patched_defaults(empty, clusters=str(clusters)):
            steered = _steer_seed_order(list(seeds))
        assert steered[0][0] == "roe" and steered[-1][0] == "momentum", "同 MI 簇两腿组合=同一信息算两遍,必须排尾"
        # fail-open 对照:簇文件缺失 → 顺序不变
        with _patched_defaults(empty):
            plain = _steer_seed_order(list(seeds))
        assert plain == seeds


# ── term_factor 修 DSL 盲区 + load_graph 合并 ───────────────────────────────

def test_term_factor_gate_fixes_dsl_blind_spot():
    hyp = _dsl_hyp("northbound_accumulation", "momentum")
    old_gate = SearchGate(match={"factor_fn_name": "northbound_accumulation"}, action="SKIP")
    assert not old_gate.matches(hyp), "旧 factor_fn_name 匹配对 DSL 候选必失配(这就是盲区)"
    new_gate = SearchGate(match={"term_factor": "northbound_accumulation"}, action="SKIP")
    assert new_gate.matches(hyp), "term_factor 匹配必须命中 DSL 候选成分因子"
    assert not new_gate.matches(_dsl_hyp("roe", "momentum")), "不含该因子的候选不得误伤"


def test_load_graph_merges_directions_without_persisting():
    with tempfile.TemporaryDirectory() as td:
        store = Path(td) / "findings.json"
        store.write_text("{}", encoding="utf-8")
        reg = _write_registry(td, [
            _entry(action="SKIP", scope_factors=["northbound_accumulation"]),
            _entry(id="e2", action="DEPRIORITIZE", scope_factors=["holder_count_chg"]),
        ])
        with _patched_defaults(reg):
            kg = load_graph(str(store))
            skip, reason = kg.should_skip(_dsl_hyp("northbound_accumulation", "roe"))
            assert skip and "e1" in reason, "方向 SKIP 必须经 load_graph 生效到 DSL 候选"
            adj = kg.priority_adjustment(_dsl_hyp("holder_count_chg", "roe"))
            assert adj == 0.3, "方向 DEPRIORITIZE 必须降权 0.3"
            assert kg.priority_adjustment(_dsl_hyp("roe", "momentum")) == 1.0
            # 策展条目只内存合并,绝不写回机器自长的 findings.json
            assert "direction_" not in store.read_text(encoding="utf-8")
            # 关闭合并的对照:证明命中来自方向登记簿
            kg_off = load_graph(str(store), include_directions=False)
            assert not kg_off.should_skip(_dsl_hyp("northbound_accumulation", "roe"))[0]


# ── LLM prompt 真注入 ───────────────────────────────────────────────────────

class _FakeAdapter:
    model = "fake-model"

    def __init__(self):
        self.captured = {}

    def available(self):
        return True

    def complete(self, system, user, max_tokens=0):
        self.captured["system"], self.captured["user"] = system, user
        return "[]"


class _FakeRepo:
    def all(self):
        return []

    def get(self, fp):
        return None


def test_llm_prompt_direction_injection_is_registry_driven():
    from services.actions.autoresearch_llm import generate_llm_candidates

    with tempfile.TemporaryDirectory() as td:
        reg = _write_registry(td, [_entry(prompt_note="北向族已证太弱-INJECT-MARKER")])
        adapter = _FakeAdapter()
        with _patched_defaults(reg):
            generate_llm_candidates(n=1, theme="t", adapter=adapter, repository=_FakeRepo())
        assert "INJECT-MARKER" in adapter.captured["user"], "方向教训必须注入 LLM 播种 prompt"
        assert "方向级研究教训" in adapter.captured["user"]

        # 对照:空登记簿 → 教训块必须消失(证明由登记簿驱动,非硬编码)
        empty = _write_registry(td, [], name="empty.json")
        adapter2 = _FakeAdapter()
        with _patched_defaults(empty):
            generate_llm_candidates(n=1, theme="t", adapter=adapter2, repository=_FakeRepo())
        assert "方向级研究教训" not in adapter2.captured["user"]


# ── runner ──────────────────────────────────────────────────────────────────

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
