"""Shared job helpers for market-data fetchers (AR-2 / AR-3).

Named `mdjob`, not `joblib`, so it can never shadow the PyPI `joblib` package.
Copied into the light and data images (no registry, ADR 0004). Two levels of adoption:
`job_run` + `write_window` for scripts written against it, `run_main` for the older scripts
that keep their own write logic and only need a job_runs row per execution.

    from mdjob import connect, write_window, job_run

    def main():
        with job_run("fx-fetcher") as run:
            rows = fetch()
            conn = connect()
            run.rows, conn = write_window(conn, "fx_rates", ["pair", "ts"], rows)
            conn.close()   # always close the RETURNED conn — a retry replaces it
        return 0 if run.rows else 1

job_runs is created by 06d-job-runs.sql in the schema ConfigMap.
"""

import os
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg


def connect():
    """Autocommit connection (reads never hold an idle transaction)."""
    conn = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    conn.autocommit = True
    return conn


def write_window(conn, table, key_cols, rows, retry=True):
    """DELETE + INSERT each row's own key in ONE transaction (ADR 0005).

    rows: list of dicts with identical keys, each containing every key_col.
    Returns (written, conn). On OperationalError the broken connection is closed,
    a fresh one is built and the whole window retried once — so keep using (and
    eventually close) the RETURNED connection, never the one passed in (same
    contract as stock_prices.write_resilient). Pass retry=False to opt out.
    """
    if not rows:
        return 0, conn
    cols = list(rows[0].keys())
    where = " AND ".join(f"{c}=%s" for c in key_cols)
    insert = (f"INSERT INTO {table} ({', '.join(cols)}) "
              f"VALUES ({', '.join(['%s'] * len(cols))})")
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                for row in rows:
                    cur.execute(f"DELETE FROM {table} WHERE {where}",
                                [row[c] for c in key_cols])
                    cur.execute(insert, [row[c] for c in cols])
        return len(rows), conn
    except psycopg.OperationalError:
        if not retry:
            raise
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — the conn is already broken
            pass
        return write_window(connect(), table, key_cols, rows, retry=False)


class _Run:
    rows = 0


@contextmanager
def job_run(job_name):
    """Record one execution in job_runs. Set `run.rows` inside the block.

    Bookkeeping failures never fail the job itself — the data write matters more
    than its log row.
    """
    run = _Run()
    started = datetime.now(timezone.utc)
    _log(job_name, started, "INSERT INTO job_runs (job_name, started_at, status) "
         "VALUES (%s, %s, 'running') ON CONFLICT DO NOTHING", (job_name, started))
    try:
        yield run
    except BaseException as exc:
        _finish(job_name, started, run.rows, "fail",
                "".join(traceback.format_exception_only(type(exc), exc)).strip()[:2000])
        raise
    _finish(job_name, started, run.rows, "ok", None)


def run_main(job_name, main):
    """Entry-point wrapper for scripts that keep their own write logic (per-job heartbeat v2):

        if __name__ == "__main__":
            from mdjob import run_main
            sys.exit(run_main("fx-fetcher", main))

    Records one job_runs row per execution — 'ok' when main() returns 0/None, 'fail' on a non-zero
    return or an exception — and returns main()'s own value unchanged. rows_written stays 0: these
    scripts do not report a count. JOB_NAME in the environment overrides the name (one-off backfill
    Jobs that reuse a script). Bookkeeping failures never fail the job.
    """
    job_name = os.environ.get("JOB_NAME") or job_name
    started = datetime.now(timezone.utc)
    _log(job_name, started, "INSERT INTO job_runs (job_name, started_at, status) "
         "VALUES (%s, %s, 'running') ON CONFLICT DO NOTHING", (job_name, started))
    try:
        rc = main()
    except SystemExit as exc:
        code = exc.code
        bad = code not in (0, None)
        _finish(job_name, started, 0, "fail" if bad else "ok", f"exit {code}"[:2000] if bad else None)
        raise
    except BaseException as exc:
        _finish(job_name, started, 0, "fail",
                "".join(traceback.format_exception_only(type(exc), exc)).strip()[:2000])
        raise
    _finish(job_name, started, 0, "fail" if rc else "ok", f"exit code {rc}" if rc else None)
    return rc


def _finish(job_name, started, rows, status, error):
    _log(job_name, started,
         "UPDATE job_runs SET ended_at=now(), rows_written=%s, status=%s, error=%s "
         "WHERE job_name=%s AND started_at=%s",
         (rows, status, error, job_name, started))


def _log(job_name, started, sql, params):
    try:
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"WARN {job_name} job_runs bookkeeping failed: {exc}", flush=True)
