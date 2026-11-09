"""Forgiving symbol lookup for /stock, /news, /watch (2026-10-18).

Called only when the exact/name lookup in stocks.resolve() finds nothing. Four layers, cheapest first:

1. **Nicknames** (static map, no network): common short names → symbol
   ("facebook" -> META, "big blue" -> IBM, "reliance" -> RELIANCE.NS).
2. **Fuzzy** (standard library, no network): the query is compared with every active universe
   symbol (".NS" stripped) and company name ("Ltd.", "Inc." … stripped). A clear winner is used
   directly ("relaince" -> RELIANCE.NS); close calls become a "Did you mean" list.
3. **AI** (only if fuzzy cannot decide, and only when enabled): the typed text alone — never the
   chat id, no hints — goes to the shared llm-gateway (`general`). It must name a ticker AND a company that
   agree with our universe (see ai_pick); anything else is ignored.
4. Gateway down / disabled / no answer -> the fuzzy suggestions (or "no match") as before.

Env (bot Deployment): BOT_AI_RESOLVE=1 enables layer 3; LLM_BASE_URL, LLM_API_KEY from Secret
llm-gateway-key (ADR 0022). Answers are cached in-process (no repeated calls for the same text).
"""
import json
import os
import re
import time
from difflib import SequenceMatcher

from nicknames import NICKNAMES

AUTO = 0.84          # fuzzy score needed to pick without asking
MARGIN = 0.06        # ... and lead over the runner-up
SUGGEST = 0.55       # below this a candidate is not worth suggesting
MIN_AUTO_LEN = 5     # shorter queries are never auto-picked by fuzzy
SUFFIX = re.compile(r"\b(ltd|limited|inc|corp|corporation|co|company|plc|the|class [abc]|\(class [abc]\))\b\.?", re.I)
_universe = {"rows": None, "at": 0.0}
_ai_cache = {}
_nickname_cache = {}


def norm(s):
    s = SUFFIX.sub(" ", (s or "").lower().replace(".ns", " ").replace("&", " and "))
    return re.sub(r"[^a-z0-9 ]+", " ", s).split()


def score(query, symbol, name):
    """0..1 similarity of a typed query to one universe row (pure)."""
    q = " ".join(norm(query))
    if not q:
        return 0.0
    sym = symbol.upper().removesuffix(".NS").lower()
    words = norm(name)
    full = " ".join(words)
    best = max(SequenceMatcher(None, q, sym).ratio(),
               SequenceMatcher(None, q, full).ratio(),
               SequenceMatcher(None, q, " ".join(words[:len(q.split())])).ratio())   # name prefix
    if len(q) >= 4 and full.startswith(q):
        best = max(best, 0.9)
    return best


def universe(q, ttl=3600):
    """Active universe rows, cached for an hour (998 rows — cheap)."""
    if not _universe["rows"] or time.time() - _universe["at"] > ttl:
        rows = q("SELECT symbol, name, sector, universe, currency FROM universe_constituents "
                 "WHERE active ORDER BY symbol")
        if rows:                                  # never cache an empty answer (a DB blip)
            _universe.update(rows=rows, at=time.time())
        return rows or []
    return _universe["rows"]


def fuzzy(rows, query, k=5):
    """[(score, row)] best first, only candidates above SUGGEST."""
    ranked = sorted(((score(query, r[0], r[1]), r) for r in rows), key=lambda x: -x[0])
    return [(s, r) for s, r in ranked[:k] if s >= SUGGEST]


def nickname(query):
    """Zero-network nickname lookup. Returns (row,) or None.

    row = (symbol, name, sector, universe, currency) — sector/universe/currency
    are placeholders; callers that need them should query the DB with the symbol.
    """
    key = " ".join(norm(query))
    if key in _nickname_cache:
        return _nickname_cache[key]
    sym = NICKNAMES.get(key)
    row = None
    if sym and sym != "none":
        row = (sym, key.title(), "", "", "")
    _nickname_cache[key] = row
    return row


def initials(text):
    return "".join(w[0] for w in norm(text))


def agrees(company, row):
    """Does the AI's company name fit this universe row? (name similarity, or initials: IBM)"""
    if not company:
        return False
    names = {" ".join(norm(row[1])), row[0].upper().removesuffix(".NS").lower()}
    return score(company, row[0], row[1]) >= 0.6 or initials(company) in names


def ai_pick(query, candidates, rows, asker=None):
    """Ask the gateway which company was meant; return a universe row or None. Never raises.

    No fuzzy hints in the prompt (2026-10-19: hints steered the model to wrong rows — "zomato" -> a hint).
    Two signals must agree with OUR data: the ticker exists and its name fits the AI's company name, or the
    AI's company name alone is an unambiguous fuzzy match (handles stale/foreign tickers: HUL, ZOMATO.NS)."""
    if os.environ.get("BOT_AI_RESOLVE") != "1" or not os.environ.get("LLM_API_KEY"):
        return None
    key = " ".join(norm(query))
    if key in _ai_cache:
        sym, company = _ai_cache[key]
    else:
        prompt = (f'A user of a stock bot typed: "{query}". Which listed company do they most likely mean? The bot '
                  "covers the S&P 500 (tickers like AAPL, BRK-B) and the NIFTY 500 (NSE tickers ending in .NS). Use "
                  "the CURRENT ticker (companies get renamed). Reply as JSON "
                  '{"symbol": "TICKER", "company": "official company name"}, or '
                  '{"symbol": null, "company": null} if unsure.')
        try:
            sym, company = (asker or _ask)(prompt)
        except Exception:  # noqa: BLE001 — the bot must answer even if the gateway is down
            return None
        _ai_cache[key] = (sym, company)
    if sym:
        by_symbol = {r[0].upper(): r for r in rows}
        s = str(sym).strip().upper()
        row = by_symbol.get(s) or by_symbol.get(s + ".NS")
        if row and agrees(company, row):
            return row
    if company:
        cands = fuzzy(rows, company, 2)
        if cands and cands[0][0] >= AUTO and cands[0][0] - (cands[1][0] if len(cands) > 1 else 0) >= MARGIN:
            return cands[0][1]
    return None


def _ask(prompt):
    """-> (symbol, company) from the gateway's `general` capability (fast-json misnamed nicknames)."""
    import requests
    r = requests.post(os.environ["LLM_BASE_URL"].rstrip("/") + "/chat/completions", timeout=20,
                      headers={"Authorization": "Bearer " + os.environ["LLM_API_KEY"]},
                      json={"model": os.environ.get("BOT_AI_MODEL", "general"), "temperature": 0,
                            "max_tokens": 800, "response_format": {"type": "json_object"},
                            "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    content = (r.json()["choices"][0]["message"]["content"] or "{}").strip()
    if content.startswith("```"):                 # gemma-4 wraps JSON in a code block
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        d = json.loads(content)
        return d.get("symbol"), d.get("company")
    except (ValueError, AttributeError):
        return None, None


def suggest(q, query, asker=None):
    """-> (row, note) to show a stock, or (None, message) with suggestions / no match.

    Layers, cheapest first:
      1. Nickname map (zero network)
      2. Fuzzy match against universe
      3. AI gateway pick (if BOT_AI_RESOLVE=1)
    """
    # 1. Nickname map
    nick = nickname(query)
    if nick:
        return nick, "nickname"

    # 2. Fuzzy match
    rows = universe(q)
    cands = fuzzy(rows, query)
    top = cands[0][0] if cands else 0.0
    second = cands[1][0] if len(cands) > 1 else 0.0
    # short text is never auto-picked by fuzzy: "coke" scored 0.86 against OKE (Oneok), live 2026-10-19
    if cands and top >= AUTO and top - second >= MARGIN and len("".join(norm(query))) >= MIN_AUTO_LEN:
        return cands[0][1], "fuzzy"

    # 3. AI gateway pick
    hit = ai_pick(query, cands, rows, asker)
    if hit:
        return hit, "ai"

    if cands:
        return None, "Did you mean:\n" + "\n".join(f"<code>{_esc(r[0])}</code> — {_esc(r[1])}" for _s, r in cands[:4])
    return None, None


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
