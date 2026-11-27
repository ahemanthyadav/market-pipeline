"""Daily US Treasury yields via yfinance (no API key needed).

^TNX=10Y, ^FVX=5Y, ^TYX=30Y, ^IRX=3M T-bill. Writes into bond_yields
(instrument, ts, yield=Close in percent). Idempotent DELETE+INSERT.
Runs 23:15 UTC with the evening batch.

Fed funds rate and India G-Secs/RBI repo are NOT here: FRED needs an API key
(see plan Phase 3 job #5 — provide one to enable the rates job), RBI publishes
no stable machine-readable feed.

Env: PGHOST, PGUSER, PGPASSWORD, PGDATABASE. Exit 1 only if zero rows written.
"""

import os
import sys

import psycopg
import yfinance as yf

TICKERS = ["^TNX", "^FVX", "^TYX", "^IRX"]


def fetch_latest() -> dict:
    """Return {instrument: (ts, yield)} for the latest valid close."""
    data = yf.download(
        " ".join(TICKERS),
        period="5d",
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        progress=False,
        threads=True,
    )
    rows = {}
    for ticker in TICKERS:
        try:
            closes = data[ticker]["Close"].dropna()
            if closes.empty:
                print(f"WARN no data for {ticker}", flush=True)
                continue
            ts = closes.index[-1].to_pydatetime()
            rows[ticker] = (ts, float(closes.iloc[-1]))
        except Exception as exc:  # noqa: BLE001 - per-series failure must not kill the batch
            print(f"WARN {ticker} failed: {exc}", flush=True)
    return rows


def write_rows(rows: dict) -> int:
    conn = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    written = 0
    with conn:
        with conn.cursor() as cur:
            for inst, (ts, y) in rows.items():
                cur.execute(
                    "DELETE FROM bond_yields WHERE instrument = %s AND ts = %s",
                    (inst, ts),
                )
                cur.execute(
                    "INSERT INTO bond_yields (instrument, ts, yield) VALUES (%s, %s, %s)",
                    (inst, ts, y),
                )
                written += 1
    conn.close()
    return written


def main() -> int:
    rows = fetch_latest()
    print(f"fetched {len(rows)}/{len(TICKERS)} series", flush=True)
    if not rows:
        print("ERROR: zero rows fetched", flush=True)
        return 1
    written = write_rows(rows)
    print(f"wrote {written} rows into bond_yields", flush=True)
    return 0


if __name__ == "__main__":
    from mdjob import run_main   # one job_runs row per execution; never fails the job
    sys.exit(run_main("yields-fetcher", main))
