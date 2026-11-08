"""India fundamentals history from NSE's own results filings (G-6, 2026-10-18).

yfinance only gives ~4 annual statements for NSE companies. NSE serves every quarterly and
annual result a listed company filed, with the exact filing timestamp (point-in-time):

  list    /api/corporates-financial-results?index=equities&symbol=S&period=Quarterly|Annual
          2005 -> 2024 records; three eras by record:
            oldNewFlag None (2005-2011)  -> archive HTML page (resultDetailedDataLink), Rs lakhs
            oldNewFlag 'N'  (2012-2024)  -> /api/corporates-financial-results-data JSON, Rs lakhs
  list    /api/integrated-filing-results?...&type=Integrated Filing- Financials
          2025 -> today, XBRL instance per filing (`xbrl`), rupees, quarter + YTD contexts

Writes financial_facts with source 'nse_cons' (consolidated) or 'nse_sa' (standalone — only for
periods that have no consolidated filing), filed = filing date, accession = NSE seq id,
duration from the filing's own period. Same rule as EDGAR (ADR 0015): a value is stored only if
it differs from the latest one held for that (metric, period, duration) — first release and
genuine restatements. Filings already stored (by accession) are never fetched again, so the
same job is both the backfill and the weekly incremental run.

Env: UNIVERSE_LIMIT, SYMBOLS (comma list, overrides universe and cursor), PAUSE (s, 1.3),
     MAX_MINUTES (stop cleanly and keep the cursor; 0 = no limit).
"""
import html as htmlmod
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import quote

import psycopg
import requests

BASE = "https://www.nseindia.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/124 Safari/537.36",
           "Accept": "application/json, text/plain, */*", "Referer": BASE + "/"}
JOB = "nse_fundamentals"
LAKH = 1e5

# metric -> (non-bank keys, bank keys, scale); first non-empty key wins
JSON_MAP = {
    # re_gro_inc = net sales + other operating income (TCS FY2014 booked 96 % of revenue as
    # "other operating income"); newer formats only have re_net_sale (= revenue from operations).
    "total_revenue": (["re_gro_inc", "re_net_sale"], ["re_tot_inc"], LAKH),
    "net_income": (["re_con_pro_loss", "re_net_profit"], ["re_con_pro_loss", "re_net_profit"], LAKH),
    "eps": (["re_dilut_eps_for_cont_dic_opr", "re_diluted_eps", "re_dil_eps_bfr_exi"],
            ["re_dilut_eps_for_cont_dic_opr", "re_diluted_eps", "re_dil_eps_bfr_exi"], 1.0),
    "profit_before_tax": (["re_pro_loss_bef_tax"], ["re_pro_loss_bef_tax"], LAKH),
    "tax": (["re_tax"], ["re_tax"], LAKH),
    "depreciation": (["re_depr_und_exp"], [], LAKH),
    "finance_costs": (["re_int_new"], [], LAKH),
}
# old archive pages: regex on the label with whitespace removed, lower case -> metric, scale.
# Wording varies by year ("Income from Operation" / "Operations").
HTML_MAP = [
    (r"consolidatednetprofit\(\+\)/loss\(-\)fortheperiod", "net_income", LAKH),  # preferred
    (r"netprofit\(\+\)/loss\(-\)fortheperiod", "net_income", LAKH),
    (r"netsales/incomefromoperations?", "total_revenue", LAKH),
    (r"totalincome", "total_revenue_bank", LAKH),                                 # banks only
    (r"profit\(\+\)/loss\(-\)fromordinaryactivitiesbeforetax", "profit_before_tax", LAKH),
    (r"taxexpense", "tax", LAKH),
    (r"depreciation", "depreciation", LAKH),
    (r"interest", "finance_costs", LAKH),                                         # non-bank only
    (r"dilutedepsafterextraordinaryitems\(inrs\.\)", "eps", 1.0),
    (r"basicepsafterextraordinaryitems\(inrs\.\)", "eps", 1.0),                 # fallback
]
# Old pages print 0.00 where a figure is missing (HDFCBANK 2005: PBT 0.00, diluted EPS 0.00 next to
# basic EPS 5.90). For these metrics an exact zero is treated as "not reported".
ZERO_IS_MISSING = {"total_revenue", "total_revenue_bank", "net_income", "profit_before_tax", "eps"}
XBRL_MAP = {
    "total_revenue": ["RevenueFromOperations", "Income"],
    # Ind AS (companies) first, then the banking taxonomy (HDFCBANK etc.)
    "net_income": ["ProfitOrLossAttributableToOwnersOfParent", "ProfitLossForPeriod",
                   "ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates",
                   "ProfitLossForThePeriod"],
    "eps": ["DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
            "DilutedEarningsLossPerShareFromContinuingOperations",
            "DilutedEarningsPerShareAfterExtraordinaryItems"],
    "profit_before_tax": ["ProfitBeforeTax", "ProfitLossFromOrdinaryActivitiesBeforeTax"],
    "tax": ["TaxExpense"],
    "depreciation": ["DepreciationDepletionAndAmortisationExpense"],
    "finance_costs": ["FinanceCosts"],
}
NUM = r"(-?[\d,]*\.?\d+)"


def _d(s):
    """'31-Dec-2008' / '30-SEP-2026' / '21-Apr-2008 19:18' / '2026-09-30' -> date."""
    s = s.strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.title() if "-" in s[:6] else s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"bad date {s!r}")


def _f(v):
    if v in (None, "", "-"):
        return None
    try:
        x = float(str(v).replace(",", ""))
    except ValueError:
        return None
    return None if x <= -999 else x


def parse_json(rec, payload):
    """2012-2024 data-API record -> [(metric, start, end, value)]."""
    d = payload.get("resultsData2") or payload.get("resultsData") or {}
    bank = (rec.get("bank") or "N") not in ("N", "")
    start, end = _d(rec["fromDate"]), _d(rec["toDate"])
    out = []
    for metric, (nb, bk, scale) in JSON_MAP.items():
        for k in (bk if bank else nb):
            v = _f(d.get(k))
            if v is not None:
                out.append((metric, start, end, v * scale))
                break
    return out


def parse_old_html(rec, page):
    """2005-2011 archive page -> [(metric, start, end, value)]."""
    text = re.sub(r"<[^>]+>", " ", page)
    flat = re.sub(r"\s+", "", htmlmod.unescape(text)).lower()
    bank = "interestearned" in flat
    start, end = _d(rec["fromDate"]), _d(rec["toDate"])
    got = {}
    for label, metric, scale in HTML_MAP:
        if metric in got:
            continue
        m = re.search(label + NUM, flat)
        if not m:
            continue
        v = _f(m.group(1))
        if v is None or (v == 0 and metric in ZERO_IS_MISSING):
            continue
        if metric == "total_revenue_bank":
            if not bank or "total_revenue" in got:
                continue
            metric = "total_revenue"
        if metric == "finance_costs" and bank:
            continue
        got[metric] = v * scale
    if "total_revenue" in got and not bank:   # same rule as re_gro_inc: add other operating income
        m = re.search(r"otheroperatingincome" + NUM, flat)
        v = _f(m.group(1)) if m else None
        if v:
            got["total_revenue"] += v * LAKH
    return [(m, start, end, v) for m, v in got.items()]


def parse_xbrl(raw, period_end):
    """2025+ integrated-filing XBRL -> [(metric, start, end, value)] for contexts ending on the
    filing's period end (quarter and year-to-date), without dimensions (entity totals)."""
    try:
        from lxml import etree
    except ImportError:
        import xml.etree.ElementTree as etree
    doc = etree.fromstring(raw)
    X = "{http://www.xbrl.org/2003/instance}"
    ctx = {}
    for c in doc.iter(X + "context"):
        if c.findall(".//{http://xbrl.org/2006/xbrldi}explicitMember"):
            continue
        s, e = c.findtext(f".//{X}startDate"), c.findtext(f".//{X}endDate")
        if s and e and _d(e) == period_end:
            ctx[c.get("id")] = (_d(s), _d(e))
    found = {}
    for el in doc:
        if not isinstance(el.tag, str) or el.get("contextRef") not in ctx:
            continue
        name = el.tag.rsplit("}", 1)[-1]
        for metric, tags in XBRL_MAP.items():
            if name in tags:
                v = _f(el.text)
                if v is None:
                    continue
                key = (metric, ctx[el.get("contextRef")])
                rank = tags.index(name)
                if key not in found or rank < found[key][0]:
                    found[key] = (rank, v)
    return [(m, s, e, v) for (m, (s, e)), (_r, v) in found.items()]


def plan(records):
    """Pick filings: ALL consolidated filings of a period where any exists, else all standalone ones.
    A period can have several (TCS FY2016: the original Indian-GAAP result in 2016 and an Ind-AS
    restatement in 2018) — both are kept; store() + readers apply first-release/restatement rules.
    records: [(kind, rec)] -> [(kind, rec, source)]."""
    groups = {}
    for kind, rec in records:
        if kind == "integrated":
            key = ("i", rec["qe_Date"].upper())
            cons = (rec.get("consolidated") or "").lower().startswith("consolidated")
        else:
            key = ("l", rec["fromDate"], rec["toDate"])
            cons = rec.get("consolidated") == "Consolidated"
        groups.setdefault(key, []).append((cons, kind, rec))
    out = []
    for items in groups.values():
        chosen = [x for x in items if x[0]] or items
        out += [(kind, rec, "nse_cons" if cons else "nse_sa") for cons, kind, rec in chosen]
    return out


class Nse:
    def __init__(self, pause):
        self.pause, self.n = pause, 0
        self.s = None

    def _new(self):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        try:
            self.s.get(BASE, timeout=30)   # cookie warm-up; NSE may answer 403 here and still serve the API
        except requests.RequestException:
            pass

    def get(self, url, as_json=True):
        for attempt in range(4):
            if self.s is None or self.n % 300 == 0:
                self._new()
            time.sleep(self.pause * (1 + attempt))
            self.n += 1
            try:
                r = self.s.get(url, timeout=45)
                if r.status_code in (401, 403, 429):
                    self.s = None
                    time.sleep(10 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json() if as_json else r.content
            except (requests.RequestException, ValueError):
                self.s = None
                time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"NSE unavailable: {url[:90]}")


def records_for(nse, sym):
    q = quote(sym)
    recs = [("list", r) for per in ("Quarterly", "Annual")
            for r in (nse.get(f"{BASE}/api/corporates-financial-results?index=equities&symbol={q}&period={per}") or [])]
    integ = nse.get(f"{BASE}/api/integrated-filing-results?index=equities&symbol={q}"
                    "&type=Integrated%20Filing-%20Financials") or {}
    recs += [("integrated", r) for r in (integ.get("data") or []) if r.get("xbrl")]
    return recs


def is_json_era(rec):
    """2012-2024 records ('N' flag) and bank records (format 'New', no flag, no archive link)."""
    return rec.get("oldNewFlag") == "N" or rec.get("format") == "New"


def fetch_facts(nse, kind, rec):
    if kind == "integrated":
        return parse_xbrl(nse.get(rec["xbrl"], as_json=False), _d(rec["qe_Date"]))
    if is_json_era(rec):
        u = (f"{BASE}/api/corporates-financial-results-data?index=equities&params={quote(rec['params'])}"
             f"&seq_id={rec['seqNumber']}&industry={quote(rec.get('industry') or '-')}"
             f"&frOldNewFlag={quote(rec.get('oldNewFlag') or '')}"
             f"&ind={quote(rec.get('indAs') or 'N')}&format={rec['format']}")
        return parse_json(rec, nse.get(u))
    link = rec.get("resultDetailedDataLink")
    if not link:
        return []
    return parse_old_html(rec, nse.get(link, as_json=False).decode("utf-8", "replace"))


def accession(kind, rec):
    return str(rec.get("seq_Id") if kind == "integrated" else rec.get("seqNumber"))


def filed_on(kind, rec):
    """When the result became public. Old records sometimes carry '-' for every timestamp: then
    period end + 60 days (SEBI deadline: 45 days for quarters, 60 for annual results) — late on
    purpose, so a backtest can never see a number before it was public."""
    keys = ("broadcast_Date", "creation_Date") if kind == "integrated" else ("filingDate", "broadCastDate", "exchdisstime")
    for k in keys:
        try:
            return _d(rec.get(k) or "")
        except ValueError:
            continue
    from datetime import timedelta
    return _d(rec.get("qe_Date") or rec["toDate"]) + timedelta(days=60)


def connect():
    conn = psycopg.connect(host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
                           user=os.environ.get("PGUSER", "postgres"), password=os.environ["PGPASSWORD"],
                           dbname=os.environ.get("PGDATABASE", "postgres"))
    conn.autocommit = True
    return conn


def store(conn, symbol, source, acc, filed, facts):
    """Insert facts that differ from the latest held value for (source, metric, end, duration)."""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT ON (metric, period_end, duration_days) metric, period_end, duration_days, "
                    "value FROM financial_facts WHERE symbol=%s AND source=%s "
                    "ORDER BY metric, period_end, duration_days, filed DESC", (symbol, source))
        latest = {(m, e, d): v for m, e, d, v in cur.fetchall()}
    n = 0
    with conn.transaction():
        with conn.cursor() as cur:
            for metric, start, end, value in facts:
                dur = (end - start).days
                prev = latest.get((metric, end, dur))
                if prev is not None and abs(value - prev) <= 1e-9 * max(1.0, abs(prev)):
                    continue
                cur.execute("INSERT INTO financial_facts (symbol, source, metric, period_end, period_start, "
                            "duration_days, value, filed, form, accession) VALUES "
                            "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                            (symbol, source, metric, end, start, dur, value, filed,
                             "consolidated" if source == "nse_cons" else "standalone", acc))
                n += cur.rowcount or 0
    return n


def main() -> int:
    limit = int(os.environ.get("UNIVERSE_LIMIT", "0") or 0) or None
    only = [s.strip() for s in os.environ.get("SYMBOLS", "").split(",") if s.strip()]
    max_min = float(os.environ.get("MAX_MINUTES", "0") or 0)
    nse, t0 = Nse(float(os.environ.get("PAUSE", "1.3"))), time.time()
    conn = connect()
    with conn.cursor() as cur:
        if only:
            symbols, cursor = only, None
        else:
            cur.execute("SELECT symbol FROM universe_constituents WHERE active AND universe='NIFTY500' ORDER BY 1")
            symbols = [r[0] for r in cur.fetchall()][:limit] if limit else [r[0] for r in cur.fetchall()]
            cur.execute("SELECT last_key FROM job_state WHERE job_name=%s", (JOB,))
            row = cur.fetchone()
            cursor = row[0] if row else None
    if cursor:
        symbols = [s for s in symbols if s > cursor]
        print(f"resuming after {cursor}: {len(symbols)} left", flush=True)
    print(f"symbols={len(symbols)}", flush=True)
    done = warn = filings = rows = 0
    stopped = False
    for sym in symbols:
        if max_min and (time.time() - t0) / 60 > max_min:
            stopped = True
            print(f"time budget {max_min:g} min reached — cursor kept at the last finished symbol", flush=True)
            break
        nse_sym = sym[:-3] if sym.endswith(".NS") else sym
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT accession FROM financial_facts WHERE symbol=%s "
                            "AND source IN ('nse_cons','nse_sa')", (sym,))
                seen = {r[0] for r in cur.fetchall()}
            todo = [p for p in plan(records_for(nse, nse_sym)) if accession(p[0], p[1]) not in seen]
            for kind, rec, source in todo:
                try:
                    facts = fetch_facts(nse, kind, rec)
                except Exception as exc:  # noqa: BLE001 — one bad filing must not lose the symbol
                    print(f"WARN {sym} filing {accession(kind, rec)}: {exc}", flush=True)
                    continue
                filings += 1
                rows += store(conn, sym, source, accession(kind, rec), filed_on(kind, rec), facts)
            done += 1
            if not only:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO job_state (job_name, last_key, updated_at) VALUES (%s,%s,now()) "
                                "ON CONFLICT (job_name) DO UPDATE SET last_key=EXCLUDED.last_key, updated_at=now()",
                                (JOB, sym))
        except Exception as exc:  # noqa: BLE001
            warn += 1
            print(f"WARN {sym}: {exc}", flush=True)
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            conn = connect()
        if done and done % 10 == 0:
            print(f"{done}/{len(symbols)} symbols, filings={filings}, rows={rows}, warn={warn}, "
                  f"{(time.time() - t0) / 60:.0f} min", flush=True)
    if not only and not limit and not stopped and done + warn >= len(symbols):
        with conn.cursor() as cur:
            cur.execute("UPDATE job_state SET last_key=NULL WHERE job_name=%s", (JOB,))
        print("cursor cleared (full pass complete)", flush=True)
    conn.close()
    print(f"NSE-FUNDAMENTALS-OK symbols={done} warn={warn} filings={filings} rows={rows} "
          f"partial={'yes' if stopped else 'no'}", flush=True)
    return 0 if done or stopped else 1


if __name__ == "__main__":
    sys.exit(main())
