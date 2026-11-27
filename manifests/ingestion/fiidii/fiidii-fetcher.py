"""Daily FII/DII flows via NSE's public fiidiiTradeReact JSON API.

NSE blocks bare requests: open a session on nseindia.com first (cookies),
then call the API with browser headers. Writes one row per category+date into
capital_flows: flow_type FII|DII, direction 'net', amount = net value (Rs cr),
segment = NSE category string. Idempotent DELETE+INSERT per
(flow_type, segment, ts). Runs 14:00 UTC (post NSE evening publication).

If NSE changes its API/cookies (it does, periodically), the job WARNs and
exits 0 on partial data — exit 1 only if zero rows are stored.

Env: PGHOST, PGUSER, PGPASSWORD, PGDATABASE.
"""

import os
import sys
from datetime import datetime, timezone

import psycopg
import requests

BASE = "https://www.nseindia.com"
API = BASE + "/api/fiidiiTradeReact"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) market-data-pipeline/1.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE + "/products-services/equity-derivatives-segment",
}


def fetch_flows() -> list:
    """Return [(flow_type, segment, ts, net)] rows."""
    session = requests.Session()
    session.headers.update(HEADERS)
    session.get(BASE, timeout=30).raise_for_status()  # cookie jar warm-up
    resp = session.get(API, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    # NSE returns either a bare list or a {"data": [...]} envelope
    entries = payload if isinstance(payload, list) else payload.get("data", [])
    rows = []
    for entry in entries:
        cat = (entry.get("category") or "").strip()
        upper = cat.upper()
        if "FII" in upper:
            flow_type = "FII"
        elif "DII" in upper:
            flow_type = "DII"
        else:
            print(f"WARN skipping category {cat!r}", flush=True)
            continue
        try:
            net = float(entry.get("netValue") or 0)
        except (TypeError, ValueError):
            print(f"WARN bad netValue for {cat!r}", flush=True)
            continue
        date_raw = (entry.get("date")
                    or (payload.get("date") if isinstance(payload, dict) else "")
                    or "")
        try:
            ts = datetime.strptime(date_raw.strip(), "%d-%b-%Y").replace(
                tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            print(f"WARN bad date {date_raw!r}, using now", flush=True)
            ts = datetime.now(timezone.utc)
        rows.append((flow_type, cat, ts, net))
    return rows


def write_rows(rows: list) -> int:
    conn = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    written = 0
    with conn:
        with conn.cursor() as cur:
            for flow_type, segment, ts, net in rows:
                cur.execute(
                    "DELETE FROM capital_flows "
                    "WHERE flow_type = %s AND segment = %s AND ts = %s",
                    (flow_type, segment, ts),
                )
                cur.execute(
                    "INSERT INTO capital_flows "
                    "(flow_type, direction, ts, amount, segment) "
                    "VALUES (%s, 'net', %s, %s, %s)",
                    (flow_type, ts, net, segment),
                )
                written += 1
    conn.close()
    return written


def main() -> int:
    try:
        rows = fetch_flows()
    except Exception as exc:  # noqa: BLE001 - NSE API changes must degrade, not crash silent
        print(f"ERROR NSE fetch failed: {exc}", flush=True)
        return 1
    print(f"fetched {len(rows)} flow rows", flush=True)
    if not rows:
        print("ERROR: zero rows fetched", flush=True)
        return 1
    written = write_rows(rows)
    print(f"wrote {written} rows into capital_flows", flush=True)
    return 0


if __name__ == "__main__":
    from mdjob import run_main   # one job_runs row per execution; never fails the job
    sys.exit(run_main("fiidii-fetcher", main))
