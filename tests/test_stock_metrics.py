#!/usr/bin/env python3
"""Tests for stock_metrics.bars_to_write — which bars get a metric row in a run.

Before 2026-11-26 rows were written at a symbol's newest bar only, so a symbol whose bar arrived
one run late never got a row for the day it skipped.

    python3 tests/test_stock_metrics.py
"""
import sys
import types
import unittest
from pathlib import Path

sys.modules.setdefault("psycopg", types.ModuleType("psycopg"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "manifests" / "stock-screener" / "scripts"))
import stock_metrics as sm  # noqa: E402


class BarsToWrite(unittest.TestCase):
    def test_newest_only_when_nothing_is_missing(self):
        self.assertEqual(sm.bars_to_write([1, 2, 3, 4, 5, 6], have_ts=[2, 3, 4, 5, 6]), [6])

    def test_newest_is_always_rewritten(self):
        self.assertEqual(sm.bars_to_write([1, 2, 3], have_ts=[1, 2, 3]), [3])

    def test_a_skipped_day_is_filled(self):
        # bar 5 arrived a run late: rows exist for 4 and 6 (old newest), not for 5
        self.assertEqual(sm.bars_to_write([1, 2, 3, 4, 5, 6, 7], have_ts=[3, 4, 6]), [5, 7])

    def test_only_the_heal_window_is_looked_at(self):
        self.assertEqual(sm.bars_to_write(list(range(1, 11)), have_ts=[], heal=3), [8, 9, 10])

    def test_existing_older_rows_are_left_alone(self):
        out = sm.bars_to_write([1, 2, 3, 4, 5], have_ts=[1, 2, 3, 4])
        self.assertNotIn(4, out)

    def test_short_history(self):
        self.assertEqual(sm.bars_to_write([1], have_ts=[]), [1])

    def test_truncated_compute_matches_a_run_on_that_day(self):
        import pandas as pd
        idx = pd.date_range("2026-01-01", periods=300, freq="B", tz="UTC")
        df = pd.DataFrame({"close": [100 + i * 0.5 for i in range(300)], "volume": 1000,
                           "adj_close": [100 + i * 0.5 for i in range(300)]}, index=idx)
        t = idx[-2]
        healed = sm.compute(df.loc[:t].tail(sm.BARS))
        as_run_that_day = sm.compute(df.iloc[:-1].tail(sm.BARS))
        self.assertEqual(healed, as_run_that_day)
        self.assertNotEqual(healed["close"], sm.compute(df)["close"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
