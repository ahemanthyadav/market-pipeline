"""Daily US rates via FRED (needs FRED_API_KEY from the fred-api Secret).

DFF (Fed funds effective) -> interest_rates; DGS2/DGS10/DGS30 -> bond_yields
(official depth alongside the yfinance ^TNX/^FVX/^TYX series). T10Y2Y and
BAMLH0A0HYM2 -> interest_rates (G-13 macro series). Idempotent
DELETE+INSERT per (name, ts). Runs 23:20 UTC in the evening batch.
Exit 1 only if zero rows are written.

Env: FRED_API_KEY, PGHOST, PGUSER, PGPASSWORD, PGDATABASE.
"""

import os
import sys

import psycopg
from fredapi import Fred

SERIES = {
    "DFF": ("interest_rates", "US Fed funds effective"),
    "DGS2": ("bond_yields", "US 2Y (FRED)"),
    "DGS10": ("bond_yields", "US 10Y (FRED)"),
    "DGS30": ("bond_yields", "US 30Y (FRED)"),
    # G-13 macro series (ADR 0026 §14)
    "T10Y2Y": ("interest_rates", "US 10Y-2Y spread"),
    "BAMLH0A0HYM2": ("interest_rates", "US HY OAS"),
}

TABLE_COLUMNS = {
    "interest_rates": ("rate_name", "value"),
    "bond_yields": ("instrument", "yield"),
}


def fetch_latest() -> dict:
    """Return {(table, name): (ts, value)} for the latest valid observation."""
    fred = Fred(api_key=os.environ["FRED_API_KEY"])
    rows = {}
    for series_id, (table, _name) in SERIES.items():
        try:
            series = fred.get_series(series_id).dropna()
            if series.empty:
                print(f"WARN no data for {series_id}", flush=True)
                continue
            ts = series.index[-1].to_pydatetime()
            rows[(table, series_id)] = (ts, float(series.iloc[-1]))
        except Exception as exc:  # noqa: BLE001 - per-series failure tolerated
            print(f"WARN {series_id} failed: {exc}", flush=True)
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
            for (table, series_id), (ts, value) in rows.items():
                name_col, val_col = TABLE_COLUMNS[table]
                cur.execute(
                    f"DELETE FROM {table} WHERE {name_col} = %s AND ts = %s",
                    (series_id, ts),
                )
                cur.execute(
                    f"INSERT INTO {table} (ts, {name_col}, {val_col}) "
                    "VALUES (%s, %s, %s)",
                    (ts, series_id, value),
                )
                written += 1
    conn.close()
    return written


def main() -> int:
    try:
        rows = fetch_latest()
    except Exception as exc:  # noqa: BLE001 - missing/invalid key must be loud
        print(f"ERROR FRED fetch failed: {exc}", flush=True)
        return 1
    print(f"fetched {len(rows)}/{len(SERIES)} series", flush=True)
    if not rows:
        print("ERROR: zero rows fetched", flush=True)
        return 1
    written = write_rows(rows)
    print(f"wrote {written} rows", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
