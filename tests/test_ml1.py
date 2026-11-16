#!/usr/bin/env python3
"""ML-1 anomaly detection tests: synthetic data only — no DB, no network.

    python3 tests/test_ml1.py
"""
import datetime as dt
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
spec = importlib.util.spec_from_file_location("ml1", OPS / "ml1.py")
ml1 = importlib.util.module_from_spec(spec)
sys.modules["ml1"] = ml1
spec.loader.exec_module(ml1)


def price_df(symbol, closes, opens=None, highs=None, lows=None, start="2025-01-02"):
    """Build a price dataframe with a given close series."""
    n = len(closes)
    idx = pd.bdate_range(start, periods=n)
    if opens is None:
        opens = closes * (1 + np.random.default_rng(0).normal(0, 0.005, n))
    if highs is None:
        highs = np.maximum(opens, closes) * (1 + np.abs(np.random.default_rng(1).normal(0, 0.003, n)))
    if lows is None:
        lows = np.minimum(opens, closes) * (1 - np.abs(np.random.default_rng(2).normal(0, 0.003, n)))
    return pd.DataFrame({
        "symbol": symbol, "ts": idx, "open": opens, "high": highs,
        "low": lows, "close": closes, "volume": 1_000_000,
    })


class BadBars(unittest.TestCase):
    def test_clean_prices_no_anomalies(self):
        closes = 100 + np.cumsum(np.random.default_rng(3).normal(0, 1, 100))
        df = price_df("AAA", closes)
        anom = ml1.detect_bad_bars(df)
        self.assertEqual(len(anom), 0)

    def test_jump_over_40pct_detected(self):
        closes = [100.0, 150.0]  # +50% jump
        df = price_df("BBB", closes)
        anom = ml1.detect_bad_bars(df)
        self.assertEqual(len(anom), 1)
        self.assertEqual(anom[0]["kind"], "bad_bar")
        self.assertEqual(anom[0]["subject"], "BBB")
        self.assertTrue("jump_" in anom[0]["details"]["reasons"][0])

    def test_non_positive_close_detected(self):
        closes = [100.0, 0.0]
        df = price_df("CCC", closes)
        anom = ml1.detect_bad_bars(df)
        self.assertEqual(len(anom), 1)
        self.assertEqual(anom[0]["details"]["reasons"][0], "non_positive_close")

    def test_ohlc_violation_detected(self):
        closes = [100.0, 101.0]
        opens = [100.0, 101.0]
        highs = [100.0, 99.0]  # high < close on day 2
        lows = [99.0, 100.0]
        df = price_df("DDD", closes, opens, highs, lows)
        anom = ml1.detect_bad_bars(df)
        self.assertGreaterEqual(len(anom), 1)
        reasons = [r["details"]["reasons"] for r in anom]
        self.assertTrue(any("high_below_open_close" in rs for rs in reasons))


class FeedDrops(unittest.TestCase):
    def test_fresh_feed_no_anomaly(self):
        hb = pd.DataFrame({
            "feed": ["fx", "rates"],
            "fresh": [True, True],
            "last_check": [pd.Timestamp.now(tz="UTC")] * 2,
            "last_data_ts": [pd.Timestamp.now(tz="UTC")] * 2,
            "rows_total": [1000, 2000],
            "max_age_hours": [24, 24],
        })
        anom = ml1.detect_feed_drops(hb)
        self.assertEqual(len(anom), 0)

    def test_stale_feed_detected(self):
        hb = pd.DataFrame({
            "feed": ["nse"],
            "fresh": [False],
            "last_check": [pd.Timestamp.now(tz="UTC")],
            "last_data_ts": [pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=3)],
            "rows_total": [500],
            "max_age_hours": [24],
        })
        anom = ml1.detect_feed_drops(hb)
        self.assertEqual(len(anom), 1)
        self.assertEqual(anom[0]["kind"], "feed_drop")
        self.assertEqual(anom[0]["subject"], "nse")


class RowCountDrops(unittest.TestCase):
    def test_stable_counts_no_anomaly(self):
        counts = pd.DataFrame({
            "d": pd.date_range("2025-01-01", periods=25, freq="D"),
            "cnt": [1000] * 25,
        })
        anom = ml1.detect_row_count_drops(counts)
        self.assertEqual(len(anom), 0)

    def test_sudden_drop_detected(self):
        counts = pd.DataFrame({
            "d": pd.date_range("2025-01-01", periods=22, freq="D"),
            "cnt": [1000] * 20 + [300, 1000],  # 300 < 70% of 1000 median; newest day is skipped
        })
        anom = ml1.detect_row_count_drops(counts)
        self.assertEqual(len(anom), 1)
        self.assertEqual(anom[0]["kind"], "row_count_drop")
        self.assertEqual(anom[0]["subject"], "stock_prices")
        self.assertLess(anom[0]["score"], 0.7)


class ScraperDrops(unittest.TestCase):
    def test_recent_feed_no_anomaly(self):
        now = pd.Timestamp.now(tz="UTC")
        hb = pd.DataFrame({
            "feed": ["active"],
            "last_data_ts": [now - pd.Timedelta(days=1)],
        })
        anom = ml1.detect_scraper_drops(hb, now=now)
        self.assertEqual(len(anom), 0)

    def test_silent_too_long_no_anomaly(self):
        now = pd.Timestamp.now(tz="UTC")
        hb = pd.DataFrame({
            "feed": ["dead"],
            "last_data_ts": [now - pd.Timedelta(days=30)],
        })
        anom = ml1.detect_scraper_drops(hb, now=now)
        self.assertEqual(len(anom), 0)

    def test_scraper_drop_detected(self):
        now = pd.Timestamp.now(tz="UTC")
        hb = pd.DataFrame({
            "feed": ["fading"],
            "last_data_ts": [now - pd.Timedelta(days=4)],  # between 2 and 7 days
        })
        anom = ml1.detect_scraper_drops(hb, now=now)
        self.assertEqual(len(anom), 1)
        self.assertEqual(anom[0]["kind"], "scraper_drop")
        self.assertEqual(anom[0]["subject"], "fading")


class RuleFixes20261112(unittest.TestCase):
    """False positives found in the first live run (2026-11-12)."""

    def test_market_holiday_not_a_drop(self):
        days = pd.date_range("2026-10-01", periods=25, freq="D")
        us = pd.DataFrame({"d": days, "cnt": 1500, "market": "US"})
        nse = pd.DataFrame({"d": days.delete(20), "cnt": 745, "market": "NSE"})  # NSE closed one day
        self.assertEqual(ml1.detect_row_count_drops(pd.concat([us, nse])), [])

    def test_partial_market_day_flagged_per_market(self):
        days = pd.date_range("2026-10-01", periods=25, freq="D")
        cnt = [745] * 25
        cnt[20] = 200
        df = pd.concat([pd.DataFrame({"d": days, "cnt": 1500, "market": "US"}),
                        pd.DataFrame({"d": days, "cnt": cnt, "market": "NSE"})])
        anom = ml1.detect_row_count_drops(df)
        self.assertEqual([a["subject"] for a in anom], ["stock_prices:NSE"])

    def test_weekly_feed_respects_its_max_age(self):
        now = pd.Timestamp("2026-11-12", tz="UTC")
        hb = pd.DataFrame({"feed": ["stock_fundamentals", "daily_feed"],
                           "last_data_ts": [now - pd.Timedelta(days=4)] * 2,
                           "max_age_hours": [192, 30]})
        anom = ml1.detect_scraper_drops(hb, now=now)
        self.assertEqual([a["subject"] for a in anom], ["daily_feed"])

    def test_recorded_split_not_a_jump(self):
        df = pd.DataFrame({"symbol": "X", "ts": pd.date_range("2026-09-01", periods=3, freq="D"),
                           "open": [100.0, 50.0, 51.0], "high": [101.0, 51.0, 52.0],
                           "low": [99.0, 49.0, 50.0], "close": [100.0, 50.0, 51.0],
                           "adj_close": [50.0, 50.0, 51.0]})   # 2:1 split recorded
        self.assertEqual(ml1.detect_bad_bars(df), [])
        unrecorded = df.assign(adj_close=df["close"])
        self.assertEqual(len(ml1.detect_bad_bars(unrecorded)), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
