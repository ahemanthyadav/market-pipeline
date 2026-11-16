"""Daily NSE/India indices fetcher via yfinance (validated Yahoo symbols).

Writes into index_prices (same schema as the global batch). Idempotent
DELETE+INSERT per (ticker, ts), one commit per calendar year. Runs 11:15 UTC,
after NSE close (10:00 UTC).

Symbol notes (verified 2026-09-29 on the TX2 stack; re-probed 2026-10-09):
- Live: ^NSEI ^NSEBANK ^CNXIT ^CNXPHARMA ^BSESN ^INDIAVIX
         NIFTYMIDCAP150.NS NIFTYSMLCAP250.NS
- RESTORED ^CRSLDX (Nifty 500) 2026-10-09: probed live on Yahoo — 21 valid
  daily bars over 1mo, fresh through today (NIFTY500.NS itself stays dead).
- ^CNXAUTO / ^CNXFMCG dead on Yahoo (stale ghosts, all symbol families) —
  since v5 (2026-10-26) they come from NSE DIRECT instead:
  api/historicalOR/indicesHistory on the fiidii cookie pattern, full daily
  OHLC back to Jan 2015 ("CNX AUTO"/"CNX FMCG" upstream). Ticker = series
  identity, not source (decision 2026-10-26): they keep the Yahoo CNX-family
  names next to ^CNXIT/^CNXPHARMA. The same leg can deep-backfill
  NIFTYMIDCAP150.NS pre-Yahoo history (close-only early years), hard-capped
  strictly BEFORE 2019-01-14 — Yahoo owns that series from there on (rule 4:
  every writer owns only its own (ticker, ts) window).

Env: PGHOST, PGUSER, PGPASSWORD, PGDATABASE. Backfills: BACKFILL=1 +
FETCH_PERIOD/FETCH_START (Yahoo), FETCH_START (NSE leg, default 2015-01-01),
NSE_DIRECT_ONLY=1 (skip Yahoo), NSE_MIDCAP150=1 (pre-2019 MIDCAP150 pull).
Exit 1 only if zero rows written.
"""

import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import psycopg
import requests
import yfinance as yf

from sessions import closed_bars  # manifests/_common/sessions.py, copied into the image

TICKERS = [
    "^NSEI",           # Nifty 50
    "^NSEBANK",        # Nifty Bank
    "^CNXIT",          # Nifty IT
    "^CNXPHARMA",      # Nifty Pharma
    "NIFTYMIDCAP150.NS",
    "NIFTYSMLCAP250.NS",
    "^CRSLDX",         # Nifty 500 (restored 2026-10-09)
    "^BSESN",          # Sensex
    "^INDIAVIX",       # India VIX
]

# ---- NSE-direct leg (v5, 2026-10-26) ------------------------------------
NSE_BASE = "https://www.nseindia.com"
NSE_API = NSE_BASE + "/api/historicalOR/indicesHistory"
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) market-data-pipeline/1.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": NSE_BASE + "/reports-indices-historical-index-data",
}
NSE_DIRECT = {"^CNXAUTO": "NIFTY AUTO", "^CNXFMCG": "NIFTY FMCG"}
MIDCAP_TICKER = "NIFTYMIDCAP150.NS"
MIDCAP_INDEX = "NIFTY MIDCAP 150"
MIDCAP_CUTOFF = date(2019, 1, 14)   # Yahoo's first bar — NSE rows stay strictly before it


def nse_records(session, index_name, start, end):
    """One indicesHistory call (a calendar year fits one request):
    [(ts 00:00 UTC, o, h, l, c, v)]. Early years are close-only → OHLC/volume None."""
    r = session.get(NSE_API, params={"indexType": index_name,
                                     "from": start.strftime("%d-%m-%Y"),
                                     "to": end.strftime("%d-%m-%Y")}, timeout=60)
    r.raise_for_status()
    out = []
    for e in r.json().get("data", []):
        raw, close = e.get("EOD_TIMESTAMP"), e.get("EOD_CLOSE_INDEX_VAL")
        if not raw or not isinstance(close, (int, float)):
            continue
        # NAIVE midnight like the Yahoo leg (container runs UTC; psycopg stores
        # both as 00:00+00) — mixing aware/naive breaks min()/max() in write_rows.
        ts = datetime.strptime(raw, "%d-%b-%Y")

        def num(key):
            v = e.get(key)
            return float(v) if isinstance(v, (int, float)) else None

        qty = e.get("HIT_TRADED_QTY")
        out.append((ts, num("EOD_OPEN_INDEX_VAL"), num("EOD_HIGH_INDEX_VAL"),
                    num("EOD_LOW_INDEX_VAL"), float(close),
                    int(qty) if isinstance(qty, (int, float)) else None))
    return out


def nse_range(session, index_name, start, end, pace=False):
    """Sweep [start, end] in ~90-day windows. The API caps ~70 records per call
    (verified 2026-10-26: a full-year ask returns Jan→mid-Apr only), so a window
    that comes back suspiciously full resumes from the last served day instead
    of silently skipping to the next window."""
    bars = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=90), end)
        got = nse_records(session, index_name, cursor, chunk_end)
        bars.extend(got)
        if len(got) >= 65:   # at/near the cap: assume truncated
            cursor = max(b[0] for b in got).date() + timedelta(days=1)
        else:
            cursor = chunk_end + timedelta(days=1)
        if pace:
            time.sleep(1.0)
    return bars


def fetch_nse_direct() -> dict:
    """{ticker: bars} via nseindia.com (fiidii cookie pattern). Daily: the last
    7 days of NIFTY AUTO/FMCG. BACKFILL=1: year-chunked sweep from FETCH_START
    (default 2015-01-01); NSE_MIDCAP150=1 adds pre-Yahoo MIDCAP150 history,
    hard-capped before 2019-01-14. Per-ticker failures WARN, never kill the batch."""
    today = datetime.now(timezone.utc).date()
    backfill = os.environ.get("BACKFILL", "0") == "1"
    start = (date.fromisoformat(os.environ.get("FETCH_START", "2015-01-01"))
             if backfill else today - timedelta(days=7))
    wanted = dict(NSE_DIRECT)
    if backfill and os.environ.get("NSE_MIDCAP150", "0") == "1":
        wanted[MIDCAP_TICKER] = MIDCAP_INDEX
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    try:
        session.get(NSE_BASE, timeout=30).raise_for_status()  # cookie warm-up
    except Exception as exc:  # noqa: BLE001
        print(f"WARN NSE session warm-up failed: {exc}", flush=True)
        return {}
    rows = {}
    for ticker, index_name in wanted.items():
        end_cap = (MIDCAP_CUTOFF - timedelta(days=1)) if ticker == MIDCAP_TICKER else today
        bars = []
        try:
            bars = nse_range(session, index_name, start, end_cap, pace=backfill)
        except Exception as exc:  # noqa: BLE001 - per-ticker failure must not kill the batch
            print(f"WARN {ticker} (NSE {index_name}) failed: {exc}", flush=True)
        if ticker == MIDCAP_TICKER:
            bars = [b for b in bars if b[0].date() < MIDCAP_CUTOFF]  # belt and braces
        # One bar per ts even if the API returns rows outside the asked window —
        # a duplicated (ticker, ts) in a single batch would trip ux_index_prices.
        bars = list({b[0]: b for b in bars}.values())
        if bars:
            rows[ticker] = bars
        else:
            print(f"WARN no NSE data for {ticker}", flush=True)
    return rows


def fetch_latest() -> dict:
    """Return {ticker: [(ts, open, high, low, close, volume), ...]} — every closed bar in the window."""
    # Backfills set BACKFILL=1 plus FETCH_PERIOD (5y, max, ...) or FETCH_START
    # (YYYY-MM-DD, wins over the period) to bound how far back they reach.
    start = os.environ.get("FETCH_START")
    span = {"start": start} if start else {"period": os.environ.get("FETCH_PERIOD", "5d")}
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
    for ticker in TICKERS:
        try:
            df = data[ticker].dropna(subset=["Close"])
            if df.empty:
                print(f"WARN no data for {ticker}", flush=True)
                continue
            # Daily mode rewrites the WHOLE fetch window (was: newest bar only — a missed
            # or mid-session day was then never corrected); open sessions are dropped
            # below so a partial bar is never stored as a close (sessions.py, 2026-11-12).
            frame = df
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
            kept = closed_bars(ticker, out)   # backfills too: never store an open session
            if len(kept) < len(out):
                print(f"{ticker}: skipped {len(out) - len(kept)} open-session bar(s)", flush=True)
            if kept:
                rows[ticker] = kept
            else:
                print(f"WARN {ticker}: no closed bars in the window", flush=True)
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
    nse_only = os.environ.get("NSE_DIRECT_ONLY", "0") == "1"
    rows = {} if nse_only else fetch_latest()
    rows.update(fetch_nse_direct())   # ticker sets are disjoint by construction
    expected = len(NSE_DIRECT) + (0 if nse_only else len(TICKERS)) \
        + (1 if os.environ.get("NSE_MIDCAP150", "0") == "1" else 0)
    print(f"fetched {len(rows)}/{expected} tickers", flush=True)
    if not rows:
        print("ERROR: zero rows fetched", flush=True)
        return 1
    written = write_rows(rows)
    print(f"wrote {written} rows into index_prices", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
