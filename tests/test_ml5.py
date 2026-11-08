#!/usr/bin/env python3
"""ML-5 peer cluster tests: synthetic data only — no DB, no network.

    python3 tests/test_ml5.py
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
spec = importlib.util.spec_from_file_location("ml5", OPS / "ml5.py")
ml5 = importlib.util.module_from_spec(spec)
sys.modules["ml5"] = ml5
spec.loader.exec_module(ml5)


class CorrelationClusters(unittest.TestCase):
    def test_cluster_shape(self):
        # Create two clearly-separated groups of correlated returns
        n = 100
        rng = np.random.default_rng(7)
        group_a = rng.normal(0, 0.02, n)
        group_b = rng.normal(0, 0.02, n)
        data = {}
        for i in range(5):
            data[f"A{i}"] = group_a + rng.normal(0, 0.005, n)  # highly correlated within group A
            data[f"B{i}"] = group_b + rng.normal(0, 0.005, n)  # highly correlated within group B
        idx = pd.date_range("2026-01-01", periods=n, freq="D")
        px_wide = pd.DataFrame(data, idx)

        clusters = ml5.correlation_clusters(px_wide, n_clusters=2)
        self.assertEqual(len(clusters), 10)
        # All symbols should be assigned a cluster
        self.assertTrue(all(c >= 1 for c in clusters.values()))

    def test_cluster_deterministic(self):
        n = 100
        rng = np.random.default_rng(8)
        data = {f"S{i}": rng.normal(0, 0.02, n) for i in range(10)}
        idx = pd.date_range("2026-01-01", periods=n, freq="D")
        px_wide = pd.DataFrame(data, idx)

        c1 = ml5.correlation_clusters(px_wide.copy(), n_clusters=3)
        c2 = ml5.correlation_clusters(px_wide.copy(), n_clusters=3)
        self.assertEqual(c1, c2, "clustering should be deterministic")


class RunnerTests(unittest.TestCase):
    def test_fetch_universe_symbols_callable(self):
        self.assertTrue(callable(ml5.fetch_universe_symbols))

    def test_fetch_returns_uses_adj_close_and_write_clusters_batches(self):
        orig_psql = ml5.psql
        queries = []
        try:
            def fake_psql(sql, text=True):
                queries.append(sql)
                if "SELECT" in sql:
                    return "AAA,2026-01-01,100.0\nAAA,2026-01-02,105.0\n"
                return "INSERT 0 2\n"
            ml5.psql = fake_psql
            df = ml5.fetch_returns(["AAA"], days=30)
            self.assertEqual(len(df), 1)
            self.assertAlmostEqual(df["ret"].iloc[0], 0.05)
            self.assertIn("COALESCE(adj_close, close)", queries[0])

            n = ml5.write_clusters([
                {"symbol": "AAA", "cluster": 1, "method": "correlation", "as_of": "2026-01-02"},
                {"symbol": "BBB", "cluster": 1, "method": "correlation", "as_of": "2026-01-02"},
            ])
            self.assertEqual(n, 2)
            self.assertEqual(len(queries), 2)
        finally:
            ml5.psql = orig_psql


if __name__ == "__main__":
    unittest.main(verbosity=2)
