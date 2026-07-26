"""Naming taxonomy documentation guard.

Run:
    cd factor_research && python3 tests/test_naming_taxonomy.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_naming_taxonomy_doc_exists_and_defines_required_concepts():
    doc = ROOT / "docs" / "naming_taxonomy.md"
    assert doc.exists(), "docs/naming_taxonomy.md must define canonical naming rules"
    text = doc.read_text(encoding="utf-8")
    required = [
        "Factor",
        "Signal",
        "Timing/Regime",
        "Strategy",
        "Policy",
        "Portfolio",
        "Engine",
        "zscore_cross_section",
        "zscore_series",
        "factor_to_signal",
        "loser_reversal_filter",
        "salience_covariance_score",
    ]
    missing = [term for term in required if term not in text]
    assert not missing, f"naming taxonomy missing terms: {missing}"


def test_ontology_glossary_links_taxonomy():
    glossary = ROOT / "docs" / "ontology_glossary.md"
    text = glossary.read_text(encoding="utf-8")
    assert "naming_taxonomy.md" in text


if __name__ == "__main__":
    test_naming_taxonomy_doc_exists_and_defines_required_concepts()
    test_ontology_glossary_links_taxonomy()
    print("naming taxonomy doc tests passed")
