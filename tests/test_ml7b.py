#!/usr/bin/env python3
"""ML-7b per-symbol narration: the wall-clock budget. No network, no DB.

    python3 tests/test_ml7b.py
"""
import re
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.modules.setdefault("psycopg", types.ModuleType("psycopg"))
sys.path.insert(0, str(ROOT / "manifests" / "llm"))
sys.path.insert(0, str(ROOT / "manifests" / "telegram"))   # factcheck.py
import ml7b_per_symbol as m  # noqa: E402


class Budget(unittest.TestCase):
    def test_stops_while_one_more_symbol_still_fits(self):
        self.assertFalse(m.out_of_budget(0, budget=3000, worst=367))
        self.assertFalse(m.out_of_budget(2633, budget=3000, worst=367))
        self.assertTrue(m.out_of_budget(2634, budget=3000, worst=367))

    def test_worst_case_covers_both_attempts(self):
        self.assertGreaterEqual(m.WORST_SYMBOL_SECONDS, 2 * 180)

    def test_job_deadline_sits_above_the_script_budget(self):
        """The Job must outlive the script's own stop, or the kill comes mid-symbol."""
        y = (ROOT / "manifests" / "llm" / "ml7b-cronjob.yaml").read_text()
        deadline = int(re.search(r"^\s*activeDeadlineSeconds:\s*(\d+)", y, re.M).group(1))
        budget = int(re.search(r"name: BUDGET_SECONDS.*?value: \"(\d+)\"", y, re.S).group(1))
        self.assertLess(budget, deadline)
        self.assertGreater(deadline - budget, 180)   # room for an in-flight gateway call


if __name__ == "__main__":
    unittest.main(verbosity=1)
