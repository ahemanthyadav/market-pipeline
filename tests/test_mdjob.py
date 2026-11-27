#!/usr/bin/env python3
"""Tests for manifests/_common/mdjob.py — the shared job helpers (AR-2/AR-3)
ahead of their per-image adoption.

Proven here, with psycopg stubbed (no DB, no network):
  * write_window deletes ONLY its own (key_cols) rows before inserting (rule 4);
  * re-running the same window issues the same deletes — idempotent by shape;
  * the OperationalError retry closes the broken connection, rebuilds, and hands
    the LIVE connection back as (written, conn);
  * retry=False re-raises instead of reconnecting;
  * job_run records ok/fail and never lets bookkeeping failures break the job.

    python3 tests/test_mdjob.py
"""
import sys
import types
import unittest
from pathlib import Path


class FakeOperationalError(Exception):
    pass


# Stub psycopg before importing the module under test (house pattern).
psycopg_stub = types.ModuleType("psycopg")
psycopg_stub.OperationalError = FakeOperationalError
psycopg_stub.connect = lambda **kw: (_ for _ in ()).throw(AssertionError("unexpected real connect"))
sys.modules["psycopg"] = psycopg_stub

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "manifests" / "_common"))
import mdjob  # noqa: E402


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        if self.conn.broken:
            raise FakeOperationalError("connection is closed")
        self.conn.statements.append((sql, list(params or [])))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, broken=False):
        self.statements = []
        self.broken = broken
        self.closed = False
        self.autocommit = False

    def cursor(self):
        return FakeCursor(self)

    def transaction(self):
        conn = self

        class Tx:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        if conn.broken:
            raise FakeOperationalError("connection is closed")
        return Tx()

    def close(self):
        if self.broken:
            # mimic psycopg: closing an already-broken conn must not blow up the helper
            self.closed = True
            return
        self.closed = True


ROWS = [
    {"pair": "USDINR", "ts": "2026-10-24", "rate": 84.1},
    {"pair": "EURINR", "ts": "2026-10-24", "rate": 91.2},
]


class WriteWindow(unittest.TestCase):
    def test_deletes_only_its_own_keys(self):
        conn = FakeConn()
        written, out = mdjob.write_window(conn, "fx_rates", ["pair", "ts"], ROWS)
        self.assertEqual(written, 2)
        self.assertIs(out, conn)
        deletes = [(s, p) for s, p in conn.statements if s.startswith("DELETE")]
        self.assertEqual(len(deletes), 2)
        for (sql, params), row in zip(deletes, ROWS):
            self.assertEqual(sql, "DELETE FROM fx_rates WHERE pair=%s AND ts=%s")
            self.assertEqual(params, [row["pair"], row["ts"]])  # own key only, no broad delete

    def test_rerun_is_idempotent_by_shape(self):
        conn = FakeConn()
        mdjob.write_window(conn, "fx_rates", ["pair", "ts"], ROWS)
        first = list(conn.statements)
        conn.statements.clear()
        mdjob.write_window(conn, "fx_rates", ["pair", "ts"], ROWS)
        self.assertEqual(conn.statements, first)  # same deletes precede same inserts

    def test_empty_rows_writes_nothing(self):
        conn = FakeConn()
        written, out = mdjob.write_window(conn, "fx_rates", ["pair", "ts"], [])
        self.assertEqual((written, out), (0, conn))
        self.assertEqual(conn.statements, [])

    def test_retry_closes_broken_conn_and_returns_live_one(self):
        broken = FakeConn(broken=True)
        fresh = FakeConn()
        orig_connect = mdjob.connect
        mdjob.connect = lambda: fresh
        try:
            written, out = mdjob.write_window(broken, "fx_rates", ["pair", "ts"], ROWS)
        finally:
            mdjob.connect = orig_connect
        self.assertEqual(written, 2)
        self.assertIs(out, fresh)          # the LIVE connection is handed back
        self.assertTrue(broken.closed)     # the broken one was closed, not leaked
        self.assertEqual(len([s for s, _ in fresh.statements if s.startswith("INSERT")]), 2)

    def test_retry_false_reraises(self):
        with self.assertRaises(FakeOperationalError):
            mdjob.write_window(FakeConn(broken=True), "t", ["k"], ROWS, retry=False)


class JobRun(unittest.TestCase):
    def setUp(self):
        self.logged = []
        self._orig = mdjob._log
        mdjob._log = lambda name, started, sql, params: self.logged.append((sql, params))

    def tearDown(self):
        mdjob._log = self._orig

    def test_ok_path_records_rows_and_status(self):
        with mdjob.job_run("fx-fetcher") as run:
            run.rows = 7
        self.assertIn("INSERT INTO job_runs", self.logged[0][0])
        update_sql, update_params = self.logged[1]
        self.assertIn("UPDATE job_runs", update_sql)
        self.assertEqual(update_params[0], 7)
        self.assertEqual(update_params[1], "ok")

    def test_failure_records_error_and_reraises(self):
        with self.assertRaises(ValueError):
            with mdjob.job_run("fx-fetcher"):
                raise ValueError("boom")
        update_sql, update_params = self.logged[1]
        self.assertEqual(update_params[1], "fail")
        self.assertIn("boom", update_params[2])

    def test_bookkeeping_failure_never_fails_the_job(self):
        mdjob._log = self._orig  # real _log, whose connect() will raise AssertionError
        psycopg_stub.connect = lambda **kw: (_ for _ in ()).throw(OSError("db down"))
        try:
            with mdjob.job_run("fx-fetcher") as run:   # must not raise
                run.rows = 1
        finally:
            psycopg_stub.connect = lambda **kw: (_ for _ in ()).throw(
                AssertionError("unexpected real connect"))


class RunMain(unittest.TestCase):
    """run_main: the wrapper for scripts that keep their own write logic (per-job heartbeat v2)."""

    def setUp(self):
        self.calls = []
        self._log = mdjob._log
        mdjob._log = lambda job, started, sql, params: self.calls.append((job, sql.split()[0], params))

    def tearDown(self):
        mdjob._log = self._log

    def status(self):
        return self.calls[-1][2][1], self.calls[-1][2][2]

    def test_zero_return_is_ok_and_value_passes_through(self):
        self.assertEqual(mdjob.run_main("fx-fetcher", lambda: 0), 0)
        self.assertEqual([c[1] for c in self.calls], ["INSERT", "UPDATE"])
        self.assertEqual(self.status(), ("ok", None))

    def test_none_return_is_ok(self):
        self.assertIsNone(mdjob.run_main("x", lambda: None))
        self.assertEqual(self.status()[0], "ok")

    def test_nonzero_return_is_fail_and_still_returned(self):
        self.assertEqual(mdjob.run_main("x", lambda: 1), 1)
        self.assertEqual(self.status(), ("fail", "exit code 1"))

    def test_exception_is_recorded_and_reraised(self):
        def boom():
            raise ValueError("no data")
        with self.assertRaises(ValueError):
            mdjob.run_main("x", boom)
        self.assertEqual(self.status()[0], "fail")
        self.assertIn("no data", self.status()[1])

    def test_sys_exit_zero_is_ok_nonzero_is_fail(self):
        with self.assertRaises(SystemExit):
            mdjob.run_main("x", lambda: sys.exit(0))
        self.assertEqual(self.status()[0], "ok")
        with self.assertRaises(SystemExit):
            mdjob.run_main("x", lambda: sys.exit(3))
        self.assertEqual(self.status(), ("fail", "exit 3"))

    def test_bookkeeping_failure_never_fails_the_job(self):
        mdjob._log = self._log          # real _log; connect() is the stub that raises
        self.assertEqual(mdjob.run_main("x", lambda: 0), 0)

    def test_job_name_env_override(self):
        import os
        os.environ["JOB_NAME"] = "stock-prices-backfill"
        try:
            mdjob.run_main("stock-prices", lambda: 0)
        finally:
            del os.environ["JOB_NAME"]
        self.assertEqual(self.calls[0][0], "stock-prices-backfill")


if __name__ == "__main__":
    unittest.main()
