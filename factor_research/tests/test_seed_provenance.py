"""种子溯源(ADR-022)端到端测试:种子来源标注 → 血缘继承 → 仓库往返 → 晋级证据。

审计 #7「种子候选可能含金库知识」的可行动部分 = 记录种子来源。本测试钉死整条链:
确定性种子/LLM 种子如实标注 → 变异/交叉子代继承祖先来源(不断链)→ 仓库读写不丢 →
晋级时进 registry evidence,LLM 起源触发 semantic_seed_review 供人工额外审视。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory.autoresearch.generator import generate_seed_candidates
from factory.autoresearch.islands import _merge_provenance
from factory.autoresearch.models import Candidate
from factory.autoresearch.repositories import CandidateRepository
from workflow.phase4_register import Phase4Register


def test_deterministic_seed_tagged():
    seeds = list(generate_seed_candidates(limit=3))
    assert seeds, "无种子产出"
    prov = seeds[0].provenance
    assert prov.get("origin") == "deterministic_seed"
    assert "_SEEDS" in prov.get("catalog", "")
    assert "×" in prov.get("pair", "")  # 教科书因子对


def test_lineage_merges_llm_and_deterministic():
    llm = Candidate("a", {}, provenance={"origin": "llm_seed", "theme": "动量", "model": "deepseek-v4"})
    det = Candidate("b", {}, provenance={"origin": "deterministic_seed"})
    child = _merge_provenance([llm, det])
    assert child["origin"] == "derived"
    assert child["ancestor_origins"] == ["deterministic_seed", "llm_seed"]
    assert child["llm_ancestors"] == [{"theme": "动量", "model": "deepseek-v4"}]


def test_lineage_propagates_to_grandchild():
    # derived 子代再变异 → 孙代仍保留 llm_seed 祖先(溯源不因多代进化断链)
    llm = Candidate("a", {}, provenance={"origin": "llm_seed", "theme": "价值", "model": "m1"})
    child_prov = _merge_provenance([llm])
    grandchild = _merge_provenance([Candidate("c", {}, provenance=child_prov)])
    assert "llm_seed" in grandchild["ancestor_origins"]
    assert grandchild["llm_ancestors"] == [{"theme": "价值", "model": "m1"}]


def test_pure_deterministic_lineage_has_no_llm():
    d1 = Candidate("a", {}, provenance={"origin": "deterministic_seed"})
    d2 = Candidate("b", {}, provenance={"origin": "deterministic_seed"})
    child = _merge_provenance([d1, d2])
    assert child["ancestor_origins"] == ["deterministic_seed"]
    assert "llm_ancestors" not in child  # 无 LLM 祖先 → 不带 LLM 细节


def test_repository_roundtrip_preserves_provenance(tmp_path):
    repo = CandidateRepository(path=tmp_path / "candidates.jsonl")
    prov = {"origin": "llm_seed", "theme": "流动性", "model": "deepseek-v4"}
    repo.record(Candidate("fp1", {"type": "linear_combo"}, provenance=prov))
    # 新建实例强制从磁盘 reload,验证 _deserialize 不丢 provenance
    reloaded = CandidateRepository(path=tmp_path / "candidates.jsonl").get("fp1")
    assert reloaded is not None
    assert reloaded.provenance == prov


def test_evidence_flags_llm_seed_for_semantic_review():
    ev = Phase4Register._build_evidence("h1", ["e1"], {"origin": "llm_seed", "theme": "价值", "model": "m1"})
    assert ev["seed_provenance"]["origin"] == "llm_seed"
    assert ev["semantic_seed_review"]["required"] is True
    assert ev["semantic_seed_review"]["llm_ancestors"] == [{"theme": "价值", "model": "m1"}]


def test_evidence_flags_derived_with_llm_ancestor():
    # 变异子代(derived)若祖先含 llm_seed,也要触发 semantic_seed_review
    prov = {"origin": "derived", "ancestor_origins": ["deterministic_seed", "llm_seed"],
            "llm_ancestors": [{"theme": "动量", "model": "m1"}]}
    ev = Phase4Register._build_evidence("h1", [], prov)
    assert ev["semantic_seed_review"]["required"] is True


def test_evidence_no_flag_for_deterministic():
    ev = Phase4Register._build_evidence("h1", [], {"origin": "deterministic_seed"})
    assert ev["seed_provenance"]["origin"] == "deterministic_seed"
    assert "semantic_seed_review" not in ev


def test_evidence_empty_provenance_backward_compatible():
    # 无 provenance(旧/手动路径)→ evidence 不含 seed_provenance,向后兼容
    ev = Phase4Register._build_evidence("h1", ["e1"], None)
    assert "seed_provenance" not in ev and "semantic_seed_review" not in ev
    assert ev["hypothesis_id"] == "h1"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
