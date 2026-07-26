import json

# Adjust sys.path to find factor_research packages
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.research.report_nlp_pipeline import (
    calculate_file_hash,
    get_next_trade_date,
    load_inbox_state,
    process_report_file,
    run_inbox_pipeline,
    save_inbox_state,
)


class TestReportNLPPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        
        # Patch paths to use temp directory
        self.patchers = [
            patch("scripts.research.report_nlp_pipeline.PDF_DIR", self.tmp_dir / "research_pdf"),
            patch("scripts.research.report_nlp_pipeline.SIGNAL_DIR", self.tmp_dir / "research_signals"),
            patch("scripts.research.report_nlp_pipeline.LOGIC_CHAIN_DIR", self.tmp_dir / "research_signals/logic_chains"),
            patch("scripts.research.report_nlp_pipeline.INBOX_STATE_FILE", self.tmp_dir / "research_pdf/_inbox_state.json"),
            patch("scripts.research.report_nlp_pipeline.FAILURES_LOG_FILE", self.tmp_dir / "report_nlp_failures.jsonl"),
        ]
        for p in self.patchers:
            p.start()
            
        # Re-initialize directories
        (self.tmp_dir / "research_pdf").mkdir(parents=True, exist_ok=True)
        (self.tmp_dir / "research_signals/logic_chains").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        shutil.rmtree(self.tmp_dir)

    def test_calculate_file_hash(self):
        test_file = self.tmp_dir / "test.txt"
        test_file.write_text("hello world", encoding="utf-8")
        h1 = calculate_file_hash(test_file)
        h2 = calculate_file_hash(test_file)
        self.assertEqual(h1, h2)
        # sha256 of "hello world"
        self.assertEqual(h1, "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9")

    def test_inbox_state_load_save(self):
        state = load_inbox_state()
        self.assertEqual(state, {"processed_files": {}})
        
        state["processed_files"]["somehash"] = {"status": "success"}
        save_inbox_state(state)
        
        state2 = load_inbox_state()
        self.assertEqual(state2["processed_files"]["somehash"]["status"], "success")

    def test_get_next_trade_date_fallback(self):
        # Fallback logic check when calendar is not found
        with patch("scripts.research.report_nlp_pipeline.ROOT", self.tmp_dir):
            next_date = get_next_trade_date("2026-06-16")
            self.assertEqual(next_date, "2026-06-17")

    def test_get_next_trade_date_calendar(self):
        # Create mock trade calendar
        meta_dir = self.tmp_dir / "data_lake/meta"
        meta_dir.mkdir(parents=True, exist_ok=True)
        cal_df = pd.DataFrame({"date": pd.to_datetime(["2026-06-15", "2026-06-18", "2026-06-19"])})
        cal_df.to_parquet(meta_dir / "trade_calendar.parquet")
        
        with patch("scripts.research.report_nlp_pipeline.ROOT", self.tmp_dir):
            # next trading day after 2026-06-15 is 2026-06-18
            next_date = get_next_trade_date("2026-06-15")
            self.assertEqual(next_date, "2026-06-18")

    def test_process_report_file_demo_stock(self):
        # Mock convert_pdf_to_text to avoid dependency on PDF libraries
        with patch("scripts.research.report_nlp_pipeline.convert_pdf_to_text", return_value="茅台 600519"):
            pdf_path = self.tmp_dir / "research_pdf/test_maotai.pdf"
            pdf_path.write_text("mock content", encoding="utf-8")
            
            res = process_report_file(pdf_path, is_demo=True)
            self.assertIsNotNone(res)
            self.assertEqual(res["report_type"], "stock")
            self.assertEqual(res["stock_code"], "600519")
            
            # Check file generated
            effective_date = get_next_trade_date("2026-06-16")
            signal_file = self.tmp_dir / f"research_signals/{effective_date}/600519_test_maotai.json"
            self.assertTrue(signal_file.exists())
            
            data = json.loads(signal_file.read_text(encoding="utf-8"))
            self.assertEqual(data["stock_name"], "贵州茅台")

    def test_process_report_file_demo_industry(self):
        with patch("scripts.research.report_nlp_pipeline.convert_pdf_to_text", return_value="有色金属 铜"):
            pdf_path = self.tmp_dir / "research_pdf/test_copper.pdf"
            pdf_path.write_text("mock content", encoding="utf-8")
            
            res = process_report_file(pdf_path, is_demo=True)
            self.assertIsNotNone(res)
            self.assertEqual(res["report_type"], "industry")
            self.assertEqual(res["target_factor_hypothesis_name"], "copper_low_inventory_premium")
            
            # Check logic chain file generated
            logic_file = self.tmp_dir / "research_signals/logic_chains/copper_low_inventory_premium.json"
            self.assertTrue(logic_file.exists())
            
            data = json.loads(logic_file.read_text(encoding="utf-8"))
            self.assertEqual(data["industry"], "有色金属")
            self.assertEqual(len(data["nodes"]), 5)

    def test_process_report_file_production_failed_no_adapter(self):
        # Production mode should fail-fast and log failures when LLM adapter is not available
        with patch("scripts.research.report_nlp_pipeline.convert_pdf_to_text", return_value="茅台"):
            mock_adapter = MagicMock()
            mock_adapter.available.return_value = False
            with patch("scripts.research.report_nlp_pipeline.get_adapter", return_value=mock_adapter):
                pdf_path = self.tmp_dir / "research_pdf/test_fail.pdf"
                pdf_path.write_text("mock content", encoding="utf-8")
                
                res = process_report_file(pdf_path, is_demo=False)
                self.assertIsNone(res)
                
                # Verify failure is logged
                failures_file = self.tmp_dir / "report_nlp_failures.jsonl"
                self.assertTrue(failures_file.exists())
                log_content = failures_file.read_text(encoding="utf-8")
                self.assertIn("RuntimeError", log_content)

    def test_run_inbox_pipeline_demo(self):
        # Put two files in inbox
        p1 = self.tmp_dir / "research_pdf/maotai.pdf"
        p2 = self.tmp_dir / "research_pdf/copper.pdf"
        p1.write_text("茅台", encoding="utf-8")
        p2.write_text("铜", encoding="utf-8")
        
        with patch("scripts.research.report_nlp_pipeline.convert_pdf_to_text", lambda path, is_demo=False: path.name):
            stats = run_inbox_pipeline(demo_mode=True)
            self.assertEqual(stats["scanned"], 2)
            self.assertEqual(stats["processed"], 2)
            self.assertEqual(stats["skipped"], 0)
            self.assertEqual(stats["failed"], 0)
            
            # Check state has been saved
            state_file = self.tmp_dir / "research_pdf/_inbox_state.json"
            self.assertTrue(state_file.exists())
            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(len(state["processed_files"]), 2)
            
            # Run again, should skip
            stats2 = run_inbox_pipeline(demo_mode=True)
            self.assertEqual(stats2["scanned"], 2)
            self.assertEqual(stats2["processed"], 0)
            self.assertEqual(stats2["skipped"], 2)
            self.assertEqual(stats2["failed"], 0)

    def test_run_inbox_pipeline_delete_production(self):
        # Put a file in inbox
        p1 = self.tmp_dir / "research_pdf/maotai.pdf"
        p1.write_text("茅台 600519", encoding="utf-8")
        
        mock_signal = {
            "report_type": "stock",
            "report_date": "2026-06-16",
            "brokerage": "中信证券",
            "analyst": "张三",
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "rating": "买入",
            "target_price": 2100.0,
            "sentiment_score": 0.85,
            "key_thesis": "渠道改革红利",
            "industry": None,
            "mechanism_summary": None,
            "target_factor_hypothesis_name": None,
            "nodes": []
        }
        
        with patch("scripts.research.report_nlp_pipeline.convert_pdf_to_text", return_value="茅台 600519"):
            with patch("scripts.research.report_nlp_pipeline.extract_signals_via_deepseek", return_value=mock_signal):
                # Run with delete_after_process=True and demo_mode=False (production)
                stats = run_inbox_pipeline(demo_mode=False, delete_after_process=True)
                self.assertEqual(stats["scanned"], 1)
                self.assertEqual(stats["processed"], 1)
                self.assertEqual(stats["failed"], 0)
                
                # The file should be deleted automatically
                self.assertFalse(p1.exists())


if __name__ == "__main__":
    unittest.main()
