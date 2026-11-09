#!/usr/bin/env python3
"""ML-3 volatility forecast tests: synthetic data only — no DB, no network.

    python3 tests/test_ml3.py
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
spec = importlib.util.spec_from_file_location("ml3", OPS / "ml3.py")
ml3 = importlib.util.module_from_spec(spec)
sys.modules["ml3"] = ml3
spec.loader.exec_module(ml3)

# Import compute functions directly from ml3_volatility for unit tests
ML = Path(__file__).resolve().parent.parent / "manifests" / "ml"
spec_ml3 = importlib.util.spec_from_file_location("ml3_volatility", ML / "ml3_volatility.py")
ml3c = importlib.util.module_from_spec(spec_ml3)
sys.modules["ml3_volatility"] = ml3c
spec_ml3.loader.exec_module(ml3c)


class EWMATests(unittest.TestCase):
    def test_ewma_forecast_shape(self):
        n = 100
        rets = pd.DataFrame({
            "symbol": ["AAA"] * n,
            "ts": pd.date_range("2026-01-01", periods=n, freq="D"),
            "ret": np.random.default_rng(1).normal(0, 0.02, n),
        })
        fc = ml3c.ewma_forecast(rets)
        self.assertEqual(len(fc), 1)
        self.assertEqual(fc.model.iloc[0], "ewma")
        self.assertEqual(fc.horizon.iloc[0], 1)
        self.assertGreater(fc.forecast.iloc[0], 0)

    def test_ewma_multiple_symbols(self):
        rets = pd.DataFrame({
            "symbol": ["AAA"] * 50 + ["BBB"] * 50,
            "ts": list(pd.date_range("2026-01-01", periods=50, freq="D")) * 2,
            "ret": np.random.default_rng(2).normal(0, 0.02, 100),
        })
        fc = ml3c.ewma_forecast(rets)
        self.assertEqual(len(fc), 2)
        self.assertSetEqual(set(fc.symbol), {"AAA", "BBB"})

    def test_ewma_horizon(self):
        n = 100
        rets = pd.DataFrame({
            "symbol": ["AAA"] * n,
            "ts": pd.date_range("2026-01-01", periods=n, freq="D"),
            "ret": np.random.default_rng(3).normal(0, 0.02, n),
        })
        fc_h1 = ml3c.ewma_forecast(rets, horizon=1)
        fc_h5 = ml3c.ewma_forecast(rets, horizon=5)
        # Horizon scales by sqrt(h)
        self.assertAlmostEqual(fc_h5.forecast.iloc[0], fc_h1.forecast.iloc[0] * np.sqrt(5))


class GARCHTests(unittest.TestCase):
    def test_garch_forecast_shape(self):
        n = 200
        rets = pd.DataFrame({
            "symbol": ["AAA"] * n,
            "ts": pd.date_range("2026-01-01", periods=n, freq="D"),
            "ret": np.random.default_rng(4).normal(0, 0.02, n),
        })
        fc = ml3c.garch_forecast(rets)
        self.assertEqual(len(fc), 1)
        self.assertIn(fc.model.iloc[0], ("garch", "ewma"))
        self.assertGreater(fc.forecast.iloc[0], 0)

    def test_garch_fallback_small_sample(self):
        n = 30  # below 50 threshold
        rets = pd.DataFrame({
            "symbol": ["AAA"] * n,
            "ts": pd.date_range("2026-01-01", periods=n, freq="D"),
            "ret": np.random.default_rng(5).normal(0, 0.02, n),
        })
        fc = ml3c.garch_forecast(rets)
        self.assertEqual(len(fc), 1)
        self.assertIn(fc.model.iloc[0], ("garch", "ewma"))
        self.assertGreater(fc.forecast.iloc[0], 0)


class GJRGARCHTests(unittest.TestCase):
    def test_gjr_garch_forecast_shape(self):
        n = 200
        rets = pd.DataFrame({
            "symbol": ["AAA"] * n,
            "ts": pd.date_range("2026-01-01", periods=n, freq="D"),
            "ret": np.random.default_rng(6).normal(0, 0.02, n),
        })
        fc = ml3c.gjr_garch_forecast(rets)
        self.assertEqual(len(fc), 1)
        self.assertIn(fc.model.iloc[0], ("gjr_garch", "garch", "ewma"))
        self.assertGreater(fc.forecast.iloc[0], 0)


class RunnerTests(unittest.TestCase):
    def test_fetch_returns_uses_adj_close(self):
        orig_psql = ml3.psql
        queries = []
        try:
            def fake_psql(sql, text=True):
                queries.append(sql)
                return "AAA,2026-01-01,100.0\nAAA,2026-01-02,102.0\n"
            ml3.psql = fake_psql
            df = ml3.fetch_returns(["AAA"], days=30)
            self.assertEqual(len(df), 1)
            self.assertAlmostEqual(df["ret"].iloc[0], 0.02)
            self.assertIn("COALESCE(adj_close, close)", queries[0])
        finally:
            ml3.psql = orig_psql

    def test_write_forecasts_batches_multi_row_insert(self):
        orig_psql = ml3.psql
        queries = []
        try:
            def fake_psql(sql, text=True):
                queries.append(sql)
                return "INSERT 0 3\n"
            ml3.psql = fake_psql
            rows = [
                {"symbol": f"S{i}", "ts": "2026-01-02T00:00:00Z", "model": "ewma", "horizon": 1, "forecast": 0.02}
                for i in range(3)
            ]
            n = ml3.write_forecasts(rows)
            self.assertEqual(n, 3)
            self.assertEqual(len(queries), 1)
        finally:
            ml3.psql = orig_psql

    def test_models_dict(self):
        self.assertIn("ewma", ml3.MODELS)
        self.assertIn("garch", ml3.MODELS)
        self.assertIn("gjr_garch", ml3.MODELS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
