#!/usr/bin/env python3
"""History backfills H-1..H-5: pre-live filtering, weekly snapshots, and that the vectorised TA history equals
the live ta-compute.compute() bar for bar. No network, no DB.

    python3 tests/test_history_backfill.py
"""
import importlib.util
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

try:
    import numpy as np
    import pandas as pd
except ImportError as exc:
    print(f"SKIP: {exc}")
    sys.exit(0)

ROOT = Path(__file__).resolve().parent.parent
sys.modules.setdefault("psycopg", types.ModuleType("psycopg"))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hb = load(ROOT / "manifests/ingestion/history/history_backfill.py", "hb")
ta = load(ROOT / "manifests/ingestion/ta/ta-compute.py", "ta_compute")
UTC = timezone.utc


class PreLive(unittest.TestCase):
    def test_only_dates_before_first_live_row_and_utc_midnight(self):
        pts = [(pd.Timestamp("2026-09-27"), 1.0), (pd.Timestamp("2026-09-28 16:00"), 2.0),
               (pd.Timestamp("2026-09-29"), 3.0), (pd.Timestamp("2026-09-26"), float("nan"))]
        rows = hb.pre_live(pts, datetime(2026, 9, 29, tzinfo=UTC))
        self.assertEqual(rows, [(datetime(2026, 9, 27, tzinfo=UTC), 1.0), (datetime(2026, 9, 28, tzinfo=UTC), 2.0)])

    def test_no_live_rows_means_everything(self):
        self.assertEqual(len(hb.pre_live([(pd.Timestamp("2000-01-03"), 5.0)], None)), 1)


class Weekly(unittest.TestCase):
    def test_last_bar_of_each_week(self):
        idx = pd.DatetimeIndex(pd.bdate_range("2026-09-01", "2026-09-18", tz="UTC"))   # Tue 1st .. Fri 18th
        self.assertEqual([idx[p].strftime("%a %d") for p in hb.weekly_ends(idx)], ["Fri 04", "Fri 11", "Fri 18"])


class TaMatchesLive(unittest.TestCase):
    def test_every_bar_equals_live_compute(self):
        rng = np.random.default_rng(7)
        close = pd.Series(1000 * np.exp(np.cumsum(rng.normal(0, 0.01, 400))),
                          index=pd.bdate_range("2024-01-01", periods=400, tz="UTC"))
        hist = hb.ta_series(close, ta.rsi_wilder)
        for n in (60, 200, 400):                     # the live job on day n sees bars [0, n)
            live = ta.compute(pd.DataFrame({"close": close.iloc[:n]}))
            for name, v in live.items():
                self.assertAlmostEqual(hist[name].iloc[n - 1], v, places=9, msg=(n, name))


if __name__ == "__main__":
    unittest.main(verbosity=1)
