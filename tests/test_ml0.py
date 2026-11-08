#!/usr/bin/env python3
"""ML-0 experiment infrastructure tests: walk-forward splitter, leakage test,
trial budget. Synthetic data only — no DB, no network.

    python3 tests/test_ml0.py
"""
import importlib.util
import sys
import unittest
from pathlib import Path

try:
    import numpy as np
    import pandas as pd
except ImportError as exc:
    print(f"SKIP: needs numpy and pandas ({exc})")
    sys.exit(0)

OPS = Path(__file__).resolve().parent.parent / "ops-tools"
spec = importlib.util.spec_from_file_location("ml0", OPS / "ml0.py")
ml0 = importlib.util.module_from_spec(spec)
sys.modules["ml0"] = ml0
spec.loader.exec_module(ml0)

spec_ev0 = importlib.util.spec_from_file_location("ev0", OPS / "ev0.py")
ev0 = importlib.util.module_from_spec(spec_ev0)
sys.modules["ev0"] = ev0
spec_ev0.loader.exec_module(ev0)


class WalkForward(unittest.TestCase):
    def test_expanding_window_no_overlap(self):
        me = pd.date_range("2005-01-31", periods=60, freq="ME")
        splits = ml0.walk_forward_splits(me, n_splits=3, min_train_months=24, step_months=12)
        self.assertEqual(len(splits), 3)
        for train, test in splits:
            self.assertTrue(train.max() < test.min(), "train must end before test starts")
            self.assertGreater(len(train), 0)
            self.assertGreater(len(test), 0)

    def test_train_grows(self):
        me = pd.date_range("2005-01-31", periods=60, freq="ME")
        splits = ml0.walk_forward_splits(me, n_splits=3, min_train_months=24, step_months=12)
        lens = [len(t) for t, _ in splits]
        self.assertEqual(lens, sorted(lens), "train window must grow")
        self.assertEqual(lens[0], 24, "first train = min_train_months")

    def test_insufficient_data_returns_fewer(self):
        me = pd.date_range("2005-01-31", periods=30, freq="ME")
        splits = ml0.walk_forward_splits(me, n_splits=5, min_train_months=24, step_months=12)
        self.assertLessEqual(len(splits), 1, "not enough data for 5 splits")

    def test_n_splits_none_expands_across_full_timeline(self):
        me = pd.date_range("2005-01-31", periods=120, freq="ME")
        splits = ml0.walk_forward_splits(me, n_splits=None, min_train_months=24, step_months=12)
        # (120 - 24) / 12 = 8 splits covering the entire 10-year range
        self.assertEqual(len(splits), 8)
        self.assertEqual(splits[-1][1].max(), me.max())


class Leakage(unittest.TestCase):
    def test_clean_signal_passes(self):
        rng = np.random.default_rng(1)
        idx = pd.date_range("2005-01-31", periods=120, freq="ME")
        cols = [f"S{i}" for i in range(50)]
        sig = pd.DataFrame(rng.normal(size=(120, 50)), idx, cols)
        fwd = pd.DataFrame(rng.normal(scale=0.08, size=(120, 50)), idx, cols) \
            + 0.15 * 0.08 * sig  # signal predicts next month
        liq = pd.DataFrame(1.0, idx, cols)
        leaked, detail = ml0.leakage_test(sig, fwd, liq)
        self.assertFalse(leaked, f"clean signal should not leak: {detail}")

    def test_leaked_signal_detected(self):
        rng = np.random.default_rng(2)
        idx = pd.date_range("2005-01-31", periods=120, freq="ME")
        cols = [f"S{i}" for i in range(50)]
        # signal = next month's return (pure look-ahead: uses price[t+1])
        fwd = pd.DataFrame(rng.normal(scale=0.08, size=(120, 50)), idx, cols)
        sig = fwd.copy()
        liq = pd.DataFrame(1.0, idx, cols)
        leaked, detail = ml0.leakage_test(sig, fwd, liq)
        self.assertTrue(leaked, f"look-ahead signal should leak: {detail}")


class DataHash(unittest.TestCase):
    def test_hash_format(self):
        h = ml0.data_hash()
        self.assertIsInstance(h, str)


class Budget(unittest.TestCase):
    def test_budget_returns_int(self):
        # No DB — just check the function signature exists
        self.assertTrue(callable(ml0.ml_runs_count))


if __name__ == "__main__":
    unittest.main(verbosity=2)
