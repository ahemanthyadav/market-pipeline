"""Universe refresh (Phase B, expanded 2026-10-27 per ADR 0029):
S&P 1500 (Wikipedia: 500 + MidCap 400 + SmallCap 600) + Nifty Total Market
(NSE archives CSVs: Nifty 500 + Microcap 250).

Upserts universe_constituents (security-master fields) + ticker_names; symbols
absent from a fresh list flip active=false (history retained). Canary via
UNIVERSE_LIMIT (per universe). Graceful degrade: if a source fetch fails but we
already have rows, WARN + exit 0 (stale beats empty); exit 1 only if a source
fails AND its table would stay empty.

CIK discipline: the Wikipedia tables are parsed header-driven (column orders
differ across the three pages) and any missing CIK is filled from the SEC's
official ticker map (company_tickers.json) — the EDGAR fundamentals leg only
runs for symbols with a CIK.
"""
import csv
import io
import os
import sys
from functools import partial

import lxml.html
import psycopg
import requests

WIKI_SOURCES = (
    ("SP500", "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", 495),
    ("SP400", "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies", 390),
    ("SP600", "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies", 580),
)
# niftyindices.com now serves a JS HTML shell at the old CSV path (2026-10-04);
# the NSE archives endpoint is the working CSV source — and it carries Industry.
NSE_SOURCES = (
    ("NIFTY500", "https://archives.nseindia.com/content/indices/ind_nifty500list.csv", 490),
    ("NIFTYMICROCAP250",
     "https://archives.nseindia.com/content/indices/ind_niftymicrocap250_list.csv", 240),
)
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
UA = {"User-Agent": "Mozilla/5.0 (homelab market-data pipeline)"}

_sec_map = None


def sec_cik_map():
    """{ticker: zero-unpadded cik str} from the SEC's official map (cached)."""
    global _sec_map
    if _sec_map is None:
        resp = requests.get(SEC_TICKERS_URL, headers=UA, timeout=60)
        resp.raise_for_status()
        _sec_map = {v["ticker"].upper(): str(v["cik_str"])
                    for v in resp.json().values()}
    return _sec_map


def wiki_universe(universe, url, limit):
    """Parse a Wikipedia constituents table directly (lxml.html) — pd.read_html
    proved unreliable on this markup (F22). Header-driven: the 400/600 pages
    order columns differently from the 500 page."""
    resp = requests.get(url, headers=UA, timeout=60)
    resp.raise_for_status()
    table = lxml.html.fromstring(resp.content).get_element_by_id("constituents")
    header = [th.text_content().strip().lower()
              for th in table.xpath(".//tr[1]/th")]

    def col(*names):
        for n in names:
            for i, h in enumerate(header):
                if n in h:
                    return i
        return None

    i_sym = col("symbol", "ticker")
    i_name = col("security", "company")
    i_sector = col("gics sector", "sector")
    i_cik = col("cik")
    if i_sym is None or i_name is None:
        raise RuntimeError(f"{universe}: constituents header not recognised: {header}")
    rows = []
    for tr in table.xpath(".//tbody/tr"):
        cells = [td.text_content().strip() for td in tr.xpath("./th|./td")]
        if len(cells) <= max(i_sym, i_name) or not cells[i_sym] \
                or cells[i_sym].lower() == "symbol":
            continue
        sym = cells[i_sym].replace(".", "-", 1)  # BRK.B -> BRK-B (Yahoo convention)
        cik = cells[i_cik] if i_cik is not None and len(cells) > i_cik else ""
        if not cik:
            cik = sec_cik_map().get(sym.upper(), "")
        sector = (cells[i_sector] if i_sector is not None
                  and len(cells) > i_sector else "") or None
        rows.append((universe, sym, cells[i_name], sector, None, cik, "US", "USD"))
    return rows[:limit] if limit else rows


def nse_universe(universe, url, limit):
    resp = requests.get(url, headers=UA, timeout=60)
    resp.raise_for_status()
    if "text/html" in resp.headers.get("content-type", "").lower():
        raise RuntimeError(f"{universe} list returned HTML (block page?), not CSV")
    rdr = csv.DictReader(io.StringIO(resp.text))
    rows = []
    for r in rdr:
        sym = (r.get("Symbol") or "").strip()
        if not sym or (r.get("Series") or "").strip() != "EQ":
            continue
        rows.append((universe, f"{sym}.NS", (r.get("Company Name") or sym).strip(),
                     (r.get("Industry") or "").strip() or None,
                     (r.get("ISIN Code") or "").strip() or None,
                     None, "NSE", "INR"))
    return rows[:limit] if limit else rows


def make_fetchers():
    """[(universe, fetcher, min_count)] over all five sources."""
    out = [(u, partial(wiki_universe, u, url), n) for u, url, n in WIKI_SOURCES]
    out += [(u, partial(nse_universe, u, url), n) for u, url, n in NSE_SOURCES]
    return out


def main() -> int:
    limit = int(os.environ.get("UNIVERSE_LIMIT", "0") or 0) or None
    conn = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    total = {}
    empty_after_fail = []
    with conn:
        with conn.cursor() as cur:
            for universe, fetcher, _min in make_fetchers():
                try:
                    rows = fetcher(limit)
                except Exception as exc:  # noqa: BLE001
                    cur.execute("SELECT count(*) FROM universe_constituents WHERE universe=%s",
                                (universe,))
                    have = cur.fetchone()[0]
                    print(f"WARN {universe} fetch failed ({exc}); existing rows={have}",
                          flush=True)
                    if have == 0:
                        empty_after_fail.append(universe)
                    continue
                cur.execute("UPDATE universe_constituents SET active=false, last_seen=now() "
                            "WHERE universe=%s", (universe,))
                for (uni, sym, name, sector, isin, cik, exch, ccy) in rows:
                    cur.execute(
                        """INSERT INTO universe_constituents
                           (universe, symbol, name, sector, isin, cik, exchange, currency,
                            active, first_seen, last_seen)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s, TRUE, now(), now())
                           ON CONFLICT (universe, symbol) DO UPDATE SET
                             name=EXCLUDED.name, sector=EXCLUDED.sector,
                             isin=EXCLUDED.isin, cik=EXCLUDED.cik,
                             exchange=EXCLUDED.exchange, currency=EXCLUDED.currency,
                             active=TRUE, last_seen=now()""",
                        (uni, sym, name, sector, isin, cik, exch, ccy))
                    cur.execute(
                        "INSERT INTO ticker_names (ticker, name) VALUES (%s,%s) "
                        "ON CONFLICT (ticker) DO UPDATE SET name=EXCLUDED.name",
                        (sym, name))
                cur.execute("SELECT count(*) FROM universe_constituents "
                            "WHERE universe=%s AND active", (universe,))
                total[universe] = cur.fetchone()[0]
                print(f"{universe}: {len(rows)} fetched, {total[universe]} active", flush=True)
                if not limit:
                    # G-2a / EV-1: append-only weekly record of who was in the index, so
                    # future backtests are not limited to today's survivors. One row per
                    # (universe, date, symbol); a same-day re-run adds nothing.
                    cur.execute("INSERT INTO universe_membership_snapshots "
                                "(universe, snapshot_date, symbol, sector) "
                                "SELECT universe, current_date, symbol, sector "
                                "FROM universe_constituents WHERE universe=%s AND active "
                                "ON CONFLICT DO NOTHING", (universe,))
                    print(f"{universe}: snapshot {cur.rowcount} rows", flush=True)
    conn.close()
    if empty_after_fail:
        print(f"UNIVERSE-FAIL empty after fetch failure: {empty_after_fail}", flush=True)
        return 1
    if limit:
        print(f"UNIVERSE-CANARY-OK {total}", flush=True)
    else:
        mins = {u: n for u, _url, n in (*WIKI_SOURCES, *NSE_SOURCES)}
        low = [u for u, n in mins.items() if total.get(u, 0) < n]
        print(f"UNIVERSE-OK active={total} "
              f"{'PASS' if not low else f'WARN-LOW-COUNT {low}'}", flush=True)
        # Hard-fail only if a FLAGSHIP universe came back empty; the expansion
        # universes degrade gracefully (stale beats empty, same as a fetch fail).
        return 0 if total.get("SP500", 0) and total.get("NIFTY500", 0) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
