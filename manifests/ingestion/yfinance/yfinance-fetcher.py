"""Daily batch fetcher: global indices + DXY + metals/energy futures via yfinance.

Writes one row per ticker per day into index_prices (ticker, ts, o/h/l/c, volume).
Idempotent: DELETEs its (ticker, ts) rows before INSERT, so re-runs and late
corrections overwrite cleanly; commits one calendar year at a time. No schema migration needed (no new constraints).

Env: PGHOST, PGUSER, PGPASSWORD, PGDATABASE (defaults suit the in-cluster DB).
Exit 0 unless zero rows were written (signals CronJob failure).
"""

import os
import sys

import pandas as pd
import psycopg
import yfinance as yf

TICKERS = [
    # US
    "^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX",
    "^RUI",  # Russell 1000 (added 2026-10-10; Yahoo probe: 251 daily bars over 1y)
    # ADR 0029 universe benchmarks (probed live 2026-10-27, all three serve):
    "^SP400", "^SP600", "NIFTY_MICROCAP250.NS",
    # Europe
    "^FTSE", "^GDAXI", "^FCHI", "^STOXX50E", "^IBEX",
    "FTSEMIB.MI", "^SSMI", "^AEX",
    # APAC
    "^N225", "^HSI", "000001.SS", "399001.SZ",
    "^KS11", "^TWII", "^AXJO", "^STI",
    # Americas ex-US
    "^GSPTSE", "^BVSP", "^MXX",
    # Dollar index + metals/energy futures
    "DX-Y.NYB", "GC=F", "SI=F", "CL=F",
]


def fetch_latest() -> dict:
    """Return {ticker: (ts, open, high, low, close, volume)} for the latest valid row."""
    # Backfills set BACKFILL=1 plus FETCH_PERIOD (5y, max, ...) or FETCH_START
    # (YYYY-MM-DD, wins over the period) to bound how far back they reach.
    start = os.environ.get("FETCH_START")
    span = {"start": start} if start else {"period": os.environ.get("FETCH_PERIOD", "5d")}
    backfill = os.environ.get("BACKFILL", "0") == "1"
    data = yf.download(
        " ".join(TICKERS),
        **span,
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        progress=False,
        threads=True,
    )
    rows = {}
    frames = data if len(TICKERS) > 1 else {TICKERS[0]: data}
    for ticker in TICKERS:
        try:
            df = frames[ticker].dropna(subset=["Close"])
            if df.empty:
                print(f"WARN no data for {ticker}", flush=True)
                continue
            frame = df if backfill else df.iloc[[-1]]
            out = []
            for ts, bar in frame.iterrows():
                ts = ts.to_pydatetime()

                def num(col):
                    val = bar[col]
                    return None if pd.isna(val) else float(val)

                vol = bar["Volume"]
                out.append((
                    ts,
                    num("Open"),
                    num("High"),
                    num("Low"),
                    num("Close"),
                    None if pd.isna(vol) else int(vol),
                ))
            rows[ticker] = out
        except Exception as exc:  # noqa: BLE001 - per-ticker failure must not kill the batch
            print(f"WARN {ticker} failed: {exc}", flush=True)
    return rows


def year_batches(rows: dict) -> list:
    """[(year, [(ticker, ts, o, h, l, c, v), ...])] oldest year first.

    A deep backfill must not sit in one transaction: every chunk it touches
    holds locks until commit, and the 5y single-transaction run of 2026-10-10
    used ~2,900 of the database's ~6,400 lock slots. One commit per calendar
    year keeps any history depth to a year's worth of chunks at a time.
    """
    years = {}
    for ticker, bars in rows.items():
        for (ts, o, h, low, c, v) in bars:
            years.setdefault(ts.year, []).append((ticker, ts, o, h, low, c, v))
    return sorted(years.items())


def write_rows(rows: dict) -> int:
    conn = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    written = 0
    for year, batch in year_batches(rows):
        stamps = [bar[1] for bar in batch]
        # Still only our own (ticker, ts) rows; the ts bounds are there so the
        # planner prunes to this year's chunks instead of locking all of them.
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM index_prices p "
                    "USING unnest(%s::text[], %s::timestamptz[]) AS k(ticker, ts) "
                    "WHERE p.ticker = k.ticker AND p.ts = k.ts "
                    "AND p.ts >= %s AND p.ts <= %s",
                    ([bar[0] for bar in batch], stamps, min(stamps), max(stamps)),
                )
                cur.executemany(
                    "INSERT INTO index_prices "
                    "(ticker, ts, open, high, low, close, volume) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    batch,
                )
        written += len(batch)
        if os.environ.get("BACKFILL", "0") == "1":
            print(f"committed {year}: {len(batch)} rows", flush=True)
    conn.close()
    return written


def main() -> int:
    rows = fetch_latest()
    print(f"fetched {len(rows)}/{len(TICKERS)} tickers", flush=True)
    if not rows:
        print("ERROR: zero rows fetched", flush=True)
        return 1
    written = write_rows(rows)
    print(f"wrote {written} rows into index_prices", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
