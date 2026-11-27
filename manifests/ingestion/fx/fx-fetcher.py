"""Daily FX fetcher via yfinance (=X series, no API key needed).

Writes into fx_rates (pair, ts, rate=Close). Idempotent DELETE+INSERT per
(pair, ts). Runs 23:00 UTC with the global batch settled. Pairs that Yahoo
restricts (e.g. some CNY crosses) log WARN and are skipped — the job succeeds
on partial coverage, fails only if zero rows are written.

Env: PGHOST, PGUSER, PGPASSWORD, PGDATABASE. Exit 1 only if zero rows written.
"""

import os
import sys

import psycopg
import yfinance as yf

TICKERS = [
    "USDINR=X",   # key pair for this pipeline
    "EURINR=X",
    "GBPINR=X",
    "JPYINR=X",
    "EURUSD=X",
    "GBPUSD=X",
    "USDJPY=X",
    "USDCNY=X",
    "USDCHF=X",
    "AUDUSD=X",
]


def fetch_latest() -> dict:
    """Return {pair: (ts, rate)} for the latest valid close."""
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
        except Exception as exc:  # noqa: BLE001 - per-pair failure must not kill the batch
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
            for pair, (ts, rate) in rows.items():
                cur.execute(
                    "DELETE FROM fx_rates WHERE pair = %s AND ts = %s",
                    (pair, ts),
                )
                cur.execute(
                    "INSERT INTO fx_rates (pair, ts, rate) VALUES (%s, %s, %s)",
                    (pair, ts, rate),
                )
                written += 1
    conn.close()
    return written


def main() -> int:
    rows = fetch_latest()
    print(f"fetched {len(rows)}/{len(TICKERS)} pairs", flush=True)
    if not rows:
        print("ERROR: zero rows fetched", flush=True)
        return 1
    written = write_rows(rows)
    print(f"wrote {written} rows into fx_rates", flush=True)
    return 0


if __name__ == "__main__":
    from mdjob import run_main   # one job_runs row per execution; never fails the job
    sys.exit(run_main("fx-fetcher", main))
