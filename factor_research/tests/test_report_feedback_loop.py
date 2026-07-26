import json

# Adjust sys.path to find factor_research packages
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.research.report_feedback_loop import (
    classify_performance,
    find_matching_metrics,
    load_registry,
    run_feedback_loop,
)


class TestReportFeedbackLoop(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        
        # Patch files to use temp directory paths
        self.patchers = [
            patch("scripts.research.report_feedback_loop.REGISTRY_FILE", self.tmp_dir / "strategy_versions.json"),
            patch("scripts.research.report_feedback_loop.GRAPH_FILE", self.tmp_dir / "industry_knowledge_graph.json"),
            patch("scripts.research.report_feedback_loop.FEEDBACK_LEDGER_FILE", self.tmp_dir / "report_feedback_ledger.json"),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        shutil.rmtree(self.tmp_dir)

    def test_load_registry_empty(self):
        res = load_registry()
        self.assertEqual(res, {"families": []})

    def test_load_registry_valid(self):
        registry_file = self.tmp_dir / "strategy_versions.json"
        data = {"families": [{"id": "test_strat"}]}
        registry_file.write_text(json.dumps(data), encoding="utf-8")
        
        res = load_registry()
        self.assertEqual(res, data)

    def test_find_matching_metrics(self):
        registry_data = {
            "families": [
                {
                    "id": "small_cap",
                    "status": "active",
                    "versions": [
                        {
                            "version": "v1.0",
                            "config": {"factor": "size"},
                            "metrics": {"sharpe": 1.2, "annual": 0.25, "maxdd": -0.1}
                        }
                    ]
                },
                {
                    "id": "illiquidity",
                    "status": "discarded",
                    "versions": [
                        {
                            "version": "v3.1",
                            "config": {"factor": "amihud_20d"},
                            "metrics": {"sharpe": 0.3, "annual": 0.02, "maxdd": -0.3}
                        }
                    ]
                }
            ]
        }

        # 1. Exact match
        metrics, status = find_matching_metrics(registry_data, "small_cap")
        self.assertIsNotNone(metrics)
        self.assertEqual(metrics["sharpe"], 1.2)
        self.assertEqual(status, "active")

        # 2. Substring match
        metrics, status = find_matching_metrics(registry_data, "illiquid")
        self.assertIsNotNone(metrics)
        self.assertEqual(metrics["sharpe"], 0.3)
        self.assertEqual(status, "discarded")

        # 3. Factor config match
        metrics, status = find_matching_metrics(registry_data, "amihud_20d")
        self.assertIsNotNone(metrics)
        self.assertEqual(status, "discarded")

        # 4. No match
        metrics, status = find_matching_metrics(registry_data, "unknown_factor")
        self.assertIsNone(metrics)
        self.assertIsNone(status)

    def test_classify_performance_untested(self):
        res = classify_performance(None, None)
        self.assertEqual(res["status"], "untested")
        self.assertEqual(res["confidence"], 0.5)

    def test_classify_performance_refuted_by_status(self):
        metrics = {"sharpe": 1.5, "annual": 0.3, "maxdd": -0.1}
        res = classify_performance(metrics, "discarded")
        self.assertEqual(res["status"], "refuted")
        self.assertEqual(res["confidence"], 0.15)

    def test_classify_performance_refuted_by_metrics(self):
        metrics = {"sharpe": 0.3, "annual": 0.02, "maxdd": -0.3}
        res = classify_performance(metrics, "active")
        self.assertEqual(res["status"], "refuted")
        self.assertEqual(res["confidence"], 0.15)

    def test_classify_performance_verified(self):
        metrics = {"sharpe": 1.2, "annual": 0.20, "maxdd": -0.08}
        res = classify_performance(metrics, "active")
        self.assertEqual(res["status"], "verified")
        self.assertEqual(res["confidence"], 0.95)

    def test_classify_performance_weak(self):
        metrics = {"sharpe": 0.6, "annual": 0.08, "maxdd": -0.15}
        res = classify_performance(metrics, "active")
        self.assertEqual(res["status"], "weak")
        self.assertEqual(res["confidence"], 0.60)

    def test_run_feedback_loop(self):
        # Setup strategy versions
        registry_file = self.tmp_dir / "strategy_versions.json"
        registry_data = {
            "families": [
                {
                    "id": "copper_low_inventory",
                    "status": "discarded",
                    "versions": [
                        {
                            "version": "v1.0",
                            "metrics": {"sharpe": 0.2, "annual": -0.05, "maxdd": -0.4}
                        }
                    ]
                },
                {
                    "id": "computing_power",
                    "status": "active",
                    "versions": [
                        {
                            "version": "v1.0",
                            "metrics": {"sharpe": 1.5, "annual": 0.35, "maxdd": -0.05}
                        }
                    ]
                }
            ]
        }
        registry_file.write_text(json.dumps(registry_data), encoding="utf-8")

        # Setup industry knowledge graph
        graph_file = self.tmp_dir / "industry_knowledge_graph.json"
        graph_data = {
            "nodes": [
                {
                    "id": "policy_support",
                    "category": "supply",
                    "industries": ["算力"],
                    "hypotheses": ["computing_power"],
                    "evidence": "政策扶持算力"
                },
                {
                    "id": "global_copper_inventory",
                    "category": "supply",
                    "industries": ["铜"],
                    "hypotheses": ["copper_low_inventory"],
                    "evidence": "全球铜库存处于低位"
                },
                {
                    "id": "untested_node",
                    "category": "demand",
                    "industries": ["黄金"],
                    "hypotheses": ["gold_hypothesis"],
                    "evidence": "避险需求"
                }
            ],
            "links": [
                {
                    "source": "policy_support",
                    "target": "global_copper_inventory",
                    "industry": "铜",
                    "hypothesis": "copper_low_inventory",
                    "evidence": "铜供需关系影响"
                }
            ]
        }
        graph_file.write_text(json.dumps(graph_data), encoding="utf-8")

        # Run feedback loop
        run_feedback_loop()

        # Load updated graph
        updated_graph = json.loads(graph_file.read_text(encoding="utf-8"))
        
        # Verify policy_support node (computing_power hypothesis -> verified)
        policy_node = next(n for n in updated_graph["nodes"] if n["id"] == "policy_support")
        self.assertEqual(policy_node["backtest_status"], "verified")
        self.assertEqual(policy_node["confidence_score"], 0.95)

        # Verify global_copper_inventory node (copper_low_inventory hypothesis -> refuted)
        copper_node = next(n for n in updated_graph["nodes"] if n["id"] == "global_copper_inventory")
        self.assertEqual(copper_node["backtest_status"], "refuted")
        self.assertEqual(copper_node["confidence_score"], 0.15)

        # Verify untested_node node (gold_hypothesis -> untested)
        untested_node = next(n for n in updated_graph["nodes"] if n["id"] == "untested_node")
        self.assertEqual(untested_node["backtest_status"], "untested")
        self.assertEqual(untested_node["confidence_score"], 0.5)

        # Verify link (copper_low_inventory hypothesis -> refuted)
        link = updated_graph["links"][0]
        self.assertEqual(link["backtest_status"], "refuted")
        self.assertEqual(link["confidence_score"], 0.15)
        self.assertEqual(link["backtest_metrics"]["sharpe"], 0.2)

        # Verify feedback ledger
        ledger_file = self.tmp_dir / "report_feedback_ledger.json"
        self.assertTrue(ledger_file.exists())
        ledger_data = json.loads(ledger_file.read_text(encoding="utf-8"))
        self.assertEqual(len(ledger_data["refuted_hypotheses"]), 1)
        self.assertEqual(ledger_data["refuted_hypotheses"][0]["hypothesis_name"], "copper_low_inventory")

if __name__ == "__main__":
    unittest.main()
