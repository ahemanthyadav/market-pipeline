#!/usr/bin/env python3
"""ML-1 anomaly detection v1 — data quality + node health flags.

Shares the workstation PSQL_CMD pattern with ev0.py / ml0.py.
Writes to app.data_anomalies: owns its window (DELETE the scanned window, then upsert).

Detection rules (all rule-based, no model):
  bad_bar        — |1-day ret| > 40% OR close <= 0 OR high < max(open,close)
                   OR low > min(open,close)   (schema kind: bad_bar)
  feed_drop      — pipeline_heartbeat fresh = false                      (schema kind: feed_drop)
  row_count_drop — daily stock_prices row count < 70% of trailing 20-day median  (row_count_drop)
  scraper_drop   — feed had data in trailing 7 days but none in last 2 days      (scraper_drop)

Usage (workstation; DB via $PSQL_CMD like ev0.py):
  PSQL_CMD='kubectl -n market-data exec -i timescaledb-0 -- env PGPASSWORD=… psql -U postgres -d postgres' \\
    python ops-tools/ml1.py run     # detect + write to app.data_anomalies
  PSQL_CMD='…' python ops-tools/ml1.py show   # list recent anomalies
"""
import argparse
import datetime as dt
import json
import os
import sys

import pandas as pd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "manifests" / "ml"))
from pgcompat import psql  # noqa: E402  (workstation PSQL_CMD or in-cluster psycopg)

OUT = "out/ml1"
BAD_BAR_PCT = 0.40        # |1-day return| threshold
ROW_DROP_PCT = 0.30       # row count < 70% of trailing median
ROW_WINDOW = 20           # trailing days for rolling median
SCRAPER_WINDOW_DAYS = 7   # was producing within this window
SCRAPER_SILENCE_DAYS = 2  # but silent for at least this many days


# ---------------------------------------------------------------- DB helpers



def _q(s):
    """SQL string literal (shell quoting happens in pgcompat.psql)."""
    return "'" + str(s).replace("'", "''") + "'"


def write_anomalies(anomalies):
    """Insert anomaly rows into app.data_anomalies (idempotent). Returns rows written."""
    if not anomalies:
        return 0
    n = 0
    for a in anomalies:
        kind = _q(a["kind"])
        subject = _q(a["subject"])
        ts = _q(pd.Timestamp(a["ts"]).isoformat())
        score = "NULL" if a["score"] is None else repr(float(a["score"]))
        details = _q(json.dumps(a.get("details", {}), default=str))
        sql = (f"INSERT INTO app.data_anomalies (kind, subject, ts, score, details) "
               f"VALUES ({kind}, {subject}, {ts}, {score}, {details}::jsonb) "
               f"ON CONFLICT (kind, subject, ts) DO UPDATE SET score = EXCLUDED.score, "
               f"details = EXCLUDED.details")
        raw = psql(sql).strip()
        if raw == "INSERT 0 1":
            n += 1
    return n


# ---------------------------------------------------------------- detection (pure, testable)

def detect_bad_bars(df):
    """Flag bad bars in a price dataframe.

    Input columns: symbol, ts, open, high, low, close[, volume]
    Returns list of anomaly dicts (kind='bad_bar', subject=symbol).
    """
    df = df.sort_values(["symbol", "ts"]).copy()
    # Returns on the split/dividend-adjusted series when present (2026-11-12): raw
    # closes flagged every recorded split as a "jump". OHLC sanity stays on raw bars.
    px = df["adj_close"].fillna(df["close"]) if "adj_close" in df else df["close"]
    df["prev_close"] = px.groupby(df["symbol"]).shift(1)
    df["ret"] = px / df["prev_close"] - 1

    anomalies = []
    for _, r in df.iterrows():
        reasons, score = [], 0.0
        if pd.notna(r["close"]) and r["close"] <= 0:
            reasons.append("non_positive_close")
            score = max(score, 1.0)
        if pd.notna(r["ret"]) and abs(r["ret"]) > BAD_BAR_PCT:
            reasons.append(f"jump_{r['ret']:+.2%}")
            score = max(score, abs(r["ret"]))
        if (pd.notna(r["high"]) and pd.notna(r["open"]) and pd.notna(r["close"])
                and r["high"] < max(r["open"], r["close"])):
            reasons.append("high_below_open_close")
            score = max(score, 0.5)
        if (pd.notna(r["low"]) and pd.notna(r["open"]) and pd.notna(r["close"])
                and r["low"] > min(r["open"], r["close"])):
            reasons.append("low_above_open_close")
            score = max(score, 0.5)
        if reasons:
            anomalies.append({
                "kind": "bad_bar",
                "subject": r["symbol"],
                "ts": r["ts"],
                "score": score,
                "details": {"reasons": reasons, "close": float(r["close"]),
                            "prev_close": (float(r["prev_close"]) if pd.notna(r["prev_close"]) else None),
                            "ret": (float(r["ret"]) if pd.notna(r["ret"]) else None),
                            "open": (float(r["open"]) if pd.notna(r["open"]) else None),
                            "high": (float(r["high"]) if pd.notna(r["high"]) else None),
                            "low": (float(r["low"]) if pd.notna(r["low"]) else None)},
            })
    return anomalies


def detect_feed_drops(hb):
    """Flag feeds with fresh = false in pipeline_heartbeat.

    Input columns: feed, fresh, last_check, last_data_ts, rows_total, max_age_hours
    Returns list of anomaly dicts (kind='feed_drop', subject=feed).
    """
    anomalies = []
    stale = hb[~hb["fresh"].fillna(True)]
    for _, r in stale.iterrows():
        ts = r["last_check"] if pd.notna(r["last_check"]) else r.get("last_data_ts")
        anomalies.append({
            "kind": "feed_drop",
            "subject": r["feed"],
            "ts": ts,
            "score": 1.0,
            "details": {"last_data_ts": str(r.get("last_data_ts")),
                        "rows_total": int(r.get("rows_total", 0) or 0),
                        "max_age_hours": int(r.get("max_age_hours", 0) or 0)},
        })
    return anomalies


def detect_row_count_drops(daily_counts):
    """Flag days where a market's row count < 70% of its trailing 20-day median.

    Input columns: d (date-like), cnt (int)[, market]. Counted PER MARKET
    (2026-11-12): pooled counts flagged every US or Indian holiday (one market
    closed = a third / two thirds of the pool). A closed market has no row for that
    day, so holidays drop out naturally. Each market's NEWEST day is skipped — it
    may still be in flight (the writer fires mid-session somewhere).
    Returns list of anomaly dicts (kind='row_count_drop', subject='stock_prices[:MKT]').
    """
    if "market" not in daily_counts:
        daily_counts = daily_counts.assign(market="")
    anomalies = []
    for market, g in daily_counts.groupby("market"):
        g = g.sort_values("d").iloc[:-1].copy()
        g["median"] = g["cnt"].rolling(ROW_WINDOW, min_periods=5).median()
        for _, r in g.iterrows():
            if pd.isna(r["median"]):
                continue
            if r["cnt"] < (1 - ROW_DROP_PCT) * r["median"]:
                anomalies.append({
                    "kind": "row_count_drop",
                    "subject": f"stock_prices:{market}" if market else "stock_prices",
                    "ts": r["d"],
                    "score": float(r["cnt"] / r["median"]),
                    "details": {"count": int(r["cnt"]), "median_count": float(r["median"])},
                })
    return anomalies


def detect_scraper_drops(hb, now=None):
    """Flag feeds that had data in trailing N days but none in last M days.

    Input columns: feed, last_data_ts
    Returns list of anomaly dicts (kind='scraper_drop', subject=feed).
    """
    if now is None:
        now = pd.Timestamp.now(tz="UTC")
    anomalies = []
    for _, r in hb.iterrows():
        ldt = r.get("last_data_ts")
        if pd.isna(ldt):
            continue
        ldt = pd.Timestamp(ldt)
        if ldt.tzinfo is None:
            ldt = ldt.tz_localize("UTC")
        days_since = (now - ldt).days
        # Per-feed cadence (2026-11-12): a weekly feed (max_age_hours 192) is not
        # "silent" after 2 days. Silence threshold = max(2 d, the feed's own max age).
        mah = r.get("max_age_hours")
        silence = max(SCRAPER_SILENCE_DAYS,
                      int(-(-float(mah) // 24)) if mah is not None and pd.notna(mah) else 0)
        if silence <= days_since <= silence + SCRAPER_WINDOW_DAYS:
            anomalies.append({
                "kind": "scraper_drop",
                "subject": r["feed"],
                "ts": ldt,
                "score": days_since / (silence + SCRAPER_WINDOW_DAYS),
                "details": {"days_since_data": int(days_since), "silence_days": int(silence),
                            "last_data_ts": str(ldt)},
            })
    return anomalies


# ---------------------------------------------------------------- fetch + detect

def fetch_prices(days=90):
    """Return stock_prices rows from the last N days as a dataframe."""
    cols = ["symbol", "ts", "open", "high", "low", "close", "volume", "adj_close"]
    sql = (f"SELECT {', '.join(cols)} "
           f"FROM stock_prices WHERE ts >= NOW() - INTERVAL '{days} days' "
           f"ORDER BY symbol, ts")
    rows = psql(sql)
    if not rows.strip():
        return pd.DataFrame(columns=cols)
    data = [line.split(",") for line in rows.strip().split("\n") if line]
    df = pd.DataFrame(data, columns=cols)
    for c in ["open", "high", "low", "close", "volume", "adj_close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


def fetch_heartbeat():
    """Return pipeline_heartbeat rows as a dataframe."""
    sql = ("SELECT feed, last_check, last_data_ts, fresh, rows_total, max_age_hours "
           "FROM pipeline_heartbeat ORDER BY feed")
    rows = psql(sql)
    if not rows.strip():
        return pd.DataFrame(columns=["feed", "last_check", "last_data_ts", "fresh",
                                     "rows_total", "max_age_hours"])
    data = [line.split(",") for line in rows.strip().split("\n") if line]
    df = pd.DataFrame(data, columns=["feed", "last_check", "last_data_ts", "fresh",
                                     "rows_total", "max_age_hours"])
    df["fresh"] = df["fresh"].str.lower().isin(["true", "t", "1"])
    for c in ["rows_total", "max_age_hours"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ["last_check", "last_data_ts"]:
        df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")
    return df


def fetch_daily_row_counts(days=45):
    """Return daily row counts for stock_prices over the last N days."""
    sql = (f"SELECT DATE(ts) AS d, COUNT(*) AS cnt, "
           f"CASE WHEN symbol LIKE '%.NS' OR symbol LIKE '%.BO' THEN 'NSE' ELSE 'US' END AS market "
           f"FROM stock_prices WHERE ts >= NOW() - INTERVAL '{days} days' "
           f"GROUP BY 1, 3 ORDER BY 1")
    rows = psql(sql)
    if not rows.strip():
        return pd.DataFrame(columns=["d", "cnt", "market"])
    data = [line.split(",") for line in rows.strip().split("\n") if line]
    df = pd.DataFrame(data, columns=["d", "cnt", "market"])
    df["d"] = pd.to_datetime(df["d"], utc=True)
    df["cnt"] = pd.to_numeric(df["cnt"])
    return df


def run(days=90):
    """Run all detection rules, write results to app.data_anomalies. Returns total anomalies."""
    all_anomalies = []

    # bad_bar
    px = fetch_prices(days=days)
    if not px.empty:
        all_anomalies.extend(detect_bad_bars(px))
        print(f"bad_bar: {len([a for a in all_anomalies if a['kind']=='bad_bar'])} anomalies", flush=True)

    # feed_drop + scraper_drop
    hb = fetch_heartbeat()
    if not hb.empty:
        all_anomalies.extend(detect_feed_drops(hb))
        all_anomalies.extend(detect_scraper_drops(hb))
        n_fd = len([a for a in all_anomalies if a["kind"] == "feed_drop"])
        n_sd = len([a for a in all_anomalies if a["kind"] == "scraper_drop"])
        print(f"feed_drop: {n_fd} anomalies, scraper_drop: {n_sd} anomalies", flush=True)

    # row_count_drop
    counts = fetch_daily_row_counts(days=days + 10)
    if not counts.empty:
        all_anomalies.extend(detect_row_count_drops(counts))
        n_rc = len([a for a in all_anomalies if a["kind"] == "row_count_drop"])
        print(f"row_count_drop: {n_rc} anomalies", flush=True)

    # Own the window (house rule 4): every run recomputes the scanned window, so a
    # rule fix or a repaired bar clears stale rows instead of leaving them forever.
    psql(f"DELETE FROM app.data_anomalies WHERE kind IN ('bad_bar','row_count_drop') "
         f"AND ts >= NOW() - INTERVAL '{days + 10} days'")
    psql("DELETE FROM app.data_anomalies WHERE kind IN ('feed_drop','scraper_drop')")
    n = write_anomalies(all_anomalies)
    print(f"ML-1: {len(all_anomalies)} detected, {n} written to app.data_anomalies", flush=True)
    return n


def show(limit=50):
    """List recent anomalies from app.data_anomalies."""
    sql = (f"SELECT kind, subject, ts, score, details FROM app.data_anomalies "
           f"ORDER BY ts DESC LIMIT {limit}")
    rows = psql(sql)
    if not rows.strip():
        print("no anomalies")
        return
    print(f"{'kind':<18} {'subject':<20} {'ts':<30} {'score':<8} details")
    print("-" * 90)
    for line in rows.strip().split("\n"):
        parts = line.split(",", 4)
        kind, subject, ts, score = parts[:4]
        details = parts[4] if len(parts) > 4 else "{}"
        print(f"{kind:<18} {subject:<20} {ts:<30} {score:<8} {details[:80]}")


def main():
    ap = argparse.ArgumentParser(description="ML-1 anomaly detection v1 (shares PSQL_CMD pattern with ev0/ml0)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run all detection rules + write to app.data_anomalies")
    r.add_argument("--days", type=int, default=90, help="lookback days for price/row-count scans")
    s = sub.add_parser("show", help="list recent anomalies from app.data_anomalies")
    s.add_argument("--limit", type=int, default=50, help="max rows to show")
    a = ap.parse_args()
    if not (os.environ.get("PSQL_CMD") or os.environ.get("PGHOST")):
        sys.exit("set PSQL_CMD (workstation) or PGHOST/PGUSER/PGPASSWORD (in-cluster)")
    if a.cmd == "run":
        run(days=a.days)
    elif a.cmd == "show":
        show(limit=a.limit)


if __name__ == "__main__":
    main()
