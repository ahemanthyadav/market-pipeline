"""Hourly intraday fetcher for fast-moving instruments (2026-10-03 request:
"gold and USD/INR and similar need to be checked more frequent").

One yfinance pull (interval=1h, period=2d) for:
  - FX pairs      -> fx_rates (pair, ts, rate)      [safe: nothing computes over it]
  - GC/SI/CL + DXY -> intraday_prices               [separate table: index_prices is
                                                     consumed by ta-compute daily math]

Idempotent: DELETE+INSERT per (key, ts) using the candle's REAL timestamp —
re-runs rewrite the forming candle; closed markets produce no new timestamps,
so no stale-row pollution. Coexists with daily rows without collision.

Env: PGHOST, PGUSER, PGPASSWORD, PGDATABASE. Exit 0 unless zero rows written.
"""
import os
import sys

import pandas as pd
import psycopg
import yfinance as yf

FX_PAIRS = [
    "USDINR=X", "EURUSD=X", "GBPUSD=X", "USDJPY=X",
    "AUDUSD=X", "USDCHF=X", "USDCNY=X", "EURINR=X", "GBPINR=X", "JPYINR=X",
]
TICKERS = [
    "GC=F", "SI=F", "CL=F", "DX-Y.NYB",
    "^GSPC", "^IXIC", "^NSEI", "^BSESN", "^VIX", "^INDIAVIX",
]
ALL = FX_PAIRS + TICKERS


def fetch() -> tuple[dict, dict]:
    """Return (fx_rows: {pair: [(ts, rate)]}, px_rows: {ticker: [(ts, o,h,l,c,v)]})."""
    data = yf.download(
        " ".join(ALL),
        period="2d",
        interval="1h",
        group_by="ticker",
        auto_adjust=False,
        progress=False,
        threads=True,
    )
    fx_rows, px_rows = {}, {}
    frames = data if len(ALL) > 1 else {ALL[0]: data}
    for ticker in ALL:
        try:
            df = frames[ticker].dropna(subset=["Close"])
            if df.empty:
                print(f"WARN no data for {ticker}", flush=True)
                continue
            out = []
            for ts, bar in df.iterrows():
                ts = ts.to_pydatetime()

                def num(col):
                    val = bar[col]
                    return None if pd.isna(val) else float(val)

                vol = bar["Volume"]
                out.append((ts, num("Open"), num("High"), num("Low"),
                            num("Close"),
                            None if pd.isna(vol) else int(vol)))
            if ticker in FX_PAIRS:
                fx_rows[ticker] = [(ts, c) for (ts, _o, _h, _l, c, _v) in out]
            else:
                px_rows[ticker] = out
        except Exception as exc:  # noqa: BLE001 - per-ticker failure tolerated
            print(f"WARN {ticker} failed: {exc}", flush=True)
    return fx_rows, px_rows


def write(fx_rows: dict, px_rows: dict) -> int:
    conn = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    written = 0
    with conn:
        with conn.cursor() as cur:
            for pair, bars in fx_rows.items():
                for (ts, rate) in bars:
                    cur.execute(
                        "DELETE FROM fx_rates WHERE pair = %s AND ts = %s",
                        (pair, ts),
                    )
                    cur.execute(
                        "INSERT INTO fx_rates (pair, ts, rate) VALUES (%s, %s, %s)",
                        (pair, ts, rate),
                    )
                    written += 1
            for ticker, bars in px_rows.items():
                for (ts, o, h, low, c, v) in bars:
                    cur.execute(
                        "DELETE FROM intraday_prices WHERE ticker = %s AND ts = %s",
                        (ticker, ts),
                    )
                    cur.execute(
                        "INSERT INTO intraday_prices "
                        "(ticker, ts, open, high, low, close, volume) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (ticker, ts, o, h, low, c, v),
                    )
                    written += 1
    conn.close()
    return written


def main() -> int:
    fx_rows, px_rows = fetch()
    got = len(fx_rows) + len(px_rows)
    print(f"fetched {got}/{len(ALL)} symbols "
          f"(fx {len(fx_rows)}, prices {len(px_rows)})", flush=True)
    if got == 0:
        print("ERROR: zero symbols fetched", flush=True)
        return 1
    written = write(fx_rows, px_rows)
    print(f"wrote {written} rows (fx_rates + intraday_prices)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
