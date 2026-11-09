#!/usr/bin/env python3
"""ML-7c story summary tests: synthetic data only — no DB, no network.

    python3 tests/test_ml7c.py
"""
import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent / "ops-tools"
spec = importlib.util.spec_from_file_location("ml7c", OPS / "ml7c.py")
ml7c = importlib.util.module_from_spec(spec)
sys.modules["ml7c"] = ml7c
spec.loader.exec_module(ml7c)


class StoryFetch(unittest.TestCase):
    def test_fetch_stories_joins_on_headline_id(self):
        orig_psql = ml7c.psql
        queries = []
        try:
            def fake_psql(sql, text=True):
                queries.append(sql)
                return "101,RBI holds repo rate at 6.5%\n101,Central bank keeps policy rate at 6.5%\n102,Single headline\n"
            ml7c.psql = fake_psql
            stories = ml7c.fetch_stories(limit=10)
            self.assertEqual(len(stories), 1)
            self.assertEqual(stories[0][0], 101)
            self.assertEqual(len(stories[0][1]), 2)
            self.assertIn("JOIN raw_headlines h ON h.id = e.headline_id", queries[0])
        finally:
            ml7c.psql = orig_psql

    def test_summarize_story_no_gateway(self):
        # No LLM_BASE_URL set — should return None gracefully
        old = os.environ.get("LLM_BASE_URL")
        os.environ.pop("LLM_BASE_URL", None)
        try:
            summary, err, model = ml7c.summarize_story(["headline one", "headline two"])
            self.assertIsNone(summary)
            self.assertIn("no gateway", err)
        finally:
            if old is not None:
                os.environ["LLM_BASE_URL"] = old


class SummaryWriteAndShow(unittest.TestCase):
    def test_write_summary_records_verified_flag(self):
        orig_psql = ml7c.psql
        queries = []
        try:
            def fake_psql(sql, text=True):
                queries.append(sql)
                return "INSERT 0 1\n"
            ml7c.psql = fake_psql
            ok = ml7c.write_summary(101, 2, "RBI kept rate at 6.5%.", "deadbeef", "general", verified=True)
            self.assertTrue(ok)
            self.assertIn("TRUE", queries[0])
        finally:
            ml7c.psql = orig_psql

    def test_show_prints_all_rows_without_truncation(self):
        orig_psql = ml7c.psql
        try:
            ml7c.psql = lambda sql, text=True: "\n".join(
                f"story_{i},2026-11-07T00:00:00Z,Summary text {i}" for i in range(5)
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                ml7c.show(limit=5)
            out = buf.getvalue()
            for i in range(5):
                self.assertIn(f"Summary text {i}", out)
        finally:
            ml7c.psql = orig_psql


if __name__ == "__main__":
    unittest.main(verbosity=2)
