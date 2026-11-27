"""Headline entity/event extraction v1 (findings Tier 1 #2; ADR 0010).

Gazetteer-first: every entity row is an INSTRUMENT-linked match — the
ticker_names table (1k+ display names) is the gazetteer, so extraction is
deterministic and reproducible (no model on TX2 CPU; model-based NER is
deferred to the 3090 AI layer). Events come from a small keyword taxonomy
(results, rating_action, ma, buyback, order_win, capex, ipo, dividend,
debt_raise, regulatory, legal, leadership, product, partnership, guidance,
rate_decision) — the taxonomy lives HERE, not in the schema.

Match rules (documented so they're auditable):
- variants per name: full legal-suffix-stripped name, plus the first token
  when it is >= 4 chars, NOT a generic-word stoplist entry, and unambiguous
  across the whole gazetteer (exactly one symbol maps to it);
- one compiled alternation, longest variants first, word-bounded,
  case-insensitive;
- a few curated aliases for names headlines use instead of listing names
  (google -> GOOG, facebook -> META);
- events get symbol= the single stock mentioned, else NULL.

Idempotency: full rescan of raw_headlines every run (563 rows today,
~seconds); per-headline DELETE+INSERT in chunked transactions — re-runs
rewrite, never duplicate. Connection discipline: house pattern (autocommit
reads, conn.transaction() writes, reconnect-retry once per chunk).
"""
import os
import re
import sys

import psycopg

ALIASES = {"google": "GOOG", "alphabet": "GOOG", "facebook": "META",
           "meta": "META"}
SUFFIX_RE = re.compile(
    r"\s+(?:Ltd\.?|Limited|Inc\.?|Corp\.?|Corporation|Plc|Co\.?|Company|"
    r"Companies)\.?$", re.IGNORECASE)
FIRST_TOKEN_STOP = {
    "the", "and", "for", "new", "first", "united", "american", "national",
    "general", "global", "standard", "total", "sun", "tech", "technology",
    "financial", "capital", "energy", "india", "bank", "banks", "steel",
    "oil", "gas", "pharma", "health", "life", "motor", "electric", "power",
    "chemical", "textile", "industries", "enterprises", "international",
    "indian", "bharat", "state",
    # measured false-positive sources (v2, 2026-10-09 canary): 'live' hit every
    # "Stock Market LIVE" blog headline (25× Live Nation); 'target' hit broker
    # price-target notes (4× Target Corp); 'realty' → Realty Income (O),
    # 'west' → West Pharma, 'data' → Data Patterns, 'action' → ACE.NS.
    # The rest are pre-emptive common-word insurance; full-name and two-token
    # variants still match those companies when actually named.
    "target", "live", "realty", "west", "data", "action", "just", "gap",
    "delta", "page", "share", "credit", "hero", "net", "free",
}
EVENT_PATTERNS = {
    "results": r"quarterly results|q[1-4]\s?results|(?:[fq]q\s?\d)|"
               r"results announcement|earnings",
    "rating_action": r"upgrades?|downgrades?|rating (?:cut|hike|action)|"
                     r"outlook (?:cut|revised|raised|lowered)|credit watch",
    "ma": r"merger|acquisition|acquires?|takeover|buyout|"
          r"stake (?:sale|purchase|buy|buyout)",
    "buyback": r"buyback|share repurchase|repurchase programme",
    "order_win": r"order win|wins? .{0,30}(?:order|contract|deal)|"
                 r"bags? .{0,30}(?:order|contract)|secures? .{0,30}contract",
    "capex": r"\bcapex\b|capacity expansion|greenfield|brownfield|new plant|"
             r"manufacturing facility|production facility",
    "ipo": r"\bipo\b|initial public offering|public listing|listing plans",
    "dividend": r"dividend|interim payout",
    "debt_raise": r"raises? .{0,20}(?:funds|capital)|non-convertible debenture|"
                  r"\bncds?\b|bond sale|loan agreement|credit facility|term loan",
    "regulatory": r"\bsebi\b|regulator|show-?cause|probe|investigat(?:e|ion|"
                  r"ing)|enforcement directorate|penalt(?:y|ies)",
    "legal": r"\bcourt\b|lawsuit|litigation|arbitration|tribunal",
    "leadership": r"\bceo\b|\bcfo\b|managing director|chairman|chairperson|"
                  r"appoints? .{0,30}(?:ceo|cfo|director)|resign",
    "product": r"launch(?:es|ed)?|unveils?|rolls? out|introduces?",
    "partnership": r"partnership|collaboration|joint venture|\bjv\b|\bmou\b|"
                   r"\bpact\b|tie-?up|signs? .{0,20}agreement",
    "guidance": r"guidance|outlook|forecasts? (?:up|down|raised|lowered)?|"
                r"revises? .{0,20}(?:up|down)ward|warns? (?:on|of) (?:demand|"
                r"margin|revenue)",
    "rate_decision": r"repo rate|rate (?:cut|hike)|\bmpc\b|monetary policy|"
                     r"policy rate|central bank",
}
CHUNK = 100


def connect():
    conn = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    conn.autocommit = True
    return conn


def entity_type(symbol):
    if symbol.startswith("^"):
        return "index"
    if symbol.endswith("=X"):
        return "fx"
    return "stock"


def clean_name(name):
    name = name.strip()
    prev = None
    while prev != name:  # strip trailing legal suffixes, possibly stacked
        prev = name
        name = SUFFIX_RE.sub("", name).rstrip(". ").strip()
    return name


def build_gazetteer(cur):
    """{variant_lower: [(symbol, name), ...]} + the compiled matcher regex."""
    cur.execute("SELECT ticker, name FROM ticker_names")
    rows = cur.fetchall()
    variants = {}
    for symbol, name in rows:
        entry = (symbol, name)
        full = clean_name(name)
        # v3: a cleaned name that is a SINGLE common-word stoplist entry
        # ("Target Corporation" → "Target") is itself the false-positive
        # source — the full-name variant must go too, not just first tokens.
        if len(full) >= 3 and not (len(full.split()) == 1
                                   and full.lower() in FIRST_TOKEN_STOP):
            variants.setdefault(full.lower(), []).append(entry)
        for alias, asymbol in ALIASES.items():
            if asymbol == symbol:
                variants.setdefault(alias, []).append(entry)
    # unambiguous first-token variants (token -> exactly one symbol overall),
    # and first-TWO-token variants for names of 3+ tokens (catches short-forms
    # like "Bajaj Holdings" / "HDFC Bank" while staying collision-free)
    token_map = {}
    for symbol, name in rows:
        toks = clean_name(name).split()
        cand = []
        if toks:
            cand.append(toks[0])
        if len(toks) >= 3:
            cand.append(" ".join(toks[:2]))
        for tok in cand:
            if len(tok) >= 4 and tok.lower().split()[0] not in FIRST_TOKEN_STOP:
                token_map.setdefault(tok.lower(), set()).add(symbol)
    for tok, symbols in token_map.items():
        if len(symbols) == 1 and tok not in variants:
            symbol = next(iter(symbols))
            for s, n in rows:
                if s == symbol:
                    variants.setdefault(tok, []).append((symbol, n))
                    break
    ordered = sorted(variants, key=lambda v: (-len(v), v))
    pattern = r"\b(?:" + "|".join(re.escape(v) for v in ordered) + r")\b"
    return variants, re.compile(pattern, re.IGNORECASE)


def build_universe_map(cur):
    cur.execute("SELECT symbol, universe FROM universe_constituents WHERE active "
                "ORDER BY 1")
    out = {}
    for symbol, universe in cur.fetchall():
        out.setdefault(symbol, universe)  # first alphabetical wins if ever both
    return out


def process_chunk(conn, chunk, variants, matcher, universes, events_re):
    """DELETE+INSERT all rows for the chunk's headlines. One transaction per
    chunk; idempotent, so a retry after a dropped connection is safe."""
    n_ent = n_evt = n_headline_with_entity = 0
    with conn.transaction():
        with conn.cursor() as cur:
            for hid, headline, source, ts in chunk:
                ents = {}
                for m in matcher.finditer(headline):
                    for symbol, name in variants.get(m.group(0).lower(), []):
                        ents.setdefault(symbol, (symbol, name))
                stocks = sorted(s for s in ents if entity_type(s) == "stock")
                cur.execute("DELETE FROM headline_entities WHERE headline_id=%s",
                            (hid,))
                cur.execute("DELETE FROM headline_events WHERE headline_id=%s",
                            (hid,))
                for symbol, name in ents.values():
                    cur.execute(
                        "INSERT INTO headline_entities (headline_id, ts, symbol,"
                        " name, entity_type, universe, source) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (hid, ts, symbol, name, entity_type(symbol),
                         universes.get(symbol), source))
                    n_ent += 1
                if ents:
                    n_headline_with_entity += 1
                single = stocks[0] if len(stocks) == 1 else None
                for etype, regex in events_re.items():
                    if regex.search(headline):
                        cur.execute(
                            "INSERT INTO headline_events (headline_id, ts, "
                            "event_type, symbol, source) VALUES (%s,%s,%s,%s,%s)",
                            (hid, ts, etype, single, source))
                        n_evt += 1
    return n_ent, n_evt, n_headline_with_entity


def main() -> int:
    conn = connect()
    with conn.cursor() as cur:
        variants, matcher = build_gazetteer(cur)
        universes = build_universe_map(cur)
        cur.execute("SELECT id, headline, source, ts FROM raw_headlines ORDER BY id")
        headlines = cur.fetchall()
    events_re = {k: re.compile(v, re.IGNORECASE) for k, v in EVENT_PATTERNS.items()}
    print(f"gazetteer: {len(variants)} variants over {len(headlines)} headlines",
          flush=True)
    total_ent = total_evt = total_h = done = 0
    for i in range(0, len(headlines), CHUNK):
        chunk = headlines[i:i + CHUNK]
        try:
            n_ent, n_evt, n_h = process_chunk(conn, chunk, variants, matcher,
                                              universes, events_re)
        except psycopg.OperationalError as exc:
            print(f"WARN conn dropped ({exc}); reconnecting, retrying chunk",
                  flush=True)
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            conn = connect()
            n_ent, n_evt, n_h = process_chunk(conn, chunk, variants, matcher,
                                              universes, events_re)
        total_ent += n_ent
        total_evt += n_evt
        total_h += n_h
        done += len(chunk)
        print(f"{done}/{len(headlines)}: entities={total_ent} events={total_evt}",
              flush=True)
    conn.close()
    print(f"NER-OK headlines={done} with_entities={total_h} "
          f"entities={total_ent} events={total_evt}", flush=True)
    return 0 if done else 1


if __name__ == "__main__":
    from mdjob import run_main   # one job_runs row per execution; never fails the job
    sys.exit(run_main("headline-ner", main))
