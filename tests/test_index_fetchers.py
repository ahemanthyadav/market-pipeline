"""Unit tests for the index fetchers' write path (no network, no database).

    python3 tests/test_index_fetchers.py

Both fetchers must commit one calendar year per transaction and delete only
the exact (ticker, ts) rows they are about to insert.
"""
import importlib.util
import os
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "manifests/_common"))  # sessions.py
FETCHERS = {
    "yfinance": ROOT / "manifests/ingestion/yfinance/yfinance-fetcher.py",
    "nse": ROOT / "manifests/ingestion/nse/nse-indices.py",
}


class FakeCursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args):
        self.log.append(("delete", sql, args))

    def executemany(self, sql, batch):
        self.log.append(("insert", sql, list(batch)))


class FakeConn:
    def __init__(self):
        self.log = []
        self.closed = False

    def transaction(self):
        self.log.append(("begin",))
        return FakeCursor(self.log)

    def cursor(self):
        return FakeCursor(self.log)

    def close(self):
        self.closed = True


def load(path, conn):
    """Import a fetcher with its heavy dependencies stubbed out."""
    for name in ("pandas", "yfinance", "requests"):
        sys.modules[name] = types.ModuleType(name)
    fake_pg = types.ModuleType("psycopg")
    fake_pg.connect = lambda **kw: conn
    sys.modules["psycopg"] = fake_pg
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bar(y, m, d, close=1.0):
    return (datetime(y, m, d), close, close, close, close, None)


ROWS = {
    "^A": [bar(2019, 12, 30), bar(2019, 12, 31), bar(2020, 1, 2)],
    "^B": [bar(2020, 1, 2), bar(1999, 6, 1)],
}


class WritePath(unittest.TestCase):
    def setUp(self):
        os.environ["PGPASSWORD"] = "x"
        os.environ.pop("BACKFILL", None)

    def each(self):
        for name, path in FETCHERS.items():
            conn = FakeConn()
            yield name, load(path, conn), conn

    def test_batches_are_one_per_year_oldest_first(self):
        for name, mod, _ in self.each():
            with self.subTest(name):
                batches = mod.year_batches(ROWS)
                self.assertEqual([y for y, _ in batches], [1999, 2019, 2020])
                self.assertEqual([len(b) for _, b in batches], [1, 2, 2])
                self.assertTrue(all(row[1].year == y for y, b in batches for row in b))

    def test_one_transaction_per_year(self):
        for name, mod, conn in self.each():
            with self.subTest(name):
                self.assertEqual(mod.write_rows(ROWS), 5)
                kinds = [entry[0] for entry in conn.log]
                self.assertEqual(kinds, ["begin", "delete", "insert"] * 3)
                self.assertTrue(conn.closed)

    def test_delete_names_exactly_the_rows_inserted(self):
        for name, mod, conn in self.each():
            with self.subTest(name):
                mod.write_rows(ROWS)
                deletes = [e for e in conn.log if e[0] == "delete"]
                inserts = [e for e in conn.log if e[0] == "insert"]
                for (_, sql, (tickers, stamps, low, high)), (_, _, batch) in zip(deletes, inserts):
                    self.assertEqual(list(zip(tickers, stamps)), [(r[0], r[1]) for r in batch])
                    self.assertEqual((low, high), (min(stamps), max(stamps)))
                    self.assertIn("p.ticker = k.ticker AND p.ts = k.ts", sql)

    def test_daily_run_is_a_single_small_batch(self):
        for name, mod, conn in self.each():
            with self.subTest(name):
                today = {"^A": [bar(2026, 10, 9)], "^B": [bar(2026, 10, 9)]}
                self.assertEqual(mod.write_rows(today), 2)
                self.assertEqual([e[0] for e in conn.log], ["begin", "delete", "insert"])


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": self._data}


class FakeSession:
    """Scripted nseindia.com session: records calls, serves canned EOD records."""
    def __init__(self, records):
        self.records = records
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if params is None:                      # cookie warm-up
            return FakeResponse([])
        return FakeResponse(self.records.get(params["indexType"], []))


def eod(day, close=100.0, full=True, qty=5):
    rec = {"EOD_TIMESTAMP": day, "EOD_CLOSE_INDEX_VAL": close}
    if full:
        rec.update({"EOD_OPEN_INDEX_VAL": close - 1, "EOD_HIGH_INDEX_VAL": close + 1,
                    "EOD_LOW_INDEX_VAL": close - 2, "HIT_TRADED_QTY": qty})
    return rec


class NseDirectLeg(unittest.TestCase):
    """v5 (2026-10-26): the NSE-direct leg for ^CNXAUTO/^CNXFMCG + MIDCAP150 history."""

    def setUp(self):
        self.conn = FakeConn()
        self.mod = load(FETCHERS["nse"], self.conn)
        for var in ("BACKFILL", "NSE_MIDCAP150", "NSE_DIRECT_ONLY", "FETCH_START"):
            os.environ.pop(var, None)

    def fake_requests(self, records):
        session = FakeSession(records)
        fake = types.ModuleType("requests")
        fake.Session = lambda: session
        self.mod.requests = fake
        return session

    def test_records_parse_full_and_close_only_rows(self):
        session = FakeSession({"NIFTY AUTO": [
            eod("09-OCT-2026", 24859.9),
            eod("03-JAN-2015", 8000.0, full=False),      # early close-only row
            {"EOD_TIMESTAMP": "", "EOD_CLOSE_INDEX_VAL": 1.0},   # junk: no date
        ]})
        from datetime import date as d
        bars = self.mod.nse_records(session, "NIFTY AUTO", d(2015, 1, 1), d(2026, 10, 10))
        self.assertEqual(len(bars), 2)
        ts, o, h, low, c, v = bars[0]
        self.assertEqual((ts.year, ts.month, ts.day, ts.hour), (2026, 10, 9, 0))
        self.assertIsNone(ts.tzinfo)   # naive like the Yahoo leg: one convention per batch
        self.assertEqual((o, c, v), (24858.9, 24859.9, 5))
        self.assertEqual(bars[1][1:], (None, None, None, 8000.0, None))  # close-only

    def test_daily_mode_covers_only_auto_and_fmcg(self):
        self.fake_requests({"NIFTY AUTO": [eod("09-OCT-2026")],
                            "NIFTY FMCG": [eod("09-OCT-2026")],
                            "NIFTY MIDCAP 150": [eod("09-OCT-2026")]})
        rows = self.mod.fetch_nse_direct()
        self.assertEqual(set(rows), {"^CNXAUTO", "^CNXFMCG"})

    def test_midcap_backfill_is_capped_before_yahoo_ownership(self):
        os.environ["BACKFILL"] = "1"
        os.environ["NSE_MIDCAP150"] = "1"
        os.environ["FETCH_START"] = "2018-12-01"
        self.mod.time.sleep = lambda s: None
        self.fake_requests({"NIFTY AUTO": [eod("14-DEC-2018")],
                            "NIFTY FMCG": [eod("14-DEC-2018")],
                            "NIFTY MIDCAP 150": [eod("11-JAN-2019"),
                                                 eod("14-JAN-2019"),    # Yahoo's first bar
                                                 eod("15-JAN-2019")]})
        rows = self.mod.fetch_nse_direct()
        midcap_days = [ts.date().isoformat() for ts, *_ in rows["NIFTYMIDCAP150.NS"]]
        self.assertEqual(midcap_days, ["2019-01-11"])   # strictly before 2019-01-14

    def test_tickers_never_collide_with_the_yahoo_list(self):
        self.assertFalse(set(self.mod.NSE_DIRECT) & set(self.mod.TICKERS))

    def test_range_sweep_survives_the_70_record_api_cap(self):
        """NSE serves at most ~70 records per call (found 2026-10-26: year asks
        came back Jan→mid-Apr). The sweep must still cover the whole span."""
        from datetime import date as d, datetime as dt, timedelta as td
        days = []
        day = d(2015, 1, 1)
        while day <= d(2015, 12, 31):
            if day.weekday() < 5:
                days.append(day)
            day += td(days=1)

        mod = self.mod

        class CappedSession:
            def get(self, url, params=None, timeout=None):
                a = dt.strptime(params["from"], "%d-%m-%Y").date()
                b = dt.strptime(params["to"], "%d-%m-%Y").date()
                window = [x for x in days if a <= x <= b][:70]   # the cap
                return FakeResponse([eod(x.strftime("%d-%b-%Y").upper()) for x in window])

        got = mod.nse_range(CappedSession(), "NIFTY AUTO", d(2015, 1, 1), d(2015, 12, 31))
        self.assertEqual(sorted(b[0].date() for b in got), days)   # full year, no gaps


if __name__ == "__main__":
    unittest.main()
