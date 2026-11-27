"""Daily market-news fetcher: RSS headlines staged for TX2 sentiment scoring.

Pulls a list of business/market RSS feeds, stores (headline, source, ts) into
raw_headlines with scored=FALSE. The sentiment job (Phase 4) scores them later.
Per-feed failures only WARN (feeds change URLs often); the job fails only if
zero headlines are stored. Same-day (headline, source) duplicates are skipped.

Env: PGHOST, PGUSER, PGPASSWORD, PGDATABASE. FEEDS optionally overrides the
default feed list (comma-separated name|url pairs).
"""

import calendar
import os
import sys
from datetime import datetime, timezone

import feedparser
import psycopg

DEFAULT_FEEDS = [
    ("EconomicTimes-Markets",
     "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Moneycontrol-Latest",
     "https://www.moneycontrol.com/rss/latestnews.xml"),
    ("Moneycontrol-Markets",
     "https://www.moneycontrol.com/rss/marketreports.xml"),
    ("BusinessStandard-Markets",
     "https://www.business-standard.com/rss/markets-106.rss"),
    ("Reuters-Business",
     "https://feeds.reuters.com/reuters/businessNews"),
]

MAX_PER_FEED = 30


def parse_feeds() -> list:
    """Return [(headline, source, ts)] across all feeds."""
    spec = os.environ.get("FEEDS")
    feeds = DEFAULT_FEEDS
    if spec:
        feeds = []
        for chunk in spec.split(","):
            name, _, url = chunk.partition("|")
            if name and url:
                feeds.append((name.strip(), url.strip()))
    items = []
    for name, url in feeds:
        try:
            parsed = feedparser.parse(url, agent="market-data-pipeline/1.0")
            if parsed.bozo and not parsed.entries:
                print(f"WARN {name}: feed parse failed", flush=True)
                continue
            for entry in parsed.entries[:MAX_PER_FEED]:
                title = (entry.get("title") or "").strip()
                if not title:
                    continue
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                if published:
                    # feedparser's struct_time is UTC: timegm, never time.mktime
                    # (mktime reads it as LOCAL time — only correct on a UTC host).
                    ts = datetime.fromtimestamp(
                        calendar.timegm(published), tz=timezone.utc)
                else:
                    ts = datetime.now(timezone.utc)
                items.append((title[:500], name, ts))
            print(f"feed {name}: {min(len(parsed.entries), MAX_PER_FEED)} entries",
                  flush=True)
        except Exception as exc:  # noqa: BLE001 - per-feed failure must not kill the batch
            print(f"WARN {name} failed: {exc}", flush=True)
    return items


def write_items(items: list) -> int:
    conn = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    stored = 0
    with conn:
        with conn.cursor() as cur:
            for headline, source, ts in items:
                cur.execute(
                    "SELECT 1 FROM raw_headlines "
                    "WHERE headline = %s AND source = %s AND ts::date = %s::date",
                    (headline, source, ts),
                )
                if cur.fetchone():
                    continue
                cur.execute(
                    "INSERT INTO raw_headlines (headline, source, ts) "
                    "VALUES (%s, %s, %s)",
                    (headline, source, ts),
                )
                stored += 1
    conn.close()
    return stored


def main() -> int:
    items = parse_feeds()
    print(f"collected {len(items)} headlines", flush=True)
    if not items:
        print("ERROR: zero headlines collected", flush=True)
        return 1
    stored = write_items(items)
    print(f"stored {stored} new rows into raw_headlines", flush=True)
    return 0


if __name__ == "__main__":
    from mdjob import run_main   # one job_runs row per execution; never fails the job
    sys.exit(run_main("news-fetcher", main))
