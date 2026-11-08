"""ML-7b — per-symbol narration for the shortlist (~50 names).

Runs weekly after the Saturday stock-screener (02:15 PDT). For each symbol in the
latest stock_reports shortlist per universe, builds a facts JSON (report excerpt +
recent headlines + key price/score), asks the gateway's `fast-json` capability for
a one-sentence note, and caches by content hash to skip unchanged symbols.
Writes to llm_narratives (kind='per_symbol', one row per symbol per run).
Exit 0 even if some symbols fail — narrative is optional, never scoring input.

Env: PG*; LLM_BASE_URL, LLM_API_KEY (Secret llm-gateway-key);
     LLM_CAPABILITY (default fast-json); MAX_SYMBOLS (default 10);
     PAUSE (s between symbol calls, default 7 — gateway allows 10 req/min).
Runs on the existing pipeline-health image (psycopg only; HTTP via stdlib).
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import psycopg

from factcheck import NUM, SMALL_INTS, allowed_numbers, bad_numbers, strip_fences, wrong_directions  # noqa: F401

IST = timezone(timedelta(hours=5, minutes=30))
KIND = "per_symbol"
SYSTEM = ("You write a one-sentence market note for a single Indian stock. Use ONLY the "
          "numbers and facts in the facts JSON, copied exactly. Do not compute new "
          "numbers, do not mention dates, do not give advice or predictions. One short "
          "sentence, plain English. Reply as JSON: {\"summary\": \"...\"}.")

MAX_SYMBOLS = int(os.environ.get("MAX_SYMBOLS", "10"))
PAUSE = float(os.environ.get("PAUSE", "7"))


def connect():
    conn = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    conn.autocommit = True
    return conn


def content_hash(facts):
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()[:16]


def ask(messages, model=None):
    body = {"model": model or os.environ.get("LLM_CAPABILITY", "fast-json"),
            "messages": messages, "temperature": 0.2, "max_tokens": 800,
            "response_format": {"type": "json_object"}}
    req = urllib.request.Request(os.environ["LLM_BASE_URL"].rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + os.environ["LLM_API_KEY"],
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.load(r)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        detail = exc.read().decode()[:200] if isinstance(exc, urllib.error.HTTPError) else str(exc)
        raise RuntimeError(f"unavailable: {detail}") from exc
    raw = strip_fences(data["choices"][0]["message"]["content"] or "")
    try:
        summary = json.loads(raw)["summary"].strip()
    except (ValueError, KeyError, TypeError, AttributeError):
        summary = raw.strip()
    return summary, data.get("model", "?")


def narrate(facts, asker=ask):
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": "Facts JSON:\n" + json.dumps(facts, separators=(",", ":"))}]
    model, attempts = "?", 0
    for attempts in (1, 2):
        try:
            summary, model = asker(msgs)
        except RuntimeError as exc:
            return "unavailable", None, model, attempts, str(exc)[:300]
        bad = bad_numbers(summary, facts) + [f"{x} (wrong direction)" for x in wrong_directions(summary, facts)]
        if summary and not bad and len(summary) <= 500:
            return "ok", summary, model, attempts, ""
        msgs += [{"role": "assistant", "content": json.dumps({"summary": summary})},
                 {"role": "user", "content": f"These numbers are not in the facts or have the wrong direction: {', '.join(bad) or 'too long/empty'}. "
                                             "Rewrite using only numbers from the facts JSON, copied exactly."}]
    why = f"numbers not in facts or wrong direction: {', '.join(bad)[:200]}" if bad else f"empty or too long ({len(summary or '')} chars)"
    return "invalid", None, model, attempts, why


def shortlist_symbols(cur, limit=MAX_SYMBOLS):
    """Latest shortlist symbols across all universes from stock_reports."""
    cur.execute("""
        SELECT DISTINCT ON (symbol) symbol, report_md
        FROM stock_reports
        WHERE ts = (SELECT max(ts) FROM stock_reports)
        ORDER BY symbol
        LIMIT %s
    """, (limit,))
    return cur.fetchall()


def symbol_facts(cur, symbol, report_md):
    """Build per-symbol facts: price, score, report excerpt, recent headlines."""
    f = {"symbol": symbol, "report": report_md[:300] if report_md else ""}
    # latest close + change_pct
    cur.execute("SELECT close FROM stock_prices WHERE symbol=%s AND close IS NOT NULL "
                "ORDER BY ts DESC LIMIT 2", (symbol,))
    rows = [r[0] for r in cur.fetchall()]
    if len(rows) == 2:
        f["close"] = round(rows[0], 2)
        f["change_pct"] = round((rows[0] / rows[1] - 1) * 100, 2)
    # latest score
    cur.execute("SELECT composite, rank FROM stock_scores WHERE symbol=%s "
                "ORDER BY ts DESC LIMIT 1", (symbol,))
    row = cur.fetchone()
    if row:
        f["composite"] = round(row[0], 2)
        f["rank"] = row[1]
    # recent headlines mentioning this symbol (via headline_entities)
    cur.execute("SELECT h.headline FROM raw_headlines h "
                "JOIN headline_entities e ON e.headline_id = h.id "
                "WHERE e.symbol = %s AND h.ts >= now() - interval '7 days' "
                "ORDER BY h.ts DESC LIMIT 3", (symbol,))
    f["headlines"] = [r[0] for r in cur.fetchall()]
    return f


def main() -> int:
    day = datetime.now(IST).date()
    conn = connect()
    processed = skipped = failed = 0
    try:
        cur = conn.cursor()
        print(f"ML7B-START day={day} max_symbols={MAX_SYMBOLS}", flush=True)
        symbols = shortlist_symbols(cur, MAX_SYMBOLS)
        print(f"ML7B-SYMBOLS count={len(symbols)}", flush=True)
        for symbol, report_md in symbols:
            facts = symbol_facts(cur, symbol, report_md)
            ch = content_hash(facts)
            # content-hash cache: skip if same symbol+kind+hash already written today
            cur.execute("SELECT 1 FROM llm_narratives WHERE ts=%s AND kind=%s AND symbol=%s LIMIT 1",
                        (day, KIND, symbol))
            if cur.fetchone():
                skipped += 1
                continue
            if not facts.get("close"):
                failed += 1
                continue
            status, summary, model, attempts, detail = narrate(facts)
            if status == "ok":
                summary_tagged = f"[{symbol}#{ch}] {summary}"
            else:
                summary_tagged = f"[{symbol}#{ch}] {detail or status}"
            with conn.transaction():
                cur2 = conn.cursor()
                cur2.execute("DELETE FROM llm_narratives WHERE ts=%s AND kind=%s AND symbol=%s",
                             (day, KIND, symbol))
                cur2.execute("INSERT INTO llm_narratives (ts, kind, status, summary, model, attempts, detail, facts, symbol) "
                             "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                             (day, KIND, status, summary_tagged, model, attempts, detail,
                              json.dumps(facts), symbol))
                cur2.close()
            if status == "ok":
                processed += 1
            else:
                failed += 1
            print(f"ML7B-{status.upper()} {symbol} attempts={attempts} {detail[:80]}", flush=True)
            time.sleep(PAUSE)
        cur.close()
    finally:
        conn.close()
    print(f"ML7B-OK processed={processed} skipped={skipped} failed={failed}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
