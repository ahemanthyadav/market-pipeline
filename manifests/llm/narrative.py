"""ML-7a — daily market narrative via the shared LLM gateway (ADR 0022; gateway repo ../llm-gateway).

Runs ~20 min before the 08:00 IST digest. Builds a FACTS JSON from the DB (public market data only —
never chat ids, watchlists or holdings), asks the gateway's `fast-json` capability for a 2–4 sentence
summary, and accepts it only if every number in it appears in the facts AND no direction word
("fell", "inflow", …) contradicts the number's sign (one retry, naming the offending numbers). Stores the result in llm_narratives (one row per IST day; DELETE+INSERT).
The digest shows it only when status = 'ok'. LLM output is never a scoring input (ADR 0018).

Status: ok | invalid (failed the number check twice) | unavailable (gateway/provider error).
Exit 0 for ok/invalid/unavailable — the narrative is optional; exit 1 only on a code/DB error.
Env: PG*; LLM_BASE_URL, LLM_API_KEY (Secret llm-gateway-key); LLM_CAPABILITY (default fast-json).
Runs on the existing pipeline-health image (psycopg only; HTTP via the standard library).
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import psycopg

from factcheck import NUM, SMALL_INTS, allowed_numbers, bad_numbers, strip_fences, wrong_directions  # noqa: F401 — re-exported

IST = timezone(timedelta(hours=5, minutes=30))
KIND = "daily_market"
INDICES = [("^NSEI", "Nifty 50"), ("^BSESN", "Sensex"), ("^NSEBANK", "Nifty Bank"),
           ("^INDIAVIX", "India VIX"), ("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq"), ("GC=F", "Gold")]
SYSTEM = ("You write a short daily market note for Indian investors. Use ONLY the numbers and direction labels "
          "in the facts JSON, copied exactly as given (same rounding). Do not compute new numbers, do not mention "
          "dates, do not give advice or predictions. For FII/DII flows, use fii_direction/dii_direction "
          "(\"inflow\"/\"outflow\"/\"flat\") to state direction, not the sign of the amount. 2 to 4 sentences, "
          "plain English. Reply as JSON: {\"summary\": \"...\"}.")


def connect():
    conn = psycopg.connect(host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
                           user=os.environ.get("PGUSER", "postgres"), password=os.environ["PGPASSWORD"],
                           dbname=os.environ.get("PGDATABASE", "postgres"))
    conn.autocommit = True
    return conn


def pct(a, b):
    return None if not a or not b else round((a / b - 1) * 100, 2)


def build_facts(cur, as_of=None):
    """Public market facts, already rounded — the only numbers the model may use.
    as_of (date) rebuilds a past day's facts (bake-off); None = now."""
    end = (as_of or datetime.now(timezone.utc).date()) + timedelta(days=1)   # exclusive upper bound
    f = {"indices": [], "fx": {}, "flows_rs_cr": {}, "sentiment_7d": None}
    for ticker, name in INDICES:
        cur.execute("SELECT close FROM index_prices WHERE ticker=%s AND ts < %s AND ts > %s::date - 15 "
                    "AND close IS NOT NULL ORDER BY ts DESC LIMIT 2", (ticker, end, end))
        rows = [r[0] for r in cur.fetchall()]
        if len(rows) == 2:
            f["indices"].append({"name": name, "close": round(rows[0], 2), "change_pct": pct(rows[0], rows[1])})
    cur.execute("SELECT rate FROM fx_rates WHERE pair='USDINR=X' AND ts < %s AND ts > %s::date - 15 "
                "ORDER BY ts DESC LIMIT 2", (end, end))
    fx = [r[0] for r in cur.fetchall()]
    if len(fx) == 2:
        f["fx"] = {"usd_inr": round(fx[0], 2), "change_pct": pct(fx[0], fx[1])}
    cur.execute("SELECT flow_type, amount FROM capital_flows WHERE direction='net' "
                "AND ts = (SELECT max(ts) FROM capital_flows WHERE ts < %s AND ts > %s::date - 15)", (end, end))
    f["flows_rs_cr"] = {t: round(a) for t, a in cur.fetchall()}
    fii = f["flows_rs_cr"].get("FII", 0)
    dii = f["flows_rs_cr"].get("DII", 0)
    f["fii_direction"] = "inflow" if fii > 0 else "outflow" if fii < 0 else "flat"
    f["dii_direction"] = "inflow" if dii > 0 else "outflow" if dii < 0 else "flat"
    cur.execute("SELECT avg(sentiment_score) FROM news_sentiment WHERE ts < %s AND ts > %s::date - 7", (end, end))
    s = cur.fetchone()[0]
    f["sentiment_7d"] = None if s is None else round(s, 2)
    return f


def ask(messages, model=None):
    """One call to the gateway. Returns (summary, model) or raises RuntimeError('unavailable: …')."""
    # max_tokens 1500: reasoning models spend tokens thinking first; 400 returned empty content (2026-10-19)
    body = {"model": model or os.environ.get("LLM_CAPABILITY", "fast-json"), "messages": messages,
            "temperature": 0.2, "max_tokens": 1500, "response_format": {"type": "json_object"}}
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
        summary = raw.strip()                    # tolerate a model that ignored the JSON instruction
    return summary, data.get("model", "?")


def narrate(facts, asker=ask):
    """-> (status, summary|None, model, attempts, detail)."""
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": "Facts JSON:\n" + json.dumps(facts, separators=(",", ":"))}]
    model, attempts = "?", 0
    for attempts in (1, 2):
        try:
            summary, model = asker(msgs)
        except RuntimeError as exc:
            return "unavailable", None, model, attempts, str(exc)[:300]
        bad = bad_numbers(summary, facts) + [f"{x} (wrong direction)" for x in wrong_directions(summary, facts)]
        if summary and not bad and len(summary) <= 900:
            return "ok", summary, model, attempts, ""
        msgs += [{"role": "assistant", "content": json.dumps({"summary": summary})},
                 {"role": "user", "content": f"These numbers are not in the facts or have the wrong direction: {', '.join(bad) or 'too long/empty'}. "
                                             "Rewrite using only numbers from the facts JSON, copied exactly; a negative "
                                             "number is a fall / outflow / net selling."}]
    why = f"numbers not in facts or wrong direction: {', '.join(bad)[:200]}" if bad else f"empty or too long ({len(summary or '')} chars)"
    return "invalid", None, model, attempts, why


def main() -> int:
    day = datetime.now(IST).date()
    conn = connect()
    with conn.cursor() as cur:
        facts = build_facts(cur)
    if not facts["indices"]:
        status, summary, model, attempts, detail = "unavailable", None, "-", 0, "no recent index data"
    else:
        status, summary, model, attempts, detail = narrate(facts)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("DELETE FROM llm_narratives WHERE ts=%s AND kind=%s", (day, KIND))
            cur.execute("INSERT INTO llm_narratives (ts, kind, status, summary, model, attempts, detail, facts) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (day, KIND, status, summary, model, attempts, detail, json.dumps(facts)))
    conn.close()
    print(f"NARRATIVE-{status.upper()} day={day} model={model} attempts={attempts} {detail}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
