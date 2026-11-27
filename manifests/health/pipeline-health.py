"""Pipeline health monitor (heartbeat v1).

Checks per-feed data freshness in the live DB and upserts one row per feed
into pipeline_heartbeat. A missed CronJob, dead upstream, or broken write
path all show up as stale last_data_ts -> fresh=false (Grafana/alertable).

v1 semantics (feed freshness, written by this job at 01:45 UTC):
  last_check    = when this check ran
  last_data_ts  = max(ts) of the feed's data
  fresh         = age(last_data_ts) <= max_age_hours
  rows_total    = total rows in the feed's table

v2 (per-JOB last_ok written by each fetcher) lands with the next fetcher
image rebuilds. Exit code is 0 even with stale feeds (staleness is DATA,
visible via the table); nonzero only if the DB itself is unreachable.
"""
import os
import sys

import psycopg

# (feed, freshness SQL -> single max(ts), count SQL, max_age_hours)
# Thresholds deliberately generous: weekends + multi-day market holidays
# must not page anyone. Price feeds 96h, news 48h (RSS publishes daily),
# FRED 168h (DFF lags), FII/DII 120h (trading days only).
FEEDS = [
    ("yfinance_batch", "SELECT max(ts) FROM index_prices WHERE ticker='^GSPC'",
     "SELECT count(*) FROM index_prices", 96),
    ("nse_indices", "SELECT max(ts) FROM index_prices WHERE ticker='^NSEI'",
     "SELECT count(*) FROM index_prices", 96),
    ("fx", "SELECT max(ts) FROM fx_rates",
     "SELECT count(*) FROM fx_rates", 96),
    ("yields", "SELECT max(ts) FROM bond_yields",
     "SELECT count(*) FROM bond_yields", 96),
    ("fred_rates", "SELECT max(ts) FROM interest_rates",
     "SELECT count(*) FROM interest_rates", 168),
    ("news_staging", "SELECT max(ts) FROM raw_headlines",
     "SELECT count(*) FROM raw_headlines", 48),
    ("sentiment", "SELECT max(ts) FROM news_sentiment",
     "SELECT count(*) FROM news_sentiment", 48),
    ("ta_indicators", "SELECT max(ts) FROM technical_indicators",
     "SELECT count(*) FROM technical_indicators", 96),
    ("fiidii_flows", "SELECT max(ts) FROM capital_flows",
     "SELECT count(*) FROM capital_flows", 120),
    ("intraday", "SELECT max(ts) FROM intraday_prices",
     "SELECT count(*) FROM intraday_prices", 96),
    # stock_prices/stock_scores were 48h until v5 (2026-10-18): every Sunday the Friday
    # bar is ~57h old, so they paged on weekends against the policy above. 96h like the
    # other price feeds.
    ("stock_prices", "SELECT max(ts) FROM stock_prices",
     "SELECT count(*) FROM stock_prices", 96),
    ("stock_fundamentals", "SELECT max(ts) FROM stock_fundamentals",
     "SELECT count(*) FROM stock_fundamentals", 192),
    ("stock_scores", "SELECT max(ts) FROM stock_scores",
     "SELECT count(*) FROM stock_scores", 96),
    # rbi-fetcher writes its own row with fresh=true, which can never go stale on its
    # own; checking it here makes a dead rbi job visible. Policy rows carry the fetch date.
    ("rbi", "SELECT max(ts) FROM interest_rates WHERE left(rate_name, 3) = 'IN_'",
     "SELECT count(*) FROM interest_rates WHERE left(rate_name, 3) = 'IN_'", 96),
    # v6 (2026-10-19): last AI note that PASSED the number check (ML-7a, ADR 0022). This job runs at
    # 07:15 IST, the note at 07:40 IST -> a good day is ~24h old; 56h = two missed days before it pages.
    ("llm_narrative", "SELECT max(ts) FROM llm_narratives WHERE status = 'ok'",
     "SELECT count(*) FROM llm_narratives", 56),
]

# v6: the shared llm-gateway (../llm-gateway) — a live readiness probe (also proves its DB connection).
# last_data_ts = last time it answered; fresh = answered now.
GATEWAY_URL = os.environ.get("LLM_GATEWAY_HEALTH",
                             "http://llm-gateway.llm.svc.cluster.local:4000/health/readiness")
GATEWAY_UPSERT = """
INSERT INTO pipeline_heartbeat
  (feed, last_check, last_data_ts, fresh, rows_total, max_age_hours)
VALUES ('llm_gateway', now(), CASE WHEN %(ok)s THEN now() END, %(ok)s, 0, 24)
ON CONFLICT (feed) DO UPDATE SET
  last_check = now(),
  last_data_ts = COALESCE(EXCLUDED.last_data_ts, pipeline_heartbeat.last_data_ts),
  fresh = EXCLUDED.fresh
"""


def gateway_ok(url=None, opener=None):
    """True if the gateway answers its readiness probe with 200 within 10 s. Never raises."""
    import urllib.request
    try:
        with (opener or urllib.request.urlopen)(url or GATEWAY_URL, timeout=10) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001 — any failure just means "not healthy"
        return False

UPSERT = """
INSERT INTO pipeline_heartbeat
  (feed, last_check, last_data_ts, fresh, rows_total, max_age_hours)
VALUES (%s, now(), %s, %s, %s, %s)
ON CONFLICT (feed) DO UPDATE SET
  last_check = now(),
  last_data_ts = EXCLUDED.last_data_ts,
  fresh = EXCLUDED.fresh,
  rows_total = EXCLUDED.rows_total,
  max_age_hours = EXCLUDED.max_age_hours
"""


def main() -> int:
    conn = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", ""),
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    fresh_n = 0
    total_extra = 0
    try:
        with conn:
            with conn.cursor() as cur:
                # Hypertable count(*) spawns parallel workers whose lock-table
                # needs can exhaust shared memory under concurrent load
                # (OutOfMemory/max_locks hint, 2026-10-04). The health job never
                # needs parallelism — force serial execution.
                cur.execute("SET max_parallel_workers_per_gather = 0")
                for feed, max_sql, cnt_sql, max_age in FEEDS:
                    cur.execute(max_sql)
                    last_ts = cur.fetchone()[0]
                    cur.execute(cnt_sql)
                    rows = cur.fetchone()[0]
                    if last_ts is None:
                        fresh = False
                    else:
                        # Age computed in SQL: no tz math on the pod.
                        cur.execute(
                            "SELECT EXTRACT(EPOCH FROM (now() - %s)) / 3600 <= %s",
                            (last_ts, max_age),
                        )
                        fresh = bool(cur.fetchone()[0])
                    cur.execute(UPSERT, (feed, last_ts, fresh, rows, max_age))
                    if fresh:
                        fresh_n += 1
                    print(f"{feed:16s} fresh={str(fresh):5s} "
                          f"last={last_ts} rows={rows} max_age_h={max_age}")
                if os.environ.get("LLM_GATEWAY_CHECK", "1") == "1":
                    ok = gateway_ok()
                    cur.execute(GATEWAY_UPSERT, {"ok": ok})
                    fresh_n += ok
                    total_extra = 1
                    print(f"{'llm_gateway':16s} fresh={str(ok):5s} (readiness probe)")
    finally:
        conn.close()
    print(f"HEALTH-OK fresh={fresh_n}/{len(FEEDS) + total_extra}")
    return 0


if __name__ == "__main__":
    from mdjob import run_main   # one job_runs row per execution; never fails the job
    sys.exit(run_main("pipeline-health", main))
