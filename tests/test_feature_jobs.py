"""Hermetic tests for predictions.py, earnings_calendar.py, accuracy_check.py.

    python3 tests/test_feature_jobs.py

mdjob/yfinance/urllib targets are stubbed before import (house convention);
what is under test: rule-9 bounds on every hypertable SQL, the yfinance
calendar-shape handling, the Stooq CSV parse, and the horizon constants.
"""
import sys
import types
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "manifests" / "stock-screener" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "manifests" / "_common"))

# psycopg stub BEFORE mdjob import (house convention)
psycopg = types.ModuleType("psycopg")
psycopg.connect = lambda **kw: None
psycopg.Error = Exception
sys.modules["psycopg"] = psycopg

# yfinance stub BEFORE earnings_calendar import
yf_stub = types.ModuleType("yfinance")


class _Ticker:
    calendar = {}

    def __init__(self, symbol):
        self.symbol = symbol
        self.calendar = _Ticker.calendar


yf_stub.Ticker = _Ticker
sys.modules["yfinance"] = yf_stub

import accuracy_check  # noqa: E402
import earnings_calendar  # noqa: E402
import predictions  # noqa: E402


class PredictionsTest(unittest.TestCase):
    def test_horizons_and_benchmarks(self):
        self.assertEqual(predictions.HORIZONS, (30, 91, 182))
        self.assertEqual(predictions.BENCH["SP500"], "^GSPC")
        self.assertEqual(predictions.BENCH["NIFTY500"], "^CRSLDX")

    def test_every_hypertable_sql_is_bounded(self):
        for name in ("SNAP_SCORES_SQL", "SNAP_VAL_SQL", "PX_AT_SQL",
                     "BENCH_AT_SQL"):
            sql = getattr(predictions, name)
            self.assertIn("ts >=", sql, name)

    def test_resolution_date_math(self):
        ts = datetime(2026, 9, 1, tzinfo=timezone.utc)
        self.assertEqual(ts + timedelta(days=30),
                         datetime(2026, 10, 1, tzinfo=timezone.utc))


class EarningsCalendarTest(unittest.TestCase):
    def setUp(self):
        _Ticker.calendar = {}

    def test_dict_shape_future_dates_only(self):
        today = datetime.now(timezone.utc).date()
        _Ticker.calendar = {"Earnings Date": [today + timedelta(days=5),
                                              today - timedelta(days=5)]}
        self.assertEqual(earnings_calendar.next_dates("AAPL"),
                         [today + timedelta(days=5)])

    def test_empty_calendar_is_a_skip(self):
        _Ticker.calendar = {}
        self.assertEqual(earnings_calendar.next_dates("AAPL"), [])

    def test_dedupes_and_sorts(self):
        today = datetime.now(timezone.utc).date()
        d1, d2 = today + timedelta(days=9), today + timedelta(days=2)
        _Ticker.calendar = {"Earnings Date": [d1, d2, d2]}
        self.assertEqual(earnings_calendar.next_dates("X"), [d2, d1])


class AccuracyCheckTest(unittest.TestCase):
    @staticmethod
    def _with_body(body):
        def fake_urlopen(req, timeout=0):
            class R:
                @staticmethod
                def read():
                    return body
            return R()
        return fake_urlopen

    def test_fred_csv_parse_skips_holiday_dots(self):
        body = b"DATE,SP500\n2026-10-22,7663.43\n2026-10-23,.\n2026-10-24,7623.68\n"
        orig = accuracy_check.urllib.request.urlopen
        accuracy_check.urllib.request.urlopen = self._with_body(body)
        try:
            out = accuracy_check.fred_sp500()
        finally:
            accuracy_check.urllib.request.urlopen = orig
        self.assertEqual(out[date(2026, 10, 22)], 7663.43)
        self.assertNotIn(date(2026, 10, 23), out)
        self.assertEqual(len(out), 2)

    def test_fred_network_failure_is_empty(self):
        def boom(req, timeout=0):
            raise OSError("no route")
        orig = accuracy_check.urllib.request.urlopen
        accuracy_check.urllib.request.urlopen = boom
        try:
            self.assertEqual(accuracy_check.fred_sp500(), {})
        finally:
            accuracy_check.urllib.request.urlopen = orig

    def test_internal_sqls_are_bounded(self):
        for name in ("VAL_SQL", "SECTOR_SQL", "BENCH_SQL", "STOCK_SAMPLE_SQL", "FRESH_SQL"):
            self.assertIn("ts >=", getattr(accuracy_check, name), name)

    def test_per_stock_second_source_skips_without_api_key(self):
        import os
        orig = os.environ.pop("TIINGO_API_KEY", None)
        try:
            self.assertEqual(accuracy_check.per_stock_second_source(None), (0, []))
        finally:
            if orig is not None:
                os.environ["TIINGO_API_KEY"] = orig


class UniverseExpansionTest(unittest.TestCase):
    """ADR 0029: five universes, two markets."""

    def test_prediction_benchmarks_cover_all_universes(self):
        for u in ("SP500", "SP400", "SP600", "NIFTY500", "NIFTYMICROCAP250"):
            self.assertIn(u, predictions.BENCH)

    def test_valuation_market_mapping(self):
        import valuation
        self.assertEqual(valuation.market("SP400"), "US")
        self.assertEqual(valuation.market("NIFTYMICROCAP250"), "IN")
        for m in ("US", "IN"):
            self.assertIn(m, valuation.TENOR)
            self.assertIn(m, valuation.FALLBACK_10Y)


class NseUniverseDummyFilter(unittest.TestCase):
    """2026-11-12: NSE index files carry DUMMY placeholder rows after demergers."""

    def test_dummy_rows_skipped(self):
        import universe_refresh as ur
        csv_text = ("Company Name,Industry,Symbol,Series,ISIN Code\n"
                    "HEG Ltd.,Capital Goods,HEG,EQ,INE545A01024\n"
                    "Dummy HEG,Capital Goods,DUMMYHEG,EQ,DUMMY0000001\n"
                    "Infra Co,Construction,INXGN,BE,INE000000001\n")

        class Resp:
            text = csv_text
            headers = {"content-type": "text/csv"}

            def raise_for_status(self):
                pass

        real = ur.requests.get
        ur.requests.get = lambda *a, **k: Resp()
        try:
            rows = ur.nse_universe("NIFTY500", "http://x", None)
        finally:
            ur.requests.get = real
        self.assertEqual([r[1] for r in rows], ["HEG.NS"])


if __name__ == "__main__":
    unittest.main()
