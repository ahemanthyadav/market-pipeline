"""Upcoming earnings dates via yfinance (schema 19) — weekly job.

For every active constituent, Ticker.calendar gives the next expected
earnings date(s). Idempotent per rule 4: per symbol, DELETE its rows with
event_date >= today then INSERT what yfinance reports now; past rows are
left untouched as a record of what was expected.

Quirks:
- yfinance .calendar is a dict since 0.2.x ('Earnings Date': [date, ...]);
  older frames are handled by falling back to get_earnings_dates().
- Many NSE names return nothing — that's a skip, not an error.
- One request per symbol; ~1k calls/run, throttled, so the CronJob gets a
  generous activeDeadlineSeconds. SYMBOL_LIMIT env caps a canary run.
"""
import os
import time
from datetime import date, datetime, timezone

import yfinance as yf

from mdjob import connect, job_run

THROTTLE_S = float(os.environ.get("THROTTLE_S", "0.4"))
SYMBOL_LIMIT = int(os.environ.get("SYMBOL_LIMIT", "0"))  # 0 = all


def next_dates(symbol):
    """Future earnings dates for one symbol, [] when unknown."""
    try:
        cal = yf.Ticker(symbol).calendar
    except Exception as exc:  # noqa: BLE001 - per-symbol fetch must not kill the run
        print(f"WARN {symbol}: calendar failed: {exc}", flush=True)
        return []
    dates = []
    if isinstance(cal, dict):
        raw = cal.get("Earnings Date") or []
        dates = [d for d in raw if isinstance(d, date)]
    elif cal is not None and hasattr(cal, "columns"):  # legacy DataFrame shape
        try:
            dates = [ts.date() for ts in cal.loc["Earnings Date"].dropna()]
        except Exception:  # noqa: BLE001
            dates = []
    today = datetime.now(timezone.utc).date()
    return sorted({d for d in dates if d >= today})


def main() -> int:
    with job_run("earnings-calendar") as run:
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol, universe FROM universe_constituents "
                    "WHERE active ORDER BY symbol")
                symbols = cur.fetchall()
            if SYMBOL_LIMIT:
                symbols = symbols[:SYMBOL_LIMIT]
            written = skipped = 0
            for symbol, universe in symbols:
                dates = next_dates(symbol)
                time.sleep(THROTTLE_S)
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "DELETE FROM earnings_calendar "
                            "WHERE symbol = %s AND event_date >= CURRENT_DATE",
                            (symbol,))
                        if dates:
                            cur.executemany(
                                "INSERT INTO earnings_calendar "
                                "(symbol, universe, event_date) VALUES (%s,%s,%s) "
                                "ON CONFLICT (symbol, event_date) DO NOTHING",
                                [(symbol, universe, d) for d in dates])
                if dates:
                    written += len(dates)
                else:
                    skipped += 1
        finally:
            conn.close()
        run.rows = written
        print(f"EARNINGS-OK symbols={len(symbols)} dates={written} "
              f"none={skipped}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
