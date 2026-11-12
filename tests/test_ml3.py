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


try:
    import scipy  # noqa: F401
    HAVE_SCIPY = True
except ImportError:
    HAVE_SCIPY = False


def _simulate_gjr(n, omega, alpha, gamma, beta, seed):
    rng = np.random.default_rng(seed)
    r = np.empty(n)
    var = omega / (1 - alpha - 0.5 * gamma - beta)
    for t in range(n):
        r[t] = rng.standard_normal() * np.sqrt(var)
        var = omega + (alpha + gamma * (r[t] < 0)) * r[t] ** 2 + beta * var
    return r


def _frame(r, sym="AAA"):
    return pd.DataFrame({"symbol": [sym] * len(r),
                         "ts": pd.date_range("2025-01-01", periods=len(r), freq="D", tz="UTC"),
                         "ret": r})


class GarchRecursion(unittest.TestCase):
    """Pure pieces — run with or without scipy."""

    def test_cond_var_matches_hand_loop(self):
        r = np.array([0.01, -0.02, 0.005, -0.01, 0.03])
        om, a, g, b = 1e-6, 0.05, 0.10, 0.90
        v = [np.var(r)]
        for x in r:
            v.append(om + (a + g * (x < 0)) * x * x + b * v[-1])
        np.testing.assert_allclose(ml3c.cond_var(r, om, a, g, b), v, rtol=1e-12)

    def test_negative_return_raises_gjr_forecast_only(self):
        up, down = np.array([0.0, 0.02]), np.array([0.0, -0.02])
        self.assertEqual(ml3c.cond_var(up, 1e-6, .05, 0, .9)[-1], ml3c.cond_var(down, 1e-6, .05, 0, .9)[-1])
        self.assertGreater(ml3c.cond_var(down, 1e-6, .05, .1, .9)[-1], ml3c.cond_var(up, 1e-6, .05, .1, .9)[-1])

    def test_horizon_vol(self):
        self.assertAlmostEqual(ml3c.horizon_vol(4e-4, 1e-6, 0.95, 1), 0.02)
        # mean reversion: from ABOVE the long-run variance the h-day vol < sqrt(h) * 1-day vol
        self.assertLess(ml3c.horizon_vol(4e-4, 1e-6, 0.95, 5), 0.02 * np.sqrt(5))
        self.assertAlmostEqual(ml3c.horizon_vol(4e-4, 1e-6, 1.0, 4), 0.04)

    def test_no_scipy_emits_no_mislabelled_rows(self):
        saved = {k: sys.modules.get(k) for k in ("scipy", "scipy.optimize")}
        sys.modules["scipy"] = None
        sys.modules["scipy.optimize"] = None
        try:
            fc = ml3c.garch_forecast(_frame(np.random.default_rng(1).normal(0, .01, 200)))
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        self.assertEqual(len(fc), 0)
        self.assertEqual(fc.attrs["skipped"], 1)


@unittest.skipUnless(HAVE_SCIPY, "scipy not installed (runs in the marketdata-data image)")
class GarchFit(unittest.TestCase):
    def test_garch_labels_and_recovers_persistence(self):
        r = _simulate_gjr(1500, 2e-6, 0.08, 0.0, 0.90, seed=7)
        fc = ml3c.garch_forecast(_frame(r))
        self.assertEqual(list(fc.model), ["garch"])      # never "ewma"
        om, a, g, b = ml3c.fit_garch(r)
        self.assertEqual(g, 0.0)
        self.assertAlmostEqual(a + b, 0.98, delta=0.05)
        self.assertGreater(fc.forecast.iloc[0], 0)

    def test_gjr_detects_leverage(self):
        r = _simulate_gjr(2000, 2e-6, 0.02, 0.15, 0.88, seed=11)
        om, a, g, b = ml3c.fit_garch(r, gjr=True)
        self.assertGreater(g, a)                         # asymmetry recovered
        fc = ml3c.gjr_garch_forecast(_frame(r))
        self.assertEqual(list(fc.model), ["gjr_garch"])

    def test_short_history_skipped_not_relabelled(self):
        fc = ml3c.garch_forecast(_frame(np.random.default_rng(5).normal(0, .02, 30)))
        self.assertEqual(len(fc), 0)
        self.assertEqual(fc.attrs["skipped"], 1)


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
