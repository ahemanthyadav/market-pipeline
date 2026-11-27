"""RBI daily rates fetcher (findings Tier 2 #8; ops manual §12.1 — closes the
last real India data gap: G-Sec yields + policy rates).

Source: the RBI mobile-site "Current Rates" widget (m.rbi.org.in) — official,
no key, no session dance. One page carries the whole India rates complex:
policy rates (repo/SDF/MSF/bank/reverse-repo), CRR/SLR, five G-Sec benchmark
yields (mapped to tenor buckets by maturity year — the benchmark SECURITY name
changes over time, the bucket logic doesn't), and T-bill auction cut-offs.

Writes (house DELETE+INSERT idempotency on the existing hypertables):
- interest_rates: IN_REPO, IN_SDF, IN_MSF, IN_BANK_RATE, IN_REVERSE_REPO,
  IN_CRR, IN_SLR  (ts = fetch date; step series between MPC meetings)
- bond_yields: IN_GS_{3,5,10,15,30}Y (ts = the page's '# as on' date — G-Sec
  marks are EOD T-1), IN_TBILL_{91,182,364}D (ts = fetch date)
- pipeline_heartbeat feed='rbi' (per-job heartbeat v2 semantics — the new-job
  pattern; the health job's hard-coded FEEDS list adopts it at its next
  natural image rebuild)

Drift tolerance (api-design): every section parses independently; a missing
section WARNs and skips; exit 1 only if ZERO rows total. lxml text_content()
drops the page's stale HTML-comment date (a raw-regex would read January in
October). Values carry '#'/'*' markers — strip before float().

Connection discipline: house pattern — autocommit reads, one
conn.transaction() per write unit, reconnect-retry once on OperationalError.

Env: PGHOST, PGUSER, PGPASSWORD, PGDATABASE. OK marker: RBI-OK.
"""
import os
import re
import sys
from datetime import datetime, timezone

import psycopg
import requests
from lxml import html as lxml_html

URL = "https://m.rbi.org.in/"
UA = {"User-Agent": "Mozilla/5.0 (marketdata homelab pipeline)"}

# interest_rates rows: (rate_name, label-or-regex on normalized page text)
POLICY = [
    ("IN_REPO", r"Policy\s+Repo\s+Rate\s*:?\s*([\d.]+)"),
    ("IN_SDF", r"Standing\s+Deposit\s+Facility\s+Rate\s*:?\s*([\d.]+)"),
    ("IN_MSF", r"Marginal\s+Standing\s+Facility\s+Rate\s*:?\s*([\d.]+)"),
    ("IN_BANK_RATE", r"Bank\s+Rate\s*:?\s*([\d.]+)"),
    ("IN_REVERSE_REPO", r"Fixed\s+Reverse\s+Repo\s+Rate\s*:?\s*([\d.]+)"),
    ("IN_CRR", r"\bCRR\b\s*:?\s*([\d.]+)"),
    ("IN_SLR", r"\bSLR\b\s*:?\s*([\d.]+)"),
]
GS_ROW_RE = re.compile(r"([\d.]+)%\s*GS\s+(\d{4})\s*:?\s*([\d.]+)%")
TBILL_RE = re.compile(r"(\d+)\s*day\s*T-?bills?\s*:?\s*([\d.]+)%")
AS_ON_RE = re.compile(r"#\s*as\s+on\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})")
GS_TENORS = [3, 5, 10, 15, 30]


def connect():
    conn = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    conn.autocommit = True
    return conn


def fetch_page():
    for attempt in (1, 2):
        try:
            r = requests.get(URL, headers=UA, timeout=30)
            r.raise_for_status()
            return r.text
        except Exception as exc:  # noqa: BLE001
            if attempt == 2:
                raise
            print(f"WARN fetch failed ({exc}); retrying once", flush=True)


def parse_page(page):
    """-> (policy{name:value}, gs{tenor:value}, tb{days:value}, gs_ts)"""
    text = re.sub(r"\s+", " ", lxml_html.fromstring(page).text_content())
    policy = {}
    for name, pattern in POLICY:
        m = re.search(pattern, text)
        if m:
            policy[name] = float(m.group(1))
        else:
            print(f"WARN policy row not found: {name}", flush=True)
    gs, tb = {}, {}
    this_year = datetime.now(timezone.utc).year
    for coupon, maturity, ytm in GS_ROW_RE.findall(text):
        years = int(maturity) - this_year
        tenor = min(GS_TENORS, key=lambda t: abs(t - years))
        gs[tenor] = float(ytm)  # later rows overwrite — page is bucket-ordered
    if not gs:
        print("WARN no G-Sec rows parsed", flush=True)
    for days, cutoff in TBILL_RE.findall(text):
        tb[int(days)] = float(cutoff)
    if not tb:
        print("WARN no T-bill rows parsed", flush=True)
    gs_ts = None
    m = AS_ON_RE.search(text)
    if m:
        gs_ts = datetime.strptime(m.group(1), "%B %d, %Y").replace(
            tzinfo=timezone.utc)
    else:
        print("WARN G-Sec 'as on' date not parsed; using fetch date", flush=True)
    return policy, gs, tb, gs_ts


def main() -> int:
    page = fetch_page()
    policy, gs, tb, gs_ts = parse_page(page)
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                microsecond=0)
    rows = []
    for name, value in policy.items():
        rows.append(("interest_rates", name, today, value))
    gs_ts = gs_ts or today
    for tenor, value in gs.items():
        rows.append(("bond_yields", f"IN_GS_{tenor}Y", gs_ts, value))
    for days, value in tb.items():
        rows.append(("bond_yields", f"IN_TBILL_{days}D", today, value))
    if not rows:
        print("RBI-FAIL zero rows parsed", flush=True)
        return 1
    conn = connect()

    def write_rows(conn):
        n = 0
        with conn.transaction():
            with conn.cursor() as cur:
                for table, key, ts, value in rows:
                    keycol = "rate_name" if table == "interest_rates" \
                        else "instrument"
                    cur.execute(f"DELETE FROM {table} WHERE {keycol}=%s AND "
                                "ts=%s", (key, ts))
                    if table == "interest_rates":
                        cur.execute("INSERT INTO interest_rates (rate_name, ts, "
                                    "value) VALUES (%s,%s,%s)", (key, ts, value))
                    else:
                        cur.execute("INSERT INTO bond_yields (instrument, ts, "
                                    "yield) VALUES (%s,%s,%s)", (key, ts, value))
                    n += 1
                cur.execute(
                    "INSERT INTO pipeline_heartbeat (feed, last_check, "
                    "last_data_ts, fresh, rows_total, max_age_hours) "
                    "VALUES ('rbi', now(), %s, true, %s, 96) "
                    "ON CONFLICT (feed) DO UPDATE SET last_check=now(), "
                    "last_data_ts=EXCLUDED.last_data_ts, fresh=true, "
                    "rows_total=EXCLUDED.rows_total",
                    (gs_ts, n))
        return n

    try:
        written = write_rows(conn)
    except psycopg.OperationalError as exc:
        print(f"WARN conn dropped ({exc}); reconnecting, retrying once",
              flush=True)
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        conn = connect()
        written = write_rows(conn)
    conn.close()
    print(f"RBI-OK rows={written} policy={len(policy)} gs={len(gs)} "
          f"tbills={len(tb)} gs_date={gs_ts:%Y-%m-%d}", flush=True)
    return 0


if __name__ == "__main__":
    from mdjob import run_main   # one job_runs row per execution; never fails the job
    sys.exit(run_main("rbi-fetcher", main))
