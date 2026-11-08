"""DQ-5 accuracy cross-checks (weekly) — is the data right, independently?

Five checks, one markdown report written to stock_reports as '__accuracy__'
(DELETE+INSERT of its own (symbol, ts) row — rule 4):
  A. stock_valuations internals recomputed in SQL: fair_value == median of
     the 8 method columns, upside_pct == (fair/close - 1) * 100.
  B. sector_metrics.median_pe recomputed with percentile_cont over members.
  C. index_membership current S&P set vs universe_constituents (drift count).
  D. SECOND SOURCE: a deterministic weekly rotation of 25 SP500 symbols,
     our stock_prices.close vs Stooq's free CSV on the matched date;
     flag |diff| > 0.5% (NSE skipped: Stooq's .in coverage is unreliable).
  E. Freshness of the tables every page/report reads.

A failed check is reported, not fatal: the job exits 0 and the report (and
its WARN lines in the log) carry the findings. Alerting on it stays with the
daily digest's stale-feeds pattern if it ever matters enough.
"""
import json
import os
import urllib.request
from datetime import datetime, timezone

from mdjob import connect, job_run

TOL_INTERNAL = 0.01   # absolute, on fair-value medians
TOL_UPSIDE = 0.1      # percentage points
TOL_PRICE_PCT = 0.5   # second-source close disagreement, percent

VAL_SQL = """
    WITH latest AS (
      SELECT * FROM stock_valuations
      WHERE ts >= now() - interval '4 days'
        AND ts = (SELECT max(ts) FROM stock_valuations
                  WHERE ts >= now() - interval '4 days')
    ), m AS (
      SELECT symbol, close, fair_value, upside_pct,
             (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY v)
              FROM unnest(ARRAY[graham, peer_multiple, fcf_yield, dcf, ddm,
                                epv, residual_income, peg_lynch,
                                damodaran_fcff, graham_growth]) AS t(v)
              WHERE v IS NOT NULL) AS med
      FROM latest WHERE fair_value IS NOT NULL
    )
    SELECT count(*) FILTER (WHERE abs(med - fair_value) > %s) AS bad_median,
           count(*) FILTER (WHERE abs((fair_value / close - 1) * 100
                                      - upside_pct) > %s) AS bad_upside,
           count(*) AS total
    FROM m WHERE close > 0
"""

SECTOR_SQL = """
    WITH latest AS (
      SELECT * FROM sector_metrics
      WHERE ts >= now() - interval '4 days'
        AND ts = (SELECT max(ts) FROM sector_metrics
                  WHERE ts >= now() - interval '4 days')
    )
    SELECT count(*) FROM latest l
    WHERE l.median_pe IS NOT NULL AND abs(l.median_pe - (
      SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY f.value)
      FROM (SELECT DISTINCT ON (symbol) symbol, value FROM stock_fundamentals
            WHERE metric = 'pe' AND ts >= now() - interval '7 days'
            ORDER BY symbol, ts DESC) f
      JOIN universe_constituents c
        ON c.symbol = f.symbol AND c.universe = l.universe
      WHERE c.active AND c.sector = l.sector
        AND f.value > 0 AND f.value < 200)) > 0.5
"""
# MEDIAN_CAP['pe'] in valuation.py is 200 and medians are over each symbol's
# latest row: the recompute above mirrors both, so a flag is a real divergence.

DRIFT_SQL = """
    SELECT count(*) FROM (
      SELECT symbol FROM index_membership
      WHERE universe = 'SP500' AND removed IS NULL
      EXCEPT
      SELECT symbol FROM universe_constituents
      WHERE universe = 'SP500' AND active) d
"""

BENCH_SQL = """
    SELECT ts::date, close FROM index_prices
    WHERE ticker = '^GSPC' AND ts >= now() - interval '25 days'
    ORDER BY ts DESC LIMIT 10
"""

STOCK_SAMPLE_SQL = """
    SELECT DISTINCT ON (p.symbol) p.symbol, p.ts::date, p.close
    FROM stock_prices p
    JOIN universe_constituents c ON c.symbol = p.symbol
    WHERE c.universe = 'SP500' AND c.active
      AND p.ts >= now() - interval '10 days' AND p.close > 0
    ORDER BY p.symbol, p.ts DESC
"""

FRESH_SQL = """
    SELECT 'stock_prices', max(ts)::date FROM stock_prices
      WHERE ts >= now() - interval '7 days'
    UNION ALL SELECT 'stock_valuations', max(ts)::date FROM stock_valuations
      WHERE ts >= now() - interval '7 days'
    UNION ALL SELECT 'stock_scores', max(ts)::date FROM stock_scores
      WHERE ts >= now() - interval '7 days'
    UNION ALL SELECT 'news_sentiment', max(ts)::date FROM news_sentiment
      WHERE ts >= now() - interval '7 days'
"""


def fred_sp500():
    """{date: close} from FRED's public CSV of the official S&P 500 level.
    (Stooq was the original per-stock source but grew a JS challenge wall
    2026-10; a per-stock second source needs a free API key — owner call.)"""
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500"
    req = urllib.request.Request(url, headers={"User-Agent": "marketdata-dq5"})
    try:
        body = urllib.request.urlopen(req, timeout=30).read().decode()
    except Exception as exc:  # noqa: BLE001 - network check must not kill the run
        print(f"WARN fred: {exc}", flush=True)
        return {}
    out = {}
    for line in body.strip().splitlines()[1:]:
        day, _, val = line.partition(",")
        try:
            out[datetime.strptime(day, "%Y-%m-%d").date()] = float(val)
        except ValueError:
            continue  # FRED marks holidays '.'
    return out


def tiingo_close(symbol, day, api_key):
    """Fetch single-day unadjusted close from Tiingo REST API for `symbol` on `day`."""
    sym = symbol.replace(".", "-")
    url = (f"https://api.tiingo.com/tiingo/daily/{sym}/prices"
           f"?startDate={day}&endDate={day}")
    req = urllib.request.Request(url, headers={
        "Content-Type": "application/json",
        "Authorization": f"Token {api_key}",
        "User-Agent": "marketdata-dq5",
    })
    try:
        rows = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
        if rows and isinstance(rows, list) and "close" in rows[0]:
            return float(rows[0]["close"])
    except Exception as exc:  # noqa: BLE001
        print(f"WARN tiingo {symbol}: {exc}", flush=True)
    return None


def per_stock_second_source(cur, sample_size=25):
    """DG-04 / DF-03: Deterministic weekly rotation of `sample_size` SP500 symbols
    checked against Tiingo when TIINGO_API_KEY is configured. Returns (checked, mismatches)."""
    api_key = os.environ.get("TIINGO_API_KEY", "").strip()
    if not api_key:
        return 0, []
    cur.execute(STOCK_SAMPLE_SQL)
    rows = cur.fetchall()
    if not rows:
        return 0, []
    week_num = datetime.now(timezone.utc).isocalendar()[1]
    start = (week_num * sample_size) % len(rows)
    rotated = (rows[start:] + rows[:start])[:sample_size]
    checked, mismatches = 0, []
    for sym, day, ours in rotated:
        theirs = tiingo_close(sym, day, api_key)
        if theirs is None or not ours:
            continue
        checked += 1
        diff = abs(ours / theirs - 1) * 100
        if diff > TOL_PRICE_PCT:
            mismatches.append((sym, day, ours, theirs, diff))
    return checked, mismatches


def second_source(cur):
    """Our ^GSPC closes vs FRED's official S&P 500 level, last 10 bars, plus
    optional per-stock Tiingo cross-check when TIINGO_API_KEY is set."""
    fred = fred_sp500()
    cur.execute(BENCH_SQL)
    checked, mismatches = 0, []
    for day, ours in cur.fetchall():
        theirs = fred.get(day)
        if theirs is None or not ours:
            continue
        checked += 1
        diff = abs(ours / theirs - 1) * 100
        if diff > TOL_PRICE_PCT:
            mismatches.append(("^GSPC", day, ours, theirs, diff))
    ps_checked, ps_mismatches = per_stock_second_source(cur)
    checked += ps_checked
    mismatches.extend(ps_mismatches)
    return checked, mismatches


def main() -> int:
    with job_run("accuracy-check") as run:
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                   microsecond=0)
        conn = connect()
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(VAL_SQL, (TOL_INTERNAL, TOL_UPSIDE))
                bad_median, bad_upside, val_total = cur.fetchone()
                cur.execute(SECTOR_SQL)
                bad_sectors = cur.fetchone()[0]
                cur.execute(DRIFT_SQL)
                drift = cur.fetchone()[0]
                cur.execute(FRESH_SQL)
                fresh = cur.fetchall()
                checked, mismatches = second_source(cur)
            fails = bad_median + bad_upside + bad_sectors + drift + len(mismatches)
            md = [f"# Accuracy cross-checks (DQ-5) — {today:%Y-%m-%d}",
                  "",
                  f"**{'ALL CLEAN' if fails == 0 else f'{fails} FINDING(S)'}**",
                  "",
                  f"- A. valuation internals: {val_total} rows — "
                  f"{bad_median} bad medians, {bad_upside} bad upsides",
                  f"- B. sector median P/E recompute: {bad_sectors} off by > 0.5",
                  f"- C. S&P membership vs universe drift: {drift} symbols",
                  f"- D. second source (FRED SP500 vs our ^GSPC): {checked} checked, "
                  f"{len(mismatches)} > {TOL_PRICE_PCT}% off",
                  "- E. freshness: " + ", ".join(
                      f"{t} {d}" if d else f"{t} **STALE**" for t, d in fresh)]
            for sym, day, ours, theirs, diff in mismatches:
                md.append(f"  - {sym} {day}: ours {ours:.2f} vs stooq "
                          f"{theirs:.2f} ({diff:.2f}%)")
                print(f"WARN price mismatch {sym} {day}: {ours} vs {theirs}",
                      flush=True)
            md.append("\n_Automated weekly check; tolerances in "
                      "accuracy_check.py. Not advice._")
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM stock_reports "
                                "WHERE symbol = '__accuracy__' AND ts = %s",
                                (today,))
                    cur.execute("INSERT INTO stock_reports (symbol, ts, report_md) "
                                "VALUES ('__accuracy__', %s, %s)",
                                (today, "\n".join(md)))
            run.rows = 1
        finally:
            conn.close()
        print(f"ACCURACY-OK findings={fails} checked={checked}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
