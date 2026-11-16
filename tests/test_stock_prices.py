#!/usr/bin/env python3
"""Tests for stock_prices.py (DQ-1 split resync, ADR 0013 timestamps).

No database, no network: psycopg/yfinance are stubbed, yf.download / Ticker.history are fakes.

    python3 tests/test_stock_prices.py
"""
import contextlib
import datetime as dt
import io
import os
import sys
import types
import unittest
from pathlib import Path

try:
    import pandas as pd
except ImportError as exc:
    print(f"SKIP: needs pandas ({exc})")
    sys.exit(0)

for name in ("psycopg", "yfinance"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["psycopg"].OperationalError = type("OperationalError", (Exception,), {})

SCRIPTS = Path(__file__).resolve().parent.parent / "manifests" / "stock-screener" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent.parent / "_common"))  # sessions.py
import stock_prices as sp  # noqa: E402

UTC = dt.timezone.utc


def bars(dates, splits=None, tz=None, close=10.0):
    idx = pd.DatetimeIndex(pd.to_datetime(dates)).tz_localize(tz) if tz else pd.DatetimeIndex(pd.to_datetime(dates))
    df = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 100.0}, index=idx)
    df["Stock Splits"] = 0.0
    for d, r in (splits or {}).items():
        df.loc[pd.Timestamp(d).tz_localize(tz) if tz else pd.Timestamp(d), "Stock Splits"] = r
    return df


class FakeCursor:
    rowcount = 0

    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=()):
        self.log.append((" ".join(sql.split()), params))

    def fetchall(self):
        return []

    def fetchone(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self):
        self.log = []

    @contextlib.contextmanager
    def transaction(self):
        yield

    def cursor(self):
        return FakeCursor(self.log)

    def close(self):
        pass


class TradeTs(unittest.TestCase):
    def test_naive_index_is_midnight_utc(self):
        self.assertEqual(sp.trade_ts(pd.Timestamp("2026-10-02")), dt.datetime(2026, 10, 2, tzinfo=UTC))

    def test_exchange_local_midnight_keeps_trade_date(self):
        # Ticker.history() style: NSE bar = local midnight = 18:30 UTC of the previous day
        self.assertEqual(sp.trade_ts(pd.Timestamp("2026-09-07", tz="Asia/Kolkata")),
                         dt.datetime(2026, 9, 7, tzinfo=UTC))
        self.assertEqual(sp.trade_ts(pd.Timestamp("2026-10-01", tz="America/New_York")),
                         dt.datetime(2026, 10, 1, tzinfo=UTC))

    def test_write_symbol_stamps_midnight_utc(self):
        conn = FakeConn()
        df = bars(["2026-09-07", "2026-09-08"], tz="Asia/Kolkata")
        self.assertEqual(sp.write_symbol(conn, "TCS.NS", df, "j"), 2)
        stamps = {p[1] for q, p in conn.log if q.startswith("INSERT INTO stock_prices")}
        self.assertEqual(stamps, {dt.datetime(2026, 9, 7, tzinfo=UTC), dt.datetime(2026, 9, 8, tzinfo=UTC)})
        deletes = [p for q, p in conn.log if q.startswith("DELETE FROM stock_prices")]
        self.assertEqual(len(deletes), 2)  # idempotent DELETE+INSERT per (symbol, ts)


class OpenSession(unittest.TestCase):
    """2026-11-12: stock-prices fires 13:00 IST — today's NSE bar must not be stored."""

    def test_mid_session_nse_bar_skipped_us_kept(self):
        import sessions
        real = sp.session_open
        now = dt.datetime(2026, 11, 13, 7, 30, tzinfo=UTC)
        sp.session_open = lambda sym, d: sessions.session_open(sym, d, now)
        try:
            conn = FakeConn()
            df = bars(["2026-11-12", "2026-11-13"], tz="Asia/Kolkata")
            self.assertEqual(sp.write_symbol(conn, "TCS.NS", df, "j"), 1)
            stamps = {p[1] for q, p in conn.log if q.startswith("INSERT INTO stock_prices")}
            self.assertEqual(stamps, {dt.datetime(2026, 11, 12, tzinfo=UTC)})
            conn = FakeConn()
            df = bars(["2026-11-11", "2026-11-12"], tz="America/New_York")
            self.assertEqual(sp.write_symbol(conn, "AAPL", df, "j"), 2)
        finally:
            sp.session_open = real


class PhantomBars(unittest.TestCase):
    """2026-11-13: zero-volume carry-forward bars on NSE holidays are not stored."""

    def test_rule(self):
        self.assertTrue(sp.phantom_bar(0, 100.0, 100.0))
        self.assertFalse(sp.phantom_bar(0, 101.0, 100.0))      # price moved: keep (old close-only data)
        self.assertFalse(sp.phantom_bar(None, 100.0, 100.0))   # unknown volume is not zero
        self.assertFalse(sp.phantom_bar(float("nan"), 100.0, 100.0))
        self.assertFalse(sp.phantom_bar(0, 100.0, None))       # first bar of the window
        self.assertFalse(sp.phantom_bar(500, 100.0, 100.0))

    def test_holiday_bar_deleted_not_inserted(self):
        conn = FakeConn()
        df = bars(["2026-10-01", "2026-10-02", "2026-10-03"], tz="Asia/Kolkata")
        df["Close"] = [448.4, 448.4, 452.0]
        df["Volume"] = [1000, 0, 900]
        self.assertEqual(sp.write_symbol(conn, "AADHARHFC.NS", df, "j"), 2)
        ins = {p[1].day for q, p in conn.log if q.startswith("INSERT INTO stock_prices")}
        dels = {p[1].day for q, p in conn.log if q.startswith("DELETE FROM stock_prices")}
        self.assertEqual(ins, {1, 3})
        self.assertIn(2, dels)   # a stored phantom is cleaned by the rewrite


class SplitEvents(unittest.TestCase):
    def test_detects_nonzero_split(self):
        ev = sp.split_events(bars(["2026-09-30", "2026-10-01"], splits={"2026-10-01": 6.665}))
        self.assertEqual([(d.date().isoformat(), r) for d, r in ev], [("2026-10-01", 6.665)])

    def test_none_zero_nan_and_one_are_not_splits(self):
        df = bars(["2026-09-30", "2026-10-01", "2026-10-02"])
        df["Stock Splits"] = [0.0, float("nan"), 1.0]
        self.assertEqual(sp.split_events(df), [])

    def test_missing_column_or_frame(self):
        self.assertEqual(sp.split_events(None), [])
        self.assertEqual(sp.split_events(bars(["2026-10-01"]).drop(columns=["Stock Splits"])), [])


class DailyRun(unittest.TestCase):
    """main() in daily mode with the split symbol re-fetched in full."""

    def run_main(self, universe, download_df, history, env=None, stored=None):
        calls = {"download_kwargs": None, "history": []}
        conn = FakeConn()

        class Cur(FakeCursor):
            def fetchall(s):
                if "universe_constituents" in s.log[-1][0]:
                    return [(x,) for x in universe]
                return (stored or {}).get(s.log[-1][1][0] if s.log[-1][1] else None, [])

            def fetchone(s):
                return None

        class C(FakeConn):
            def cursor(s):
                return Cur(s.log)

        conn = C()
        yf = sys.modules["yfinance"]

        def download(tickers, **kw):
            calls["download_kwargs"] = kw
            return {t: download_df[t] for t in tickers.split()} if len(tickers.split()) > 1 else download_df[tickers]

        class Ticker:
            def __init__(s, sym):
                s.sym = sym

            def history(s, **kw):
                calls["history"].append((s.sym, kw))
                return history[s.sym]

        yf.download, yf.Ticker = download, Ticker
        sp.connect = lambda: conn
        sp.time.sleep = lambda *_: None
        old = dict(os.environ)
        os.environ.update({"BACKFILL": "", "UNIVERSE_LIMIT": "0", **(env or {})})
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                rc = sp.main()
        finally:
            os.environ.clear()
            os.environ.update(old)
        return rc, out.getvalue(), conn, calls

    def test_split_symbol_is_resynced_in_full_others_are_not(self):
        recent = {"CTVA": bars(["2026-10-12", "2026-10-13"], splits={"2026-10-13": 6.665}),
                  "AAPL": bars(["2026-10-12", "2026-10-13"])}
        full = bars(["2026-09-01", "2026-09-02", "2026-10-12", "2026-10-13"], tz="America/New_York")
        rc, out, conn, calls = self.run_main(["AAPL", "CTVA"], recent, {"CTVA": full})
        self.assertEqual(rc, 0)
        self.assertTrue(calls["download_kwargs"]["actions"])
        self.assertEqual([c[0] for c in calls["history"]], ["CTVA"])
        self.assertEqual(calls["history"][0][1]["period"], "max")
        self.assertIn("SPLIT-RESYNC CTVA 2026-10-13x6.665 rows=4", out)
        self.assertIn("splits_resynced=1", out)
        self.assertTrue(out.strip().splitlines()[-1].startswith("PRICES-OK mode=daily"))
        stamps = {p[1] for q, p in conn.log if q.startswith("INSERT INTO stock_prices") and p[0] == "CTVA"}
        self.assertTrue(all(t.hour == 0 and t.minute == 0 and t.tzinfo == UTC for t in stamps))
        self.assertIn(dt.datetime(2026, 9, 1, tzinfo=UTC), stamps)

    def test_no_splits_means_no_history_fetch(self):
        recent = {"AAPL": bars(["2026-10-13"])}
        rc, out, conn, calls = self.run_main(["AAPL"], recent, {})
        self.assertEqual(rc, 0)
        self.assertEqual(calls["history"], [])
        self.assertIn("splits_resynced=0", out)

    def test_resync_is_capped(self):
        syms = ["S1", "S2", "S3"]
        recent = {s: bars(["2026-10-13"], splits={"2026-10-13": 2.0}) for s in syms}
        full = {s: bars(["2026-10-12", "2026-10-13"]) for s in syms}
        rc, out, conn, calls = self.run_main(syms, recent, full, env={"SPLIT_RESYNC_MAX": "2"})
        self.assertEqual(len(calls["history"]), 2)
        self.assertIn("WARN 3 symbols show splits (> 2)", out)
        self.assertIn("splits_resynced=2", out)

    def test_failed_resync_does_not_fail_the_run(self):
        recent = {"CTVA": bars(["2026-10-13"], splits={"2026-10-13": 6.665})}
        rc, out, conn, calls = self.run_main(["CTVA"], recent, {"CTVA": pd.DataFrame()})
        self.assertEqual(rc, 0)
        self.assertIn("WARN split resync CTVA", out)
        self.assertIn("splits_resynced=0", out)

    def test_dividend_is_recorded_but_never_triggers_resync(self):
        df = bars(["2026-10-12", "2026-10-13"])
        df["Dividends"] = [0.0, 0.26]
        rc, out, conn, calls = self.run_main(["AAPL"], {"AAPL": df}, {})
        self.assertEqual(rc, 0)
        self.assertEqual(calls["history"], [])  # no full-history refetch for a dividend
        self.assertIn("splits_resynced=0", out)
        ca = [p for q, p in conn.log if q.startswith("INSERT INTO corporate_actions")]
        self.assertEqual(ca, [("AAPL", dt.datetime(2026, 10, 13, tzinfo=UTC), "dividend", None, 0.26)])

    def test_split_recorded_as_corporate_action_with_ratio(self):
        recent = {"CTVA": bars(["2026-10-12", "2026-10-13"], splits={"2026-10-13": 6.665})}
        full = bars(["2026-10-12", "2026-10-13"])
        rc, out, conn, calls = self.run_main(["CTVA"], recent, {"CTVA": full})
        ca = [p for q, p in conn.log if q.startswith("INSERT INTO corporate_actions")]
        self.assertEqual(ca, [("CTVA", dt.datetime(2026, 10, 13, tzinfo=UTC), "split", 6.665, None)])

    def test_adj_close_starts_at_close_never_yahoo_adj_close(self):
        conn = FakeConn()
        df = bars(["2026-10-13"])
        df["Adj Close"] = 9.0
        sp.write_symbol(conn, "AAPL", df, "j")
        ins = [p for q, p in conn.log if q.startswith("INSERT INTO stock_prices")]
        self.assertEqual(ins[0][5:7], (10.0, 10.0))  # close, adj_close — not Yahoo's 9.0

    def test_dividend_in_window_triggers_adj_recompute(self):
        df = bars(["2026-10-12", "2026-10-13"])
        df["Dividends"] = [0.0, 0.26]
        stored = {"AAPL": [(dt.datetime(2026, 10, 12, tzinfo=UTC), 10.0)]}
        rc, out, conn, calls = self.run_main(["AAPL"], {"AAPL": df}, {}, stored=stored)
        self.assertTrue(any(q.startswith("UPDATE stock_prices p SET adj_close") for q, p in conn.log))

    def test_no_dividend_no_recompute(self):
        rc, out, conn, calls = self.run_main(["AAPL"], {"AAPL": bars(["2026-10-13"])}, {})
        self.assertFalse(any(q.startswith("UPDATE stock_prices p SET adj_close") for q, p in conn.log))

    def test_restated_history_is_resynced_without_split_flag(self):
        days = ["2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"]
        stored = {"MSFT": [(dt.datetime(2026, 10, 7, tzinfo=UTC), 20.0)]}  # Yahoo now says 10.0
        full = bars(days)
        rc, out, conn, calls = self.run_main(["MSFT"], {"MSFT": bars(days)}, {"MSFT": full},
                                             stored=stored)
        self.assertIn("SPLIT-RESYNC MSFT restated", out)
        kinds = {p[2] for q, p in conn.log if q.startswith("INSERT INTO price_anomalies")}
        self.assertEqual(kinds, {"history_restated"})


class AdjSeries(unittest.TestCase):
    D = [dt.datetime(2026, 1, d, tzinfo=UTC) for d in (5, 6, 7, 8)]

    def test_no_dividends_equals_close(self):
        self.assertEqual(sp.adj_series([(d, 10.0) for d in self.D], []), [10.0] * 4)

    def test_dividend_scales_only_earlier_bars(self):
        bars_ = [(self.D[0], 100.0), (self.D[1], 100.0), (self.D[2], 98.0), (self.D[3], 99.0)]
        adj = sp.adj_series(bars_, [(self.D[2], 2.0)])  # ex-date D2, prev close 100 -> f=0.98
        self.assertAlmostEqual(adj[0], 98.0)
        self.assertAlmostEqual(adj[1], 98.0)
        self.assertEqual(adj[2:], [98.0, 99.0])  # on/after ex-date: unchanged

    def test_factors_compound_and_bad_dividends_are_skipped(self):
        bars_ = [(d, 100.0) for d in self.D]
        adj = sp.adj_series(bars_, [(self.D[1], 1.0), (self.D[3], 1.0), (self.D[0], 5.0),
                                    (self.D[2], 500.0)])  # D0: no earlier bar; D2: >= close
        self.assertAlmostEqual(adj[0], 100 * 0.99 * 0.99)
        self.assertAlmostEqual(adj[2], 99.0)
        self.assertEqual(adj[3], 100.0)


class Anomalies(unittest.TestCase):
    D = [dt.datetime(2026, 10, d, tzinfo=UTC) for d in (7, 8, 9, 12, 13)]

    def test_jump_flagged_against_stored_previous_close(self):
        out = sp.find_anomalies({}, 100.0, [(self.D[0], 50.0), (self.D[1], 51.0)])
        self.assertEqual([(o[0], o[1]) for o in out], [(self.D[0], "jump_gt_40pct")])

    def test_newest_bars_may_change_without_restatement(self):
        fetched = [(d, 10.0) for d in self.D]
        stored = {self.D[3]: 11.0, self.D[4]: 12.0}  # the two newest: partial-bar territory
        self.assertEqual(sp.find_anomalies(stored, 10.0, fetched), [])

    def test_small_differences_are_not_restatements(self):
        fetched = [(d, 10.0) for d in self.D]
        self.assertEqual(sp.find_anomalies({self.D[0]: 10.04}, 10.0, fetched), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
