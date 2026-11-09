"""Research watcher (ADR 0031) — weekly collector of factor-investing papers.

Sources, by evidence tier (probed live 2026-10-27; RFS/OUP is bot-walled and
deliberately skipped — we do not masquerade past bot checks):
  1  Journal of Finance   (Wiley RSS)          — peer-reviewed
  1  J. Financial Economics (ScienceDirect RSS) — peer-reviewed
  2  NBER new working papers (back.nber.org)    — prestigious, NOT yet reviewed
  3  arXiv q-fin.PM / q-fin.ST (Atom API)       — preprints, radar only

Relevance = venue weight (tier 1: 30, tier 2: 20, tier 3: 10) + 5 per matched
factor keyword in the title. DETERMINISTIC — no LLM. Items are append-only
(ON CONFLICT DO NOTHING; rule 4: a re-run adds nothing). The Strategy Lab
shows them under "Research radar — unvetted"; nothing automated ever edits
the curated tiers (ADR 0031 hard rule).

Per-source failures WARN and continue (the F13 RSS lesson): a dead feed must
not kill the run.
"""
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

from mdjob import connect, job_run

UA = {"User-Agent": "marketdata-research-watcher (homelab; contact via repo)"}
TIMEOUT = 30

SOURCES = [
    ("jf", "Journal of Finance", 1,
     "https://onlinelibrary.wiley.com/feed/15406261/most-recent", "rss"),
    ("jfe", "Journal of Financial Economics", 1,
     "https://rss.sciencedirect.com/publication/science/0304405X", "rss"),
    ("nber", "NBER working paper", 2,
     "https://back.nber.org/rss/new.xml", "rss"),
    ("arxiv-pm", "arXiv q-fin.PM (preprint)", 3,
     "https://export.arxiv.org/api/query?search_query=cat:q-fin.PM"
     "&sortBy=submittedDate&sortOrder=descending&max_results=40", "atom"),
    ("arxiv-st", "arXiv q-fin.ST (preprint)", 3,
     "https://export.arxiv.org/api/query?search_query=cat:q-fin.ST"
     "&sortBy=submittedDate&sortOrder=descending&max_results=40", "atom"),
]
TIER_WEIGHT = {1: 30, 2: 20, 3: 10}

KEYWORDS = ("momentum", "value premium", "factor", "anomal", "cross-section",
            "quality", "profitability", "low volatility", "low-volatility",
            "betting against beta", "size premium", "small-cap", "reversal",
            "post-earnings", "earnings drift", "accrual", "book-to-market",
            "expected returns", "risk premi", "multifactor", "asset pricing",
            "return predictab", "stock selection", "quantitative equity")

ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"


def relevance(title, tier):
    t = (title or "").lower()
    hits = sum(1 for k in KEYWORDS if k in t)
    return TIER_WEIGHT[tier] + 5 * hits, hits


def parse_date(text):
    if not text:
        return None
    text = text.strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    m = re.search(r"\d{4}-\d{2}-\d{2}", text)
    return datetime.strptime(m.group(), "%Y-%m-%d").date() if m else None


def fetch_rss(url):
    root = ET.fromstring(requests.get(url, headers=UA, timeout=TIMEOUT).content)
    for item in root.iter("item"):
        yield {
            "ext_id": (item.findtext("guid") or item.findtext("link") or
                       item.findtext("title") or "")[:300],
            "title": (item.findtext("title") or "").strip(),
            "authors": (item.findtext(f"{DC}creator") or "").strip()[:300] or None,
            "published": parse_date(item.findtext("pubDate")
                                    or item.findtext(f"{DC}date")),
            "url": (item.findtext("link") or "").strip()[:500] or None,
        }


def fetch_atom(url):
    root = ET.fromstring(requests.get(url, headers=UA, timeout=TIMEOUT).content)
    for e in root.iter(f"{ATOM}entry"):
        authors = ", ".join(a.findtext(f"{ATOM}name") or ""
                            for a in e.iter(f"{ATOM}author"))
        yield {
            "ext_id": (e.findtext(f"{ATOM}id") or "")[:300],
            "title": " ".join((e.findtext(f"{ATOM}title") or "").split()),
            "authors": authors[:300] or None,
            "published": parse_date(e.findtext(f"{ATOM}published")),
            "url": (e.findtext(f"{ATOM}id") or "").strip()[:500] or None,
        }


def main() -> int:
    with job_run("research-watcher") as run:
        conn = connect()
        rows, failed = [], 0
        for source, venue, tier, url, kind in SOURCES:
            try:
                items = list((fetch_atom if kind == "atom" else fetch_rss)(url))
            except Exception as exc:  # noqa: BLE001 - a dead feed must not kill the run
                print(f"WARN {source}: {exc.__class__.__name__}: {exc}", flush=True)
                failed += 1
                continue
            kept = 0
            for it in items:
                if not it["ext_id"] or not it["title"]:
                    continue
                rel, hits = relevance(it["title"], tier)
                # Preprints must earn their place with at least one keyword;
                # peer-reviewed venues are kept regardless (title screening
                # misses clever titles, and tier-1 volume is tiny).
                if tier == 3 and hits == 0:
                    continue
                topics = ",".join(k for k in KEYWORDS if k in it["title"].lower())
                rows.append((source, it["ext_id"], it["title"][:500], it["authors"],
                             venue, tier, it["published"], it["url"],
                             topics[:300] or None, rel))
                kept += 1
            print(f"{source}: {len(items)} items, kept {kept}", flush=True)
        written = 0
        with conn.transaction():
            with conn.cursor() as cur:
                for r in rows:
                    cur.execute(
                        "INSERT INTO research_items (source, ext_id, title, authors, "
                        "venue, tier, published, url, topics, relevance) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (source, ext_id) DO NOTHING", r)
                    written += cur.rowcount
        conn.close()
        run.rows = written
        print(f"RESEARCH-OK sources={len(SOURCES) - failed}/{len(SOURCES)} "
              f"candidates={len(rows)} new={written}", flush=True)
    return 0 if failed < len(SOURCES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
