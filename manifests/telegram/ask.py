"""/ask — free-text questions answered from DB facts only (ML-7d family, ADR 0022/0024; 2026-10-19).

Flow: find the subject (stock via the forgiving resolver, index/FX/flows/news by keyword), gather a small
FACTS JSON from the DB (public data only — never chat ids or watchlists), ask the gateway (`general`) to
answer using ONLY those facts, then accept the answer only if every number and direction checks out
(factcheck.py — the same rule as ML-7a). Two failures → the bot shows the facts themselves instead of the
model's prose. A question the facts cannot answer is refused, never guessed. News questions use semantic
search over headline_embeddings (same embedding model only — ADR 0024).

Env: BOT_AI_RESOLVE, LLM_BASE_URL, LLM_API_KEY (Secret llm-gateway-key). Degrades to plain commands when off.
"""
import json
import os
import re

import factcheck
import stocks

NEWS_K = 6
NEWS_DAYS = 21
TOPICS = {
    "news": re.compile(r"\bnews|headline|recent|latest|said|announcement", re.I),
    "flows": re.compile(r"\bfii|\bdii|flow|foreign|institutional", re.I),
    "fx": re.compile(r"\bfx|usdinr|rupee|dollar|euro|yen|currency", re.I),
    "screener": re.compile(r"\btop\b|\bscreener\b|\bbest\b|\bmultibagger\b|\bshortlist\b|\bpicks?\b", re.I),
    "sectors": re.compile(r"\bsectors?\b|\bindustr(?:y|ies)\b|\bcheapest|\bexpensive", re.I),
    "regime": re.compile(r"\bregime\b|\bmacro\b|\bvix\b|\bvolatility\b|\byield\b|\bspread\b|\bbreadth\b", re.I),
    "scorecard": re.compile(r"\bscorecard\b|\btrack record\b|\bhit rate\b|\bpredictions?\b|\balpha\b", re.I),
    "earnings": re.compile(r"\bearnings\b|\bresults calendar\b|\breporting\b|\bupcoming results\b", re.I),
}


def q_or_none(q, sql, args=()):
    try:
        return q(sql, args)
    except Exception:  # noqa: BLE001 — a missing table must not kill the command
        return None


def stock_facts(q, row):
    sym = row[0]
    m = q_or_none(q, "SELECT metric, value FROM stock_metrics_daily WHERE symbol = %s "
                     "AND ts >= now() - interval '400 days' ORDER BY ts DESC LIMIT 12", (sym,))
    f = {"symbol": sym, "name": row[1], "sector": row[2], "universe": row[3],
         "metrics": {k: v for k, v in (m or [])}}
    if "pos_52w" in f["metrics"]:
        # self-describing for the model: 0.35 = 35% of the way up the low->high range, not "35% above the low"
        f["metrics"]["position_in_52w_range"] = f["metrics"].pop("pos_52w")
    hl = q_or_none(q, "SELECT max(high), min(low) FROM stock_prices "
                      "WHERE symbol = %s AND ts >= now() - interval '52 weeks'", (sym,))
    if hl and hl[0] and hl[0][0] is not None:
        f["metrics"]["high_52w"] = round(hl[0][0], 2)
        f["metrics"]["low_52w"] = round(hl[0][1], 2)
    sc = q_or_none(q, "SELECT composite, rank, value_score, quality_score, growth_score, "
                      "momentum_score, risk_score FROM stock_scores WHERE symbol = %s "
                      "AND ts >= now() - interval '400 days' ORDER BY ts DESC LIMIT 1", (sym,))
    if sc and sc[0]:
        f["score"] = {"composite": sc[0][0], "rank": sc[0][1]}
        if len(sc[0]) >= 7 and sc[0][2] is not None:
            subs = tuple(sc[0][2:7])
            f["score"].update({
                "value": round(subs[0], 2), "quality": round(subs[1], 2),
                "growth": round(subs[2], 2), "momentum": round(subs[3], 2),
                "risk": round(subs[4], 2),
                "archetype": stocks.stock_archetype(subs),
                "drivers": stocks.stock_drivers(subs),
            })
    fund = q_or_none(q, "SELECT DISTINCT ON (metric) metric, value FROM stock_fundamentals "
                        "WHERE symbol = %s AND metric IN ('pe','pb','roe','operating_margin','market_cap','ev_ebitda') "
                        "AND ts >= now() - interval '400 days' ORDER BY metric, ts DESC", (sym,))
    if fund:
        f["fundamentals"] = {k: round(v, 4) if isinstance(v, float) else v for k, v in fund if v is not None}
    val = q_or_none(q, "SELECT fair_value, upside_pct, methods FROM v_latest_valuations WHERE symbol = %s", (sym,))
    if val and val[0] and val[0][0] is not None:
        f["valuation"] = {"fair_value": round(val[0][0], 2), "upside_pct": round(val[0][1], 2), "methods": val[0][2]}
    ec = q_or_none(q, "SELECT earnings_date FROM earnings_calendar WHERE symbol = %s "
                      "AND earnings_date >= current_date ORDER BY earnings_date LIMIT 1", (sym,))
    if ec and ec[0] and ec[0][0] is not None:
        f["next_earnings"] = str(ec[0][0])
    news = news_facts(q, sym, days=NEWS_DAYS, exact=True)
    if not news and sym.endswith(".NS"):
        news = news_facts(q, stocks.bare(sym), days=NEWS_DAYS, exact=True)
    if news:
        f["recent_headlines"] = news
    return f


def news_facts(q, text, k=NEWS_K, days=NEWS_DAYS, exact=False):
    """Nearest headlines: semantic search when the embedding table has this text's model rows, else LIKE."""
    rows = None
    if not exact and os.environ.get("BOT_AI_RESOLVE") == "1":
        try:
            model, vecs = embed_question(text)
            if vecs:
                rows = q_or_none(
                    q, f"SELECT h.headline, h.ts::date FROM headline_embeddings e "
                    f"JOIN raw_headlines h ON h.id = e.headline_id "
                    f"WHERE e.model = %s AND e.ts >= now() - interval '{int(days)} days' "
                    f"ORDER BY e.embedding <=> %s::vector LIMIT {int(k)}",
                    (model, "[" + ",".join(f"{x:.6g}" for x in vecs[0]) + "]"))
        except Exception:  # noqa: BLE001 — semantic search is an enhancement, not a requirement
            rows = None
    if rows is None:
        rows = q_or_none(
            q, f"SELECT headline, ts::date FROM raw_headlines WHERE ts >= now() - interval '{int(days)} days' "
            f"AND headline ILIKE %s ORDER BY ts DESC LIMIT {int(k)}", (f"%{text}%",))
    return [f"{d}: {h[:200]}" for h, d in (rows or [])]


def embed_question(text):
    import urllib.request
    body = {"model": "embed", "input": [text[:1000]], "encoding_format": "float", "disable_fallbacks": True}
    req = urllib.request.Request(os.environ["LLM_BASE_URL"].rstrip("/") + "/embeddings",
                                 data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + os.environ["LLM_API_KEY"],
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        model = (r.headers.get("x-litellm-model-name") or "").split("/", 1)[-1]
        data = json.load(r)
    return model, [d["embedding"] for d in data["data"]]


def macro_facts(q):
    f = {}
    fx = q_or_none(q, "SELECT pair, rate FROM fx_rates WHERE pair = 'USDINR=X' "
                      "AND ts >= now() - interval '10 days' ORDER BY ts DESC LIMIT 2")
    if fx:
        f["usdinr"] = {"latest": fx[0][1], "prev": fx[1][1] if len(fx) > 1 else None}
    fl = q_or_none(q, "SELECT flow_type, amount FROM capital_flows WHERE direction = 'net' "
                      "AND ts >= now() - interval '10 days' ORDER BY ts DESC LIMIT 4")
    if fl:
        f["flows_rs_cr_latest"] = {t: a for t, a in fl}
    y = q_or_none(q, "SELECT instrument, yield FROM bond_yields WHERE instrument IN ('^TNX','IN_GS_10Y') "
                      "AND ts >= now() - interval '10 days' ORDER BY ts DESC LIMIT 4")
    if y:
        f["yields"] = {i: v for i, v in y}
    return f


def index_facts(q, arg):
    t = stocks.find_index(q, arg)
    if not t:
        return None
    rows = q_or_none(q, "SELECT ts::date, close FROM index_prices WHERE ticker = %s "
                        "AND ts >= now() - interval '10 days' ORDER BY ts DESC LIMIT 3", (t[0],))
    if not rows:
        return None
    f = {"index": t[1], "ticker": t[0], "close": rows[0][1], "prev_close": rows[1][1] if len(rows) > 1 else None}
    ytd = stocks.ytd_map(q, [t[0]])
    if ytd.get(t[0]) is not None:
        f["ytd_pct"] = round(ytd[t[0]], 2)
    return f


def screener_facts(q, question):
    ql = question.lower()
    uni = "NIFTY500"
    for k, u in stocks.UNIVERSES.items():
        if k in ql:
            uni = u
            break
    fac_col = "composite"
    fac_name = "composite"
    for fn, col in (("value", "value_score"), ("quality", "quality_score"),
                    ("growth", "growth_score"), ("momentum", "momentum_score"),
                    ("risk", "risk_score")):
        if fn in ql:
            fac_name, fac_col = fn, col
            break
    rows = q_or_none(
        q, f"SELECT symbol, rank, composite, value_score, quality_score, growth_score, "
           f"momentum_score, risk_score FROM stock_scores "
           f"WHERE universe = %s AND eligible AND ts = ("
           f"  SELECT max(ts) FROM stock_scores WHERE universe = %s AND ts >= now() - interval '14 days') "
           f"ORDER BY {fac_col} DESC NULLS LAST LIMIT 5", (uni, uni))
    if not rows:
        return None
    picks = []
    for r in rows:
        item = {"symbol": r[0], "rank": r[1], "composite": round(r[2], 2) if r[2] is not None else None}
        if len(r) >= 8 and r[3] is not None:
            item["archetype"] = stocks.stock_archetype(tuple(r[3:8]))
        picks.append(item)
    return {"universe": uni, "sorted_by": fac_name, "top_picks": picks}


def sector_facts(q, question):
    ql = question.lower()
    uni = "SP500" if any(k in ql for k in ("sp500", "s&p", "us")) else "NIFTY500"
    rows = q_or_none(
        q, "SELECT sector, median_pe, median_pb, median_roe, median_upside_pct FROM sector_metrics "
           "WHERE universe = %s AND ts = ("
           "  SELECT max(ts) FROM sector_metrics WHERE universe = %s AND ts >= now() - interval '14 days') "
           "ORDER BY median_pe ASC NULLS LAST LIMIT 12", (uni, uni))
    if not rows:
        return None
    return {"universe": uni, "sectors": [
        {"sector": s, "median_pe": round(pe, 2) if pe is not None else None,
         "median_pb": round(pb, 2) if pb is not None else None,
         "median_roe": round(roe, 4) if roe is not None else None,
         "median_upside_pct": round(up, 2) if up is not None else None}
        for s, pe, pb, roe, up in rows
    ]}


def regime_facts(q):
    f = macro_facts(q)
    idx = q_or_none(
        q, "SELECT DISTINCT ON (ticker) ticker, close FROM index_prices "
           "WHERE ticker IN ('^NSEI','^GSPC','^VIX','^INDIAVIX','GC=F') "
           "AND ts >= now() - interval '10 days' ORDER BY ticker, ts DESC")
    if idx:
        f["indices"] = {t: round(c, 2) for t, c in idx if c is not None}
    return f if f else None


def scorecard_facts(q):
    rows = q_or_none(
        q, "SELECT horizon_days, count(*), "
           "avg(CASE WHEN excess > 0 THEN 100.0 ELSE 0.0 END), avg(excess) * 100.0 "
           "FROM prediction_outcomes WHERE ts >= now() - interval '2 years' "
           "GROUP BY horizon_days ORDER BY horizon_days")
    if not rows:
        return None
    return {"horizons": [
        {"horizon_days": h, "resolved_picks": n,
         "hit_rate_pct": round(hr, 2) if hr is not None else None,
         "mean_excess_pct": round(ex, 2) if ex is not None else None}
        for h, n, hr, ex in rows
    ]}


def earnings_facts(q):
    rows = q_or_none(
        q, "SELECT symbol, earnings_date FROM earnings_calendar "
           "WHERE earnings_date >= current_date AND earnings_date <= current_date + 7 "
           "ORDER BY earnings_date, symbol LIMIT 15")
    if not rows:
        return None
    return {"upcoming_earnings": [{"symbol": s, "date": str(d)} for s, d in rows]}


def build_facts(q, question):
    """-> (facts, subject) or (None, None) when the question maps to nothing we store."""
    # "TCS outlook" is a question ABOUT TCS: try the whole phrase, then each word as a symbol.
    for cand in [question] + question.split():
        if cand.lower().strip("?.,") in router_stop_words():
            continue
        row, _problem, _note = stocks.pick_one_ex(q, cand)
        if row and not str(row[0]).startswith("^"):
            return stock_facts(q, row), row[0]
    if TOPICS["screener"].search(question):
        f = screener_facts(q, question)
        if f:
            return f, "screener"
    if TOPICS["sectors"].search(question):
        f = sector_facts(q, question)
        if f:
            return f, "sectors"
    if TOPICS["scorecard"].search(question):
        f = scorecard_facts(q)
        if f:
            return f, "scorecard"
    if TOPICS["earnings"].search(question):
        f = earnings_facts(q)
        if f:
            return f, "earnings"
    if TOPICS["regime"].search(question):
        f = regime_facts(q)
        if f:
            return f, "regime"
    if TOPICS["news"].search(question):
        words = [w for w in re.findall(r"[A-Za-z0-9&\-]{3,}", question) if w.lower() not in
                 ("news", "headline", "recent", "latest", "said", "about", "what", "tell", "any")]
        f = {"headlines": news_facts(q, " ".join(words[:3]))}
        return (f, "news") if f["headlines"] else (None, None)
    if TOPICS["flows"].search(question):
        f = macro_facts(q)
        return (f, "flows") if f.get("flows_rs_cr_latest") else (None, None)
    if TOPICS["fx"].search(question):
        f = macro_facts(q)
        return (f, "fx") if f.get("usdinr") else (None, None)
    f = index_facts(q, question)
    return (f, "index") if f else (None, None)


def router_stop_words():
    return {"what", "why", "how", "when", "where", "which", "who", "whom", "whose",
            "top", "best", "worst", "value", "growth", "quality", "momentum", "risk",
            "sector", "sectors", "regime", "macro", "scorecard", "earnings", "news",
            "latest", "recent", "outlook", "explain", "summary", "show", "tell", "are",
            "is", "the", "for", "in", "on", "of", "and", "or", "to", "with", "from",
            "stocks", "stock", "picks", "nifty", "nifty500", "sp500", "sp400", "sp600",
            "microcap", "vix", "sensex", "market", "hit", "rate", "cheapest", "expensive",
            "upcoming", "calendar"}


SYSTEM = ("You answer a stock-bot question using ONLY the facts JSON given. Copy numbers exactly as given "
          "or rounded to at most 2 decimals (a percent form of a given value is fine: 0.0259 -> 2.59%); "
          "never compute new numbers (no differences, no averages); never add facts the JSON does not "
          "contain. If the facts cannot answer the question, say exactly what they do show instead of "
          "guessing. 2-4 short sentences. Reply as JSON: {\"answer\": \"...\"}.")


def answer(q, question, asker=None):
    """-> reply text. Never raises: gateway down or two failed checks → show the facts."""
    facts, subject = build_facts(q, question)
    if not facts:
        return ("I can only answer from what the pipeline stores: a stock, an index, FX, flows, or recent "
                "news. Try <code>/ask TCS outlook</code>, <code>/ask nifty this week</code>, "
                "<code>/ask fii flows</code>, <code>/ask news infosys</code>.")
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Question: {question[:300]}\nFacts JSON:\n"
                                        + json.dumps(facts, separators=(",", ":"))}]
    directions = subject != "news"          # headline text carries no sign context — numbers-only check there
    if asker is None:
        asker = _ask
    for attempt in (1, 2):
        try:
            text = asker(msgs)
        except Exception:  # noqa: BLE001 — degrade to the facts table
            return _facts_table(facts, subject)
        bad = factcheck.bad_numbers(text, facts) + (factcheck.wrong_directions(text, facts) if directions else [])
        if text and len(text) <= 900 and not bad:
            return f"<b>{subject}</b> — {stocks.esc(text)}"
        msgs += [{"role": "assistant", "content": json.dumps({"answer": text})},
                 {"role": "user", "content": f"These numbers/directions are not supported by the facts: "
                                             f"{', '.join(bad) or 'too long/empty'}. Rewrite using only the facts JSON."}]
    return _facts_table(facts, subject)


def _ask(prompt_msgs):
    import requests
    r = requests.post(os.environ["LLM_BASE_URL"].rstrip("/") + "/chat/completions", timeout=30,
                      headers={"Authorization": "Bearer " + os.environ["LLM_API_KEY"]},
                      json={"model": "general", "temperature": 0.2, "max_tokens": 800,
                            "response_format": {"type": "json_object"}, "messages": prompt_msgs})
    r.raise_for_status()
    content = factcheck.strip_fences(r.json()["choices"][0]["message"]["content"] or "{}")
    try:
        return json.loads(content).get("answer") or ""
    except (ValueError, AttributeError):
        return ""


def _facts_table(facts, subject):
    lines = [f"<b>{subject}</b> — the facts themselves (AI answer failed the check):"]
    for k, v in facts.items():
        if isinstance(v, dict):
            lines += [f"<code>{k}</code> " + " · ".join(f"{a}={b}" for a, b in v.items())]
        elif isinstance(v, list):
            lines += [f"<code>{k}</code>"] + [f"· {x}" for x in v[:4]]
        else:
            lines.append(f"<code>{k}</code> {v}")
    return "\n".join(lines)


def summarize_card(card_html, command="", question="", asker=None):
    """Generate a fact-checked 1-2 sentence AI takeaway for ANY command's output card.
    Uses ONLY numbers present in `card_html` (verified via factcheck.bad_numbers and
    factcheck.wrong_directions). Returns '' when AI is disabled, unavailable, or fails check."""
    if not card_html or card_html.startswith("Usage:"):
        return ""
    if asker is None and (os.environ.get("BOT_AI_RESOLVE") != "1" or not os.environ.get("LLM_API_KEY")):
        return ""
    plain = re.sub(r"<[^>]+>", " ", card_html)
    plain = re.sub(r"\s+", " ", plain).strip()
    nums = factcheck.signed_numbers(plain)
    if not nums:
        return ""
    facts = {"command": command, "numbers": nums, "text": plain[:1200]}
    q_line = f"User focus: {question[:200]}\n" if question else ""
    msgs = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"{q_line}Summarize the key takeaway from this /{command} output in 1-2 short sentences using ONLY the exact numbers present:\n{plain[:1200]}"},
    ]
    if asker is None:
        asker = _ask
    for _attempt in (1, 2):
        try:
            text = asker(msgs)
        except Exception:  # noqa: BLE001
            return ""
        bad = factcheck.bad_numbers(text, facts) + factcheck.wrong_directions(text, facts)
        if text and len(text) <= 500 and not bad:
            return f"\n\n<b>AI Takeaway</b> <i>(fact-checked)</i>\n{stocks.esc(text)}"
        msgs += [
            {"role": "assistant", "content": json.dumps({"answer": text})},
            {"role": "user", "content": f"Unsupported numbers/directions: {', '.join(bad) or 'too long'}. "
                                        "Rewrite using ONLY exact numbers from the card text."},
        ]
    return ""
