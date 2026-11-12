"""Fundamentals ingestion (Phase E): statements + live ratios, max history.

Per symbol (resumable via job_state cursor):
- US (CIK present): SEC EDGAR companyfacts XBRL — official statement history
  (~2009→ structured). Metrics: revenue, net income, operating income, EPS,
  equity, long-term debt, OCF, capex → FCF; since EV-9 2026-10-26 also total
  assets, gross profit, current assets/liabilities, shares outstanding and
  dividends paid (stored as a POSITIVE outflow from both sources — Yahoo files
  it negative, see _norm). filing_published_at = EDGAR 'filed'.
- NSE (no CIK): yfinance annual statements — same metric names.
- All symbols: yfinance info live ratios (pe, pb, ev_ebitda, roe,
  operating_margin, market_cap) stamped ts=fetch date (last-write-wins).

financial_facts (DQ-2, since v12): every EDGAR 10-K/10-Q value keyed by duration and
first filing date (edgar_fact_rows), and yfinance statement values with filed = first day
we observed them (write_yf_facts). stock_fundamentals stays the "latest" view.

Weekly cadence; BACKFILL=1 processes the whole universe from the cursor
(canary via CANARY_LIMIT). Point-in-time: yfinance legs carry
filing_published_at = fetch date (our DB could not know earlier).
SEC courtesy: declared User-Agent + 0.4s spacing (10 req/s policy).
"""
import os
import sys
import time

import psycopg
import requests
import yfinance as yf

UA = {"User-Agent": "piCluster market-data pipeline contact: homelab-admin@localhost"}
EDGAR = "https://data.sec.gov/api/xbrl/companyfacts/CIK{}.json"

# EDGAR metric -> accepted us-gaap tags (first match wins)
EDGAR_MAP = {
    # Append-only; the first tag with data wins per (metric, end, duration) key, so later
    # tags only fill gaps. EV-9 extension 2026-10-20: tags chosen from a probe of the 100
    # then-stale series (which fresh tags those companies actually file in 10-K/10-Q).
    "total_revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                      "RevenueFromContractWithCustomerIncludingAssessedTax",
                      "RegulatedAndUnregulatedOperatingRevenue",   # utilities: DTE, DUK, NEE, XEL
                      "RevenuesNetOfInterestExpense"],             # banks/brokers: MS, WFC
    "net_income": ["NetIncomeLoss", "ProfitLoss",
                   "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "operating_income": ["OperatingIncomeLoss"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
    "shareholder_equity": ["StockholdersEquity"],
    "total_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets",
              "PaymentsToAcquireOtherProductiveAssets",
              "PaymentsForCapitalImprovements"],                   # REITs: HST, INVH, WY
    # EV-9 full extension 2026-10-26 (roadmap: prerequisites of EV-10…EV-14).
    "total_assets": ["Assets"],                                    # instant
    "gross_profit": ["GrossProfit"],
    "current_assets": ["AssetsCurrent"],                           # instant
    "current_liabilities": ["LiabilitiesCurrent"],                 # instant
    "shares_outstanding": ["CommonStockSharesOutstanding",         # instant count first,
                           "WeightedAverageNumberOfDilutedSharesOutstanding"],  # per-period fallback
    "dividends_paid": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
    # DG-20 2026-11-12: captured for a future net-debt bridge. Valuation still nets GROSS
    # total_debt with no cash credit (ADR 0026/0030) until the owner decides otherwise.
    "cash_and_equivalents": ["CashCashEquivalentsAndShortTermInvestments",     # instant
                             "CashAndCashEquivalentsAtCarryingValue"],
}
# DG-11 / DF-08: Fallback COGS tags to derive gross_profit = total_revenue - cogs when
# a filer omits the explicit GrossProfit XBRL tag.
COGS_TAGS = ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"]
# yfinance statement row -> metric. yfinance 1.7 renamed rows to CamelCase
# (e.g. "NetIncome"); candidate lists cover both 0.2.x spaced names and 1.7+.
YF_MAP = {
    "total_revenue": ["Total Revenue", "TotalRevenue"],
    "net_income": ["Net Income", "NetIncome"],
    "operating_income": ["Operating Income", "OperatingIncome"],
    "eps": ["Diluted EPS", "DilutedEPS"],
    "shareholder_equity": ["Stockholders Equity", "StockholdersEquity"],
    "total_debt": ["Total Debt", "TotalDebt"],
    "fcf": ["Free Cash Flow", "FreeCashFlow"],
    # EV-9 full extension 2026-10-26; candidates cover 0.2.x spaced and 1.7+ camel names.
    "total_assets": ["Total Assets", "TotalAssets"],
    "gross_profit": ["Gross Profit", "GrossProfit"],
    "current_assets": ["Current Assets", "CurrentAssets",
                       "Total Current Assets", "TotalCurrentAssets"],
    "current_liabilities": ["Current Liabilities", "CurrentLiabilities",
                            "Total Current Liabilities", "TotalCurrentLiabilities"],
    "shares_outstanding": ["Ordinary Shares Number", "OrdinarySharesNumber",
                           "Share Issued", "ShareIssued"],
    "dividends_paid": ["Cash Dividends Paid", "CashDividendsPaid",
                       "Common Stock Dividend Paid", "CommonStockDividendPaid",
                       "Dividends Paid"],
    # ocf feeds financial_facts only (accruals, EV-12, for NSE names); like the
    # EDGAR leg it stays out of the stock_fundamentals "latest" view.
    "ocf": ["Operating Cash Flow", "OperatingCashFlow",
            "Total Cash From Operating Activities",
            "CashFlowFromContinuingOperatingActivities"],
    # DG-20 (see EDGAR_MAP): balance-sheet cash; the India names get it from this leg only
    # (NSE results filings carry no balance sheet).
    "cash_and_equivalents": ["Cash Cash Equivalents And Short Term Investments",
                             "CashCashEquivalentsAndShortTermInvestments",
                             "Cash And Cash Equivalents", "CashAndCashEquivalents"],
}
INFO_RATIOS = {
    "pe": "trailingPE", "pb": "priceToBook", "ev_ebitda": "enterpriseToEbitda",
    "roe": "returnOnEquity", "operating_margin": "operatingMargins",
    "market_cap": "marketCap",
}


def upsert(cur, symbol, ts, metric, value, source, published):
    if value is None:
        return 0
    # Keep the earliest filing_published_at ever seen: a weekly re-fetch must not
    # push a fact's "first knowable" date forward (EV-2). LEAST ignores NULL.
    cur.execute("SELECT filing_published_at FROM stock_fundamentals WHERE symbol=%s "
                "AND ts=%s AND metric=%s AND source=%s", (symbol, ts, metric, source))
    prior = cur.fetchone()
    first_seen = prior[0] if prior else None
    cur.execute("DELETE FROM stock_fundamentals WHERE symbol=%s AND ts=%s "
                "AND metric=%s AND source=%s", (symbol, ts, metric, source))
    cur.execute("INSERT INTO stock_fundamentals "
                "(symbol, ts, metric, value, source, filing_published_at) "
                "VALUES (%s,%s,%s,%s,%s,LEAST(%s::timestamptz, %s::timestamptz))",
                (symbol, ts, metric, float(value), source, published, first_seen))
    return 1


FLOW = {"total_revenue", "net_income", "operating_income", "eps", "ocf", "capex", "fcf",
        "gross_profit", "dividends_paid"}   # the rest are balance-sheet instants (duration 0)
# Kept out of the stock_fundamentals "latest" view on BOTH legs (raw inputs; fcf derived).
EXCLUDE_LATEST = {"ocf", "capex"}


def _norm(metric, value):
    """dividends_paid: EDGAR files it as a positive cash outflow, Yahoo as a negative
    line — store the positive magnitude from both sources (EV-9, 2026-10-26)."""
    return abs(value) if metric == "dividends_paid" and value is not None else value


def _days(a, b):
    from datetime import date
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def edgar_fact_rows(facts):
    """DQ-2, pure: companyfacts 'us-gaap' dict -> financial_facts rows
    (metric, period_end, period_start, duration_days, value, filed, form, accn).

    Keyed by DURATION (3/6/9/12-month YTD periods no longer collapse onto one end date)
    and by FILING DATE. Per (metric, end, duration) only the first release and genuine
    restatements are kept: a later filing that repeats the same value as a comparative is
    dropped, so `filed` is when the number first became knowable. Instant facts (balance
    sheet: no start) get duration 0 — the column is part of the primary key. For a metric
    with several tags the first tag in EDGAR_MAP wins per key."""
    by_key = {}
    for metric, tags in EDGAR_MAP.items():
        seen = set()
        for tag in tags:
            mine = set()
            for unit, entries in ((facts.get(tag) or {}).get("units", {})).items():
                if unit not in ("USD", "USD/shares", "shares"):   # shares: shares_outstanding (EV-9)
                    continue
                for e in entries:
                    if e.get("form") not in ("10-K", "10-Q") or not e.get("end") \
                            or not e.get("filed") or e.get("val") is None:
                        continue
                    start = e.get("start")
                    dur = _days(start, e["end"]) if start else 0
                    key = (metric, e["end"], dur)
                    if key in seen:
                        continue  # an earlier tag already owns this key
                    mine.add(key)
                    by_key.setdefault(key, []).append(
                        (e["filed"], e.get("accn") or "", _norm(metric, float(e["val"])),
                         start, e["form"]))
            seen |= mine
    # DG-11 / DF-08: Derive gross_profit = total_revenue - COGS when GrossProfit tag is absent
    cogs_by_key = {}
    seen_cogs = set()
    for tag in COGS_TAGS:
        mine = set()
        for unit, entries in ((facts.get(tag) or {}).get("units", {})).items():
            if unit != "USD":
                continue
            for e in entries:
                if e.get("form") not in ("10-K", "10-Q") or not e.get("end") \
                        or not e.get("filed") or e.get("val") is None:
                    continue
                start = e.get("start")
                dur = _days(start, e["end"]) if start else 0
                ckey = (e["end"], dur)
                if ckey in seen_cogs:
                    continue
                mine.add(ckey)
                cogs_by_key.setdefault(ckey, []).append(
                    (e["filed"], e.get("accn") or "", float(e["val"]), start, e["form"]))
        seen_cogs |= mine
    for (end, dur), cogs_entries in cogs_by_key.items():
        if ("gross_profit", end, dur) in by_key or ("total_revenue", end, dur) not in by_key:
            continue
        rev_by_filed = {r[0]: r for r in sorted(by_key[("total_revenue", end, dur)])}
        for filed, accn, cval, start, form in sorted(cogs_entries):
            rev_entry = rev_by_filed.get(filed)
            if rev_entry is not None:
                by_key.setdefault(("gross_profit", end, dur), []).append(
                    (filed, accn or rev_entry[1], rev_entry[2] - cval, start, form))
    rows = []
    for (metric, end, dur), entries in by_key.items():
        last_val, last_filed = None, None
        for filed, accn, val, start, form in sorted(entries):
            if filed == last_filed:
                continue  # same-day duplicates: keep the first
            if last_val is not None and abs(val - last_val) <= 1e-9 * max(1.0, abs(last_val)):
                continue  # repeated comparative, not new information
            rows.append((metric, end, start, dur, val, filed, form, accn or None))
            last_val, last_filed = val, filed
    return rows


def write_facts(cur, symbol, source, rows):
    """Insert-only; the primary key includes `filed`, so re-runs add nothing (ADR 0005)."""
    n = 0
    for metric, end, start, dur, val, filed, form, accn in rows:
        cur.execute("INSERT INTO financial_facts (symbol, source, metric, period_end, "
                    "period_start, duration_days, value, filed, form, accession) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (symbol, source, metric, end, start, dur, val, filed, form, accn))
        n += cur.rowcount or 0
    return n


def write_yf_facts(cur, symbol, facts, observed):
    """yfinance has no filing dates: `filed` = the day WE first observed a value (EV-2).
    A row is added only when the value differs from the latest one we hold for that
    (metric, period_end, duration) — weekly re-fetches add nothing unless Yahoo restates."""
    if not facts:
        return 0
    cur.execute("SELECT DISTINCT ON (metric, period_end, duration_days) metric, period_end, "
                "duration_days, value FROM financial_facts WHERE symbol=%s AND source='yfinance' "
                "ORDER BY metric, period_end, duration_days, filed DESC", (symbol,))
    latest = {(m, str(e), d): v for m, e, d, v in cur.fetchall()}
    rows = []
    for metric, end, val in facts:
        dur = 365 if metric in FLOW else 0
        prev = latest.get((metric, end, dur))
        if prev is not None and abs(val - prev) <= 1e-9 * max(1.0, abs(prev)):
            continue
        rows.append((metric, end, None, dur, val, observed, None, None))
    return write_facts(cur, symbol, "yfinance", rows)


def edgar_facts(cik, symbol, cur):
    """Ingest FULL EDGAR history: one row per (metric, period end) — the version
    with the latest 'filed' date wins (restatements supersede, v1 semantics).
    Annual + quarterly periods share end dates; 10-K supersedes 10-Q there
    (filed later). FCF derived as ocf − capex per matching period end."""
    r = requests.get(EDGAR.format(cik.zfill(10)), headers=UA, timeout=60)
    r.raise_for_status()
    facts = r.json().get("facts", {}).get("us-gaap", {})
    write_facts(cur, symbol, "edgar", edgar_fact_rows(facts))  # DQ-2 point-in-time store
    best = {}  # (metric, end) -> (val, filed)
    for metric, tags in EDGAR_MAP.items():
        for tag in tags:
            # companyfacts nesting: facts.{namespace}.{tag}.units.{unit}[]
            units = (facts.get(tag) or {}).get("units", {})
            for unit, entries in units.items():
                if unit not in ("USD", "USD/shares", "shares"):   # shares: shares_outstanding (EV-9)
                    continue
                for e in entries:
                    if e.get("form") not in ("10-K", "10-Q") or not e.get("end"):
                        continue
                    key = (metric, e["end"])
                    filed = e.get("filed", "")
                    if key not in best or filed > best[key][1]:
                        best[key] = (_norm(metric, float(e["val"])), filed)
    rows = 0
    cogs_best = {}
    for tag in COGS_TAGS:
        for unit, entries in ((facts.get(tag) or {}).get("units", {})).items():
            if unit != "USD":
                continue
            for e in entries:
                if e.get("form") not in ("10-K", "10-Q") or not e.get("end") or e.get("val") is None:
                    continue
                end = e["end"]
                filed = e.get("filed", "")
                if end not in cogs_best or filed > cogs_best[end][1]:
                    cogs_best[end] = (float(e["val"]), filed)
    ends = {end for (_m, end) in best}
    for end in ends:
        ocf = best.get(("ocf", end))
        capex = best.get(("capex", end))
        if ocf and capex:
            best[("fcf", end)] = (ocf[0] - capex[0], max(ocf[1], capex[1]))
        if ("gross_profit", end) not in best:
            rev = best.get(("total_revenue", end))
            cogs = cogs_best.get(end)
            if rev and cogs:
                best[("gross_profit", end)] = (rev[0] - cogs[0], max(rev[1], cogs[1]))
    for (metric, end), (val, filed) in best.items():
        if metric in EXCLUDE_LATEST:
            continue  # raw inputs kept out of the metric table (fcf derived)
        rows += upsert(cur, symbol, f"{end}T00:00:00Z", metric, val,
                       "edgar", f"{filed}T00:00:00Z" if filed else None)
    return rows


def yf_statements(symbol, cur, fetch_date):
    """Annual statements via yfinance, version-proof row lookup (0.2.x or 1.7+)."""
    tk = yf.Ticker(symbol)
    rows = 0
    frames = []
    facts = []  # (metric, period_end 'YYYY-MM-DD', value) -> financial_facts
    try:
        for getter, attrs in (("get_income_stmt", ("income_stmt",)),
                               ("get_balance_sheet", ("balance_sheet",)),
                               ("get_cashflow", ("cash_flow", "cashflow"))):
            fn = getattr(tk, getter, None)
            if fn:
                frames.append(fn(freq="yearly"))
            else:
                frames.append(next((getattr(tk, a) for a in attrs
                                    if getattr(tk, a, None) is not None), None))
    except Exception as exc:  # noqa: BLE001
        print(f"WARN {symbol} statements failed: {exc}", flush=True)
        return 0
    for metric, cands in YF_MAP.items():
        for frame in frames:
            if frame is None or not len(getattr(frame, "index", [])):
                continue
            hit = next((c for c in cands if c in frame.index), None)
            if hit is None:
                continue
            for col in frame.columns:
                v = frame.loc[hit, col]
                if v is not None and v == v:
                    v = _norm(metric, float(v))
                    if metric not in EXCLUDE_LATEST:
                        rows += upsert(cur, symbol, col, metric, v, "yfinance", fetch_date)
                    facts.append((metric, str(getattr(col, "date", lambda: col)())[:10], v))
            break
    write_yf_facts(cur, symbol, facts, fetch_date[:10])
    return rows


def yf_ratios(symbol, cur, fetch_date):
    try:
        info = yf.Ticker(symbol).info or {}
    except Exception as exc:  # noqa: BLE001
        print(f"WARN {symbol} info failed: {exc}", flush=True)
        return 0
    rows = 0
    for metric, key in INFO_RATIOS.items():
        v = info.get(key)
        if isinstance(v, (int, float)):
            rows += upsert(cur, symbol, fetch_date, metric, v, "yfinance", fetch_date)
    return rows


def main() -> int:
    backfill = os.environ.get("BACKFILL", "1") == "1"
    limit = int(os.environ.get("CANARY_LIMIT", "0") or 0) or None
    job = "stock_fundamentals"
    conn = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    conn.autocommit = True  # reads idle-transaction-safe (lesson 2026-10-04)
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, coalesce(cik,'') FROM universe_constituents "
                    "WHERE active ORDER BY 1")
        universe = [(r[0], r[1]) for r in cur.fetchall()]
        if limit:
            universe = universe[:limit]
        if backfill:
            cur.execute("SELECT last_key FROM job_state WHERE job_name=%s", (job,))
            row = cur.fetchone()
            if row and row[0]:
                universe = [u for u in universe if u[0] > row[0]]
    if not universe:
        if backfill:
            # Empty post-cursor universe in backfill mode = the pass already
            # completed (a previous attempt died between the last symbol and
            # the clear). Clear so the next run starts fresh (2026-10-04 lesson).
            with conn.cursor() as cur:
                cur.execute("UPDATE job_state SET last_key=NULL WHERE job_name=%s",
                            (job,))
            print("pass already complete — cursor cleared", flush=True)
        else:
            print("nothing to do", flush=True)
        return 0
    print(f"symbols={len(universe)} (resumable)", flush=True)
    done, failed, rows = 0, 0, 0
    for symbol, cik in universe:
        fetch_date = f"{time.strftime('%Y-%m-%d')}T00:00:00Z"
        wrote = 0
        try:
            if cik:
                with conn.transaction():
                    with conn.cursor() as cur:
                        wrote += edgar_facts(cik, symbol, cur)
                time.sleep(0.4)
            with conn.transaction():
                with conn.cursor() as cur:
                    wrote += yf_statements(symbol, cur, fetch_date)
                    wrote += yf_ratios(symbol, cur, fetch_date)
        except psycopg.OperationalError as exc:
            print(f"WARN {symbol} conn dropped ({exc}); reconnecting", flush=True)
            failed += 1
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            conn = psycopg.connect(
                host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
                user=os.environ.get("PGUSER", "postgres"),
                password=os.environ["PGPASSWORD"],
                dbname=os.environ.get("PGDATABASE", "postgres"),
            )
            conn.autocommit = True
        except Exception as exc:  # noqa: BLE001
            print(f"WARN {symbol} failed: {exc}", flush=True)
            failed += 1
        else:
            done += 1
            rows += wrote
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO job_state (job_name, last_key, updated_at) "
                                "VALUES (%s,%s,now()) ON CONFLICT (job_name) DO UPDATE "
                                "SET last_key=EXCLUDED.last_key, updated_at=now()",
                                (job, symbol))
        time.sleep(1.2 if not cik else 0.3)
    if backfill and done + failed >= len(universe):
        with conn.cursor() as cur:
            cur.execute("UPDATE job_state SET last_key=NULL WHERE job_name=%s", (job,))
        print("cursor cleared (full pass complete)", flush=True)
    conn.close()
    print(f"FUNDAMENTALS-OK done={done}/{len(universe)} failed={failed} rows={rows}",
          flush=True)
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
