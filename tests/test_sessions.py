"""sessions.py — never store a mid-session bar as a close (2026-11-12)."""
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "manifests" / "_common"))
import sessions as s  # noqa: E402


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


class Exchanges(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(s.exchange_of("RELIANCE.NS"), "NSE")
        self.assertEqual(s.exchange_of("AAPL"), "US")
        self.assertEqual(s.exchange_of("BRK-B"), "US")
        self.assertEqual(s.exchange_of("^HSI"), "HKEX")
        self.assertEqual(s.exchange_of("NIFTY_MICROCAP250.NS"), "NSE")
        self.assertIsNone(s.exchange_of("^UNKNOWN"))

    def test_every_live_index_ticker_is_mapped(self):
        import importlib.util, types
        root = Path(__file__).resolve().parent.parent
        for name in ("pandas", "yfinance", "requests", "psycopg"):
            sys.modules.setdefault(name, types.ModuleType(name))
        for p in ("manifests/ingestion/yfinance/yfinance-fetcher.py",
                  "manifests/ingestion/nse/nse-indices.py"):
            spec = importlib.util.spec_from_file_location(Path(p).stem.replace("-", "_"), root / p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            for t in list(m.TICKERS) + list(getattr(m, "NSE_DIRECT", {})):
                self.assertIsNotNone(s.exchange_of(t), t)


class SessionOpen(unittest.TestCase):
    def test_nse_mid_session_at_stock_prices_run(self):
        # stock-prices fires 23:30 PT = 07:30 UTC = 13:00 IST
        now = utc(2026, 11, 13, 7, 30)
        self.assertTrue(s.session_open("RELIANCE.NS", date(2026, 11, 13), now))
        self.assertFalse(s.session_open("RELIANCE.NS", date(2026, 11, 12), now))
        self.assertFalse(s.session_open("AAPL", date(2026, 11, 12), now))   # US closed 16:00 ET

    def test_buffer_after_close(self):
        self.assertTrue(s.session_open("^NSEI", date(2026, 11, 13), utc(2026, 11, 13, 10, 10)))
        self.assertFalse(s.session_open("^NSEI", date(2026, 11, 13), utc(2026, 11, 13, 10, 31)))

    def test_apac_at_yfinance_run(self):
        now = utc(2026, 11, 13, 6, 30)   # yfinance-fetcher 22:30 PT
        self.assertTrue(s.session_open("^HSI", date(2026, 11, 13), now))
        self.assertTrue(s.session_open("000001.SS", date(2026, 11, 13), now))
        self.assertTrue(s.session_open("^STI", date(2026, 11, 13), now))
        self.assertFalse(s.session_open("^N225", date(2026, 11, 13), utc(2026, 11, 13, 7, 1)))

    def test_future_dated_bar_is_open(self):
        self.assertTrue(s.session_open("AAPL", date(2026, 11, 14), utc(2026, 11, 13, 23, 0)))

    def test_us_dst_is_handled(self):
        # 16:15 EDT + 30 min = 20:45 UTC in July
        self.assertTrue(s.session_open("^VIX", date(2026, 7, 15), utc(2026, 7, 15, 20, 40)))
        self.assertFalse(s.session_open("^VIX", date(2026, 7, 15), utc(2026, 7, 15, 20, 50)))

    def test_unknown_ticker_never_dropped(self):
        self.assertFalse(s.session_open("^UNKNOWN", date(2030, 1, 1), utc(2026, 1, 1)))

    def test_closed_bars_filters_window(self):
        now = utc(2026, 11, 13, 6, 30)
        bars = [(datetime(2026, 11, d), 1.0) for d in (10, 11, 12, 13)]
        self.assertEqual([b[0].day for b in s.closed_bars("^HSI", bars, now)], [10, 11, 12])


if __name__ == "__main__":
    unittest.main()
