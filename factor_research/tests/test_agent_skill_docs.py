"""Agent skill documentation guard.

Run:
    cd factor_research && python3 tests/test_agent_skill_docs.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_agent_operating_model_exists():
    doc = ROOT / "docs" / "agent_operating_model.md"
    assert doc.exists()
    text = doc.read_text(encoding="utf-8")
    for term in ["Agent", "Skill", "Tool", "Data", "Strategy", "Governance"]:
        assert term in text


def test_skill_docs_have_required_sections():
    skill_dir = ROOT / "docs" / "agent_skills"
    expected = [
        "data_health.md",
        "factor_audit.md",
        "candidate_promote.md",
        "production_readiness.md",
        "module_cleanup.md",
    ]
    for name in expected:
        path = skill_dir / name
        assert path.exists(), f"missing {path}"
        text = path.read_text(encoding="utf-8")
        for section in ["## Inputs", "## Allowed Tools", "## Forbidden", "## Success Criteria"]:
            assert section in text, f"{name} missing {section}"


if __name__ == "__main__":
    test_agent_operating_model_exists()
    test_skill_docs_have_required_sections()
    print("agent skill docs tests passed")
