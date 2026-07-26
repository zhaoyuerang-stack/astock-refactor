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

from scripts.research.run_ontology_shadow_pipeline import run_ontology_shadow_pipeline


class TestOntologyShadowPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        
        # Patch all file paths to temp directory
        self.patchers = [
            patch("scripts.research.run_ontology_shadow_pipeline.GRAPH_FILE", self.tmp_dir / "industry_knowledge_graph.json"),
            patch("scripts.research.run_ontology_shadow_pipeline.PREDICTIONS_FILE", self.tmp_dir / "ontology_predictions.json"),
            patch("scripts.research.run_ontology_shadow_pipeline.PERFORMANCE_FILE", self.tmp_dir / "shadow_ontology_performance.json"),
            patch("scripts.research.run_ontology_shadow_pipeline.SHADOW_LOG", self.tmp_dir / "shadow_incubation_log.json"),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        shutil.rmtree(self.tmp_dir)

    def test_pipeline_skips_when_no_graph(self):
        # By default, graph file doesn't exist
        run_ontology_shadow_pipeline()
        
        # None of the outputs should be created
        self.assertFalse((self.tmp_dir / "ontology_predictions.json").exists())
        self.assertFalse((self.tmp_dir / "shadow_ontology_performance.json").exists())
        self.assertFalse((self.tmp_dir / "shadow_incubation_log.json").exists())

    @patch("scripts.research.run_ontology_shadow_pipeline.load_price_panels")
    def test_pipeline_runs_successfully(self, mock_load_panels):
        # Mock load_price_panels returns dummy close, volume, amount DataFrames
        import pandas as pd
        dates = pd.date_range("2023-01-01", periods=10, freq="D")
        close_df = pd.DataFrame(
            100.0,
            index=dates,
            columns=["000977", "603019", "002415", "601899", "600362", "000878", "600519", "000858", "000568"]
        )
        mock_load_panels.return_value = (close_df, close_df, close_df)
        
        # Create a mock knowledge graph
        graph_data = {
            "nodes": [
                {
                    "id": " policy_support",
                    "category": "supply",
                    "industries": ["算力网"],
                    "last_change": "up",
                    "numeric_value": 0.12
                },
                {
                    "id": "copper_price",
                    "category": "price",
                    "industries": ["铜"],
                    "last_change": "up",
                    "numeric_value": 0.05
                }
            ],
            "links": []
        }
        graph_file = self.tmp_dir / "industry_knowledge_graph.json"
        graph_file.write_text(json.dumps(graph_data), encoding="utf-8")

        # Run pipeline
        run_ontology_shadow_pipeline()

        # Check outputs are created
        predictions_file = self.tmp_dir / "ontology_predictions.json"
        performance_file = self.tmp_dir / "shadow_ontology_performance.json"
        shadow_log = self.tmp_dir / "shadow_incubation_log.json"

        self.assertTrue(predictions_file.exists())
        self.assertTrue(performance_file.exists())
        self.assertTrue(shadow_log.exists())

        # Verify predictions
        pred_data = json.loads(predictions_file.read_text(encoding="utf-8"))
        self.assertIn("rankings", pred_data)
        self.assertIn("bom_shocks", pred_data)
        self.assertIn("framework_scores", pred_data)
        
        # Verify framework score for 算力网
        self.assertIn("算力网", pred_data["framework_scores"])
        self.assertEqual(pred_data["framework_scores"]["算力网"]["stage"], "rapid_growth")

        # Verify shadow performance
        perf_data = json.loads(performance_file.read_text(encoding="utf-8"))
        self.assertEqual(len(perf_data["dates"]), 10)
        self.assertEqual(len(perf_data["shadow_nav"]), 10)

if __name__ == "__main__":
    unittest.main()
