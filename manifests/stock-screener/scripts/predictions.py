"""Prediction scorecard (prediction-validation plan, schema 18) — daily job.

Two passes in one run:
1. SNAPSHOT: write today's claims write-once-per-day (DELETE+INSERT only
   today's rows): kind 'screener_rank' (value=rank, aux=composite) for every
   eligible scored symbol, and kind 'valuation_upside' (value=upside_pct,
   aux=fair_value) for every valued symbol. `close` stores the ADJUSTED close
   at snapshot so later returns are total-return and split-safe.
2. RESOLVE: for every snapshot whose horizon (30/91/182 calendar days) has
   elapsed and that has no outcome row yet, compute the realized adjusted
   forward return and the universe benchmark's return over the same window,
   then INSERT ... ON CONFLICT DO NOTHING (outcomes are write-once).

Prices: stock_prices.adj_close at the latest bar <= the target date (markets
close on weekends; the nearest prior bar is the correct fill). Benchmarks:
index_prices close for ^GSPC / ^CRSLDX (no adjusted series; index levels).
Scheduled after stock-screener (02:15) and stock-valuation (02:10).
"""
from datetime import datetime, timedelta, timezone

from mdjob import connect, job_run

HORIZONS = (30, 91, 182)
# NIFTYMICROCAP250 benchmarks against the broad Indian market: Yahoo serves no
# history for NIFTY_MICROCAP250.NS (verified 2026-10-27 — one bar), and
# beating the investable broad index is the honest excess-return question.
BENCH = {"SP500": "^GSPC", "NIFTY500": "^CRSLDX", "SP400": "^SP400",
         "SP600": "^SP600", "NIFTYMICROCAP250": "^CRSLDX"}

SNAP_SCORES_SQL = """
    SELECT s.symbol, s.universe, s.rank::float, s.composite, p.adj_close
    FROM stock_scores s
    JOIN LATERAL (
        SELECT adj_close FROM stock_prices
        WHERE symbol = s.symbol AND ts >= now() - interval '7 days'
        ORDER BY ts DESC LIMIT 1) p ON TRUE
    WHERE s.ts = %s AND s.eligible AND s.rank IS NOT NULL
"""

SNAP_VAL_SQL = """
    SELECT v.symbol, v.universe, v.upside_pct, v.fair_value, p.adj_close
    FROM stock_valuations v
    JOIN LATERAL (
        SELECT adj_close FROM stock_prices
        WHERE symbol = v.symbol AND ts >= now() - interval '7 days'
        ORDER BY ts DESC LIMIT 1) p ON TRUE
    WHERE v.ts = %s AND v.fair_value IS NOT NULL
"""

DUE_SQL = """
    SELECT s.kind, s.symbol, s.universe, s.ts, s.close, h.horizon
    FROM prediction_snapshots s
    CROSS JOIN (SELECT unnest(%s::int[]) AS horizon) h
    LEFT JOIN prediction_outcomes o
      ON o.kind = s.kind AND o.symbol = s.symbol AND o.ts = s.ts
     AND o.horizon_days = h.horizon
    WHERE o.symbol IS NULL AND s.close IS NOT NULL AND s.close > 0
      AND s.ts + h.horizon * interval '1 day' <= %s
"""

PX_AT_SQL = """
    SELECT adj_close FROM stock_prices
    WHERE symbol = %s AND ts >= %s - interval '10 days' AND ts <= %s
    ORDER BY ts DESC LIMIT 1
"""

BENCH_AT_SQL = """
    SELECT close FROM index_prices
    WHERE ticker = %s AND ts >= %s - interval '10 days' AND ts <= %s
    ORDER BY ts DESC LIMIT 1
"""


LATEST_TS_SQL = "SELECT max(ts) FROM {table} WHERE ts >= now() - interval '4 days'"


def snapshot(conn):
    """Snapshot each source at ITS latest run day (not the calendar day: just
    after UTC midnight today's run doesn't exist yet, and a skipped run must
    not silently snapshot nothing)."""
    rows = []
    with conn.cursor() as cur:
        for kind, table, sql in (
                ("screener_rank", "stock_scores", SNAP_SCORES_SQL),
                ("valuation_upside", "stock_valuations", SNAP_VAL_SQL)):
            cur.execute(LATEST_TS_SQL.format(table=table))
            ts = cur.fetchone()[0]
            if ts is None:
                print(f"WARN no fresh {table} rows — {kind} skipped", flush=True)
                continue
            cur.execute(sql, (ts,))
            rows += [(kind, s, u, ts, v, a, c) for s, u, v, a, c in cur.fetchall()]
    if not rows:
        return 0
    with conn.transaction():
        with conn.cursor() as cur:
            for kind, ts in {(r[0], r[3]) for r in rows}:
                cur.execute("DELETE FROM prediction_snapshots "
                            "WHERE kind = %s AND ts = %s", (kind, ts))
            cur.executemany(
                "INSERT INTO prediction_snapshots "
                "(kind, symbol, universe, ts, value, aux, close) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)", rows)
    return len(rows)


def resolve(conn, today):
    with conn.cursor() as cur:
        cur.execute(DUE_SQL, (list(HORIZONS), today))
        due = cur.fetchall()
    if not due:
        return 0
    out, px_cache, bench_cache = [], {}, {}
    with conn.cursor() as cur:
        for kind, sym, uni, ts, base, horizon in due:
            target = ts + timedelta(days=horizon)
            key = (sym, target)
            if key not in px_cache:
                cur.execute(PX_AT_SQL, (sym, target, target))
                row = cur.fetchone()
                px_cache[key] = row[0] if row else None
            px = px_cache[key]
            if px is None:
                continue  # delisted or no bar yet near the target; retry tomorrow
            bench = BENCH.get(uni)
            bkey = (bench, ts, target)
            if bkey not in bench_cache:
                cur.execute(BENCH_AT_SQL, (bench, ts, ts))
                b0 = cur.fetchone()
                cur.execute(BENCH_AT_SQL, (bench, target, target))
                b1 = cur.fetchone()
                bench_cache[bkey] = (b1[0] / b0[0] - 1.0) if b0 and b1 and b0[0] else None
            out.append((kind, sym, ts, horizon, px / base - 1.0, bench_cache[bkey]))
    with conn.transaction():
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO prediction_outcomes "
                "(kind, symbol, ts, horizon_days, realized_ret, bench_ret) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", out)
    return len(out)


def main() -> int:
    with job_run("prediction-tracker") as run:
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                   microsecond=0)
        conn = connect()
        try:
            snapped = snapshot(conn)
            resolved = resolve(conn, today)
        finally:
            conn.close()
        run.rows = snapped + resolved
        print(f"PREDICTIONS-OK snapshots={snapped} resolved={resolved}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
