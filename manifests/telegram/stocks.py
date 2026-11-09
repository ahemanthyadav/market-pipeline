"""Stock, index, watchlist, screener and macro views shared by the Telegram image
(bot.py commands, digest.py sections, alerts.py) — added 2026-10-10.

Everything here READS existing tables; the one table this image owns is
`watchlist` (schema-configmap.yaml), written only by bot.py's /watch command.
No psycopg/requests import on purpose: every function takes `q`, a callable
`q(sql, args) -> list[tuple]`, so the logic runs under tests/test_stocks.py
with a fake and under bot/digest/alerts with their own connection handling.

Units as stored by the screener layer (stock_metrics.py / stock_screener.py):
returns, ratios-minus-one, vol, drawdown, ROE and margins are FRACTIONS;
factor scores are percentile ranks in [0,1], higher = better (risk_score too:
higher = steadier); composite is 0-100. Timestamps are UTC midnight per
trading date, identical across stock_prices / stock_metrics_daily / stock_scores.

Message layout (Telegram HTML): lists are fixed-width tables inside <pre>,
kept to ~36 characters so a phone shows them without wrapping; single
instruments are a bold headline plus label/value blocks (`kv`).

Chunk pruning: stock_prices has ~3,400 chunks. Its time bounds must be
parameters or now() arithmetic — a date_trunc() or sub-select bound planned
for 11 s / ran 79 s on 2026-10-10. Keep bounds computed in Python.

trim_html (2026-10-25): every send path cuts an over-long message with
trim_html, never a raw slice — a cut mid-tag makes Telegram's HTML parser
return 400 and the whole message is lost.
"""

import html
import math
import os
import re
from datetime import datetime, timedelta, timezone

TOP_N = 20                    # "the shortlist" for movers / track record
HORIZONS = (1, 5, 21, 63)     # trading days: 1d, 1w, 1m, 3m
MIN_FULL_RUN = 40             # eligible rows a run needs to count (skips canaries)
FRESH_DAYS = 1                # alerts fire only on data dated today/yesterday UTC
BIG_MOVE = 0.05               # watchlist day-move alert threshold
OZ_GRAMS = 31.1034768
FACTORS = {
    "composite": "composite", "value": "value_score", "quality": "quality_score",
    "growth": "growth_score", "momentum": "momentum_score", "risk": "risk_score",
}
UNIVERSES = {"nifty": "NIFTY500", "nifty500": "NIFTY500", "india": "NIFTY500",
             "sp500": "SP500", "sp": "SP500", "us": "SP500",
             # ADR 0029 expansion universes
             "sp400": "SP400", "midcap": "SP400", "sp600": "SP600",
             "smallcap": "SP600", "microcap": "NIFTYMICROCAP250",
             "micro": "NIFTYMICROCAP250"}
SYMBOL_RE = re.compile(r"[A-Za-z0-9 .&\-]{1,40}")

# /world layout: region → index_prices tickers, in display order. A ticker the
# fetchers add later shows up under "Other" until it is placed here.
REGIONS = [
    ("India", ["^NSEI", "^BSESN", "^NSEBANK", "^CRSLDX", "NIFTYMIDCAP150.NS",
               "NIFTYSMLCAP250.NS", "^CNXIT", "^CNXPHARMA", "^INDIAVIX"]),
    ("US", ["^GSPC", "^DJI", "^IXIC", "^RUI", "^RUT", "^VIX"]),
    ("Europe", ["^STOXX50E", "^FTSE", "^GDAXI", "^FCHI", "FTSEMIB.MI", "^IBEX",
                "^AEX", "^SSMI"]),
    ("Asia-Pacific", ["^N225", "^HSI", "000001.SS", "399001.SZ", "^KS11", "^TWII",
                      "^STI", "^AXJO"]),
    ("Americas", ["^GSPTSE", "^BVSP", "^MXX"]),
    ("Commodities & dollar", ["GC=F", "SI=F", "CL=F", "DX-Y.NYB"]),
]
REGION_WORDS = {"india": "India", "us": "US", "usa": "US", "europe": "Europe",
                "asia": "Asia-Pacific", "apac": "Asia-Pacific", "americas": "Americas",
                "commodities": "Commodities & dollar", "gold": "Commodities & dollar"}
NOT_RANKED = {"^VIX", "^INDIAVIX"}   # volatility gauges: no "best/worst YTD"
FX_ORDER = ["USDINR=X", "EURINR=X", "GBPINR=X", "JPYINR=X", "EURUSD=X", "GBPUSD=X",
            "USDJPY=X", "USDCHF=X", "USDCNY=X", "AUDUSD=X"]
# Table-width names for the long ticker_names entries (tables clip at 13 chars).
SHORT = {
    "Nifty Midcap 150": "Nifty Mid 150", "Nifty Smallcap 250": "Nifty Sml 250",
    "Nasdaq Composite": "Nasdaq", "Shanghai Composite": "Shanghai",
    "Shenzhen Component": "Shenzhen", "Straits Times Index": "Straits Times",
    "Swiss Market Index": "Swiss SMI", "S&P/TSX Composite": "TSX Canada",
    "Gold Futures": "Gold", "Silver Futures": "Silver",
    "WTI Crude Oil Futures": "WTI crude", "US Dollar Index (DXY)": "Dollar (DXY)",
}
DIGEST_QUOTES = ["^NSEI", "^BSESN", "^GSPC", "^VIX", "^INDIAVIX", "DX-Y.NYB", "GC=F"]

# Index ticker → universe, so an index card can show constituent-level
# valuation (median P/E, P/B, total market cap) computed from stock_fundamentals.
# Non-stock indices (VIX, gold, dollar, …) have no mapping → no quant block.
INDEX_UNIVERSE = {
    "^GSPC": "SP500", "^SP400": "SP400", "^SP600": "SP600",
    "^NSEI": "NIFTY500", "^BSESN": "NIFTY500",
    "NIFTY_MICROCAP250.NS": "NIFTYMICROCAP250",
}


# ---------------------------------------------------------------- formatting

def pct(x, digits=1):
    return "n/a" if x is None else f"{x * 100:+.{digits}f}%"


def share(x, digits=0):
    """An unsigned percentage (ROE, margin, volatility)."""
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


def num(x, digits=2):
    return "n/a" if x is None else f"{x:,.{digits}f}"


def px(x):
    """A price or index level sized for a table column."""
    if x is None:
        return "n/a"
    return f"{x:,.0f}" if abs(x) >= 10000 else f"{x:,.1f}" if abs(x) >= 1000 else f"{x:,.2f}"


def big(x):
    """1.23T / 936.5B / 12.3M — market caps and the like."""
    if x is None:
        return "n/a"
    for div, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(x) >= div:
            return f"{x / div:,.2f}{unit}"
    return f"{x:,.0f}"


def esc(s):
    return html.escape(str(s))


def clip(s, width):
    s = str(s)
    return s if len(s) <= width else s[: width - 1] + "…"


# Tags our messages use (module docstring) and the atomic units a cut must not split.
TRIM_TAGS = {"pre", "b", "i", "code"}
_TOKEN = re.compile(r"</?[a-zA-Z][^>]*>|&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);")


def _html_tokens(text):
    """text as a list of atomic pieces: whole tags, whole entities, single chars."""
    tokens, pos = [], 0
    for m in _TOKEN.finditer(text):
        tokens += list(text[pos:m.start()])
        tokens.append(m.group(0))
        pos = m.end()
    tokens += list(text[pos:])
    return tokens


def trim_html(text, limit):
    """Cut Telegram-HTML text to <= limit chars, appending '…' inside the current
    scope, without ever splitting a tag or entity; tags still open at the cut
    (TRIM_TAGS) are closed so the Bot API accepts the result."""
    if len(text) <= limit:
        return text
    if limit < 1:
        return ""
    out, stack, used = [], [], 0
    for tok in _html_tokens(text):
        new_stack = stack
        if tok.startswith("</"):
            if stack and stack[-1] == tok[2:-1].strip().lower():
                new_stack = stack[:-1]
        elif tok.startswith("<") and tok.endswith(">"):
            name = tok[1:-1].split()[0].lower()
            if name in TRIM_TAGS:
                new_stack = stack + [name]
        # reserve room for '…' plus a </tag> per still-open tag
        if used + len(tok) + 1 + sum(len(t) + 3 for t in new_stack) > limit:
            break
        out.append(tok)
        used += len(tok)
        stack = new_stack
    return "".join(out) + "…" + "".join(f"</{t}>" for t in reversed(stack))


def table(header, rows, align):
    """Fixed-width <pre> table. `align` is one 'l'/'r' per column; a row that
    is a plain string becomes a section line (blank line before it)."""
    cells = [header] + [r for r in rows if not isinstance(r, str)]
    widths = [max(len(str(r[i])) for r in cells) for i in range(len(header))]

    def line(r):
        return " ".join(str(c).ljust(w) if a == "l" else str(c).rjust(w)
                        for c, w, a in zip(r, widths, align)).rstrip()

    out = [line(header)]
    for r in rows:
        out += ["", r] if isinstance(r, str) else [line(r)]
    return "<pre>" + esc("\n".join(out)) + "</pre>"


def kv(pairs):
    """Label/value block; pairs whose value is None are left out."""
    pairs = [(k, v) for k, v in pairs if v is not None]
    if not pairs:
        return ""
    left = max(len(k) for k, _ in pairs)
    right = max(len(v) for _, v in pairs)
    return "<pre>" + esc("\n".join(f"{k.ljust(left)}  {v.rjust(right)}"
                                   for k, v in pairs)) + "</pre>"


def bare(symbol):
    """TCS.NS → TCS for table columns (the suffix is implied by the universe)."""
    return symbol[:-3] if symbol.endswith(".NS") else symbol


def rsi(closes, period=14):
    """Wilder RSI over closes ordered oldest → newest; None if too short."""
    if len(closes) < period + 1:
        return None
    deltas = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum(d for d in deltas[:period] if d > 0) / period
    loss = sum(-d for d in deltas[:period] if d < 0) / period
    for d in deltas[period:]:
        gain = (gain * (period - 1) + max(d, 0)) / period
        loss = (loss * (period - 1) + max(-d, 0)) / period
    if loss == 0:
        return 100.0
    return 100 - 100 / (1 + gain / loss)


def sma(closes, n):
    return sum(closes[-n:]) / n if len(closes) >= n else None


def back(closes, n):
    """Close `n` sessions before the latest (closes oldest → newest), or None."""
    return closes[-1 - n] if len(closes) > n else None


def change(now, then):
    return None if now is None or not then else now / then - 1


def jan1(now=None):
    """Start of the current UTC year — always passed as a query parameter."""
    return datetime((now or datetime.now(timezone.utc)).year, 1, 1, tzinfo=timezone.utc)


def is_fresh(ts, now=None):
    """True when a UTC-midnight data timestamp is dated today or yesterday."""
    now = now or datetime.now(timezone.utc)
    return ts is not None and ts.date() >= (now - timedelta(days=FRESH_DAYS)).date()


def table_missing(exc):
    """watchlist DDL not applied yet (or the per-user column from schema 09) — callers
    degrade instead of crashing."""
    return exc.__class__.__name__ in ("UndefinedTable", "UndefinedColumn")


# ---------------------------------------------------------------- indices, FX

def performance_sql(table_name, key, value):
    """Latest value per series with its previous-day, 1-month-ago and
    last-of-previous-year values. Identifiers are code constants, never input.
    (index_prices / fx_rates are small; stock_prices must NOT use this.)"""
    def before(cutoff, alias):
        return (
            f"LEFT JOIN LATERAL (SELECT {value} AS v FROM {table_name} x "
            f"  WHERE x.{key} = t.k AND x.ts < {cutoff} "
            f"    AND x.ts >= {cutoff} - interval '20 days' "
            f"  ORDER BY x.ts DESC LIMIT 1) {alias} ON true ")
    return (
        f"SELECT t.k, l.ts, l.v, d.v, m.v, y.v FROM ("
        f"  SELECT DISTINCT {key} AS k FROM {table_name} "
        f"  WHERE ts >= now() - interval '15 days') t "
        f"INNER JOIN LATERAL (SELECT ts, {value} AS v FROM {table_name} x "
        f"  WHERE x.{key} = t.k AND x.ts >= now() - interval '15 days' "
        f"  ORDER BY x.ts DESC LIMIT 1) l ON true "
        + before("date_trunc('day', l.ts)", "d")
        + before("l.ts - interval '29 days'", "m")
        + before("date_trunc('year', l.ts)", "y")
    )


def index_rows(q):
    """({ticker: (ticker, ts, close, prev, month_ago, year_start)}, {ticker: name})"""
    rows = {r[0]: r for r in q(performance_sql("index_prices", "ticker", "close"), ())}
    return rows, dict(q("SELECT ticker, name FROM ticker_names", ()))


def quote_row(name, row):
    _key, _ts, value, prev, _month, year_start = row
    return (clip(SHORT.get(name, name), 13), px(value),
            pct(change(value, prev)), pct(change(value, year_start)))


QUOTE_HEAD = ("", "Close", "Day", "YTD")
QUOTE_LEGEND = "<i>Day = vs previous close · YTD = since 1 Jan</i>"


def quote_table(q, tickers):
    """Close / day / YTD table for a fixed list of index tickers (digest, /vix)."""
    rows, names = index_rows(q)
    body = [quote_row(names.get(t, t), rows[t]) for t in tickers if t in rows]
    if not body:
        return "No index rows in the last 15 days."
    return table(QUOTE_HEAD, body, "lrrr") + "\n" + QUOTE_LEGEND


def parse_world(text):
    """'/world asia' → (region or None, error)."""
    words = text.lower().split()[1:]
    if not words:
        return None, None
    if len(words) == 1 and words[0] in REGION_WORDS:
        return REGION_WORDS[words[0]], None
    return None, ("Usage: <code>/world [india|us|europe|asia|americas|commodities]</code>")


def world_text(q, only=None):
    rows, names = index_rows(q)
    placed = {t for _, tickers in REGIONS for t in tickers}
    body = []
    for region, tickers in REGIONS + [("Other", sorted(set(rows) - placed))]:
        present = [t for t in tickers if t in rows]
        if not present or (only and region != only):
            continue
        body.append(region.upper())
        body += [quote_row(names.get(t, t), rows[t]) for t in present]
    if not body:
        return "No index rows in the last 15 days."
    as_of = max(r[1] for r in rows.values())
    out = [f"<b>World markets</b>  <i>latest closes, to {as_of:%d %b}</i>",
           table(QUOTE_HEAD, body, "lrrr"), QUOTE_LEGEND]
    if not only:
        ranked = sorted(
            (change(r[2], r[5]), names.get(k, k)) for k, r in rows.items()
            if r[5] and k not in NOT_RANKED)
        if len(ranked) >= 2:
            (worst, worst_name), (best, best_name) = ranked[0], ranked[-1]
            out.append(f"Best this year: <b>{esc(best_name)}</b> {pct(best)}\n"
                       f"Worst this year: <b>{esc(worst_name)}</b> {pct(worst)}")
        out.append("<i>/index nikkei for one index in depth</i>")
    return "\n".join(out)


def fx_text(q):
    rows = {r[0]: r for r in q(performance_sql("fx_rates", "pair", "rate"), ())}
    if not rows:
        return "No FX rows in the last 15 days."
    order = [p for p in FX_ORDER if p in rows] + sorted(set(rows) - set(FX_ORDER))
    has_ytd = any(r[5] for r in rows.values())
    body = []
    for pair in order:
        _k, _ts, rate, prev, _m, year_start = rows[pair]
        label = pair.replace("=X", "")
        row = (f"{label[:3]}/{label[3:]}", f"{rate:,.4f}", pct(change(rate, prev), 2))
        body.append(row + ((pct(change(rate, year_start)),) if has_ytd else ()))
    head = ("Pair", "Rate", "Day") + (("YTD",) if has_ytd else ())
    as_of = max(r[1] for r in rows.values())
    out = [f"<b>Currencies</b>  <i>{as_of:%d %b}</i>", table(head, body, "lrrr"[:len(head)])]
    if not has_ytd:
        out.append("<i>Year-to-date appears once the FX history reaches back to 1 Jan.</i>")
    return "\n".join(out)


def find_index(q, arg):
    """[(ticker, name)] for an index/commodity by name fragment or ticker."""
    arg = (arg or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9 .&^=\-/]{1,40}", arg):
        return []
    return q(
        "SELECT n.ticker, n.name FROM ticker_names n "
        "WHERE (upper(n.ticker) = upper(%s) OR n.name ILIKE %s) "
        "AND n.ticker IN (SELECT DISTINCT ticker FROM index_prices "
        "                 WHERE ts >= now() - interval '15 days') "
        "ORDER BY (upper(n.ticker) = upper(%s)) DESC, length(n.name) LIMIT 6",
        (arg, f"%{arg}%", arg))


YEAR = timedelta(days=365.25)
LEVEL_GAUGES = NOT_RANKED   # volatility indices: judge the level itself, not a trend gap
READINGS = ((10, "very low"), (25, "low"), (75, "normal"), (90, "high"), (101, "very high"))


def trailing_average(rows, years):
    """Mean close over the last `years` (rows: [(ts, close)] oldest first), or
    None when the history does not reach that far back."""
    start = rows[-1][0] - years * YEAR
    if rows[0][0] > start + timedelta(days=20):
        return None
    window = [c for t, c in rows if t > start]
    return sum(window) / len(window)


def gap_history(rows, years):
    """close ÷ its own trailing `years`-average − 1, for every day that has a
    full window behind it. One pass with a running sum."""
    span, out, total, left = years * YEAR, [], 0.0, 0
    first = rows[0][0]
    for right, (ts, close) in enumerate(rows):
        total += close
        while rows[left][0] <= ts - span:
            total -= rows[left][1]
            left += 1
        if ts - first >= span - timedelta(days=20):
            out.append(close / (total / (right - left + 1)) - 1)
    return out


def percentile_of(value, history):
    """Mid-rank percentile of `value` within `history`, 0-100: the share strictly
    below it plus half of the ties. Values are compared at 4 decimals so float
    noise cannot turn a tie into a rank."""
    value = round(value, 4)
    rounded = [round(h, 4) for h in history]
    below = sum(h < value for h in rounded)
    ties = sum(h == value for h in rounded)
    return 100.0 * (below + 0.5 * ties) / len(rounded)


def reading(pctile):
    return next(name for limit, name in READINGS if pctile < limit)


def long_term_block(rows, ticker):
    """Lines placing today's level against the index's own long history:
    1/3/5/10-year averages, the high in the data, and a low/normal/high reading.

    The reading for a trending index compares today's gap to its 5-year
    average (3-year when history is shorter) with every past day's gap — a
    rising index is nearly always above its long average, so the level alone
    says nothing. Volatility gauges mean-revert, so their level is ranked
    directly. Needs ~5 years of rows; returns [] below that.
    """
    last_ts, last = rows[-1]
    span_years = (last_ts - rows[0][0]) / YEAR
    body = []
    for years in (1, 3, 5, 10):
        avg = trailing_average(rows, years)
        if avg:
            body.append((f"{years}-year average", px(avg), pct(change(last, avg))))
    if span_years < 2 or not body:
        return []
    high = max(c for _, c in rows)
    body.append((f"High since {rows[0][0]:%Y}", px(high), pct(change(last, high))))
    out = [f"<b>Long-term reference</b>  <i>{span_years:.0f} years of history</i>",
           table(("", "Level", "Now vs"), body, "lrr")]

    if ticker in LEVEL_GAUGES and span_years >= 4.5:
        p = percentile_of(last, [c for _, c in rows])
        out.append(f"<b>Reading: {reading(p)}</b> — higher than on {p:.0f}% of days "
                   f"since {rows[0][0]:%Y}.")
    elif ticker not in LEVEL_GAUGES:
        years = 5 if span_years >= 7.5 else 3 if span_years >= 4.5 else None
        if years:
            gaps = gap_history(rows, years)
            p = percentile_of(gaps[-1], gaps)
            since = (rows[0][0] + years * YEAR).year
            out.append(
                f"<b>Reading: {reading(p)}</b> — {pct(gaps[-1])} against its {years}-year "
                f"average. Relative to that average it was lower on {p:.0f}% of days "
                f"since {since}.")
    if len(out) > 2:
        out.append("<i>Measured against this index's own past. It describes where the "
                   "level sits, not where it goes next.</i>")
    return out


def index_quant(q, universe):
    """Median P/E, median P/B and total market cap for an index's constituents.

    Computed from stock_fundamentals (latest per symbol), so an index card can
    show "quant values" like a stock's /card does. Returns
    (median_pe, median_pb, total_cap, n_pe, n_pb) or None when no data.
    """
    members = q(
        "SELECT symbol FROM universe_constituents "
        "WHERE universe = %s AND active", (universe,))
    symbols = [r[0] for r in members]
    if not symbols:
        return None
    # latest P/E and P/B per symbol (source-agnostic: newest filing wins)
    rows = q(
        "SELECT DISTINCT ON (symbol) symbol, metric, value FROM stock_fundamentals "
        "WHERE symbol = ANY(%s) AND metric = ANY(%s) "
        "ORDER BY symbol, metric, ts DESC",
        (symbols, ["pe", "pb"]))
    pes, pbs = {}, {}
    for symbol, metric, value in rows:
        if metric == "pe" and value is not None:
            pes[symbol] = value
        elif metric == "pb" and value is not None:
            pbs[symbol] = value
    # total market cap from latest snapshot per symbol
    cap_rows = q(
        "SELECT DISTINCT ON (symbol) symbol, value FROM stock_fundamentals "
        "WHERE symbol = ANY(%s) AND metric = 'market_cap' "
        "ORDER BY symbol, ts DESC", (symbols,))
    caps = {s: v for s, v in cap_rows if v is not None}
    if not pes and not pbs and not caps:
        return None
    def med(d):
        vals = sorted(d.values())
        if not vals:
            return None
        m = len(vals) // 2
        return (vals[m] if len(vals) % 2 else (vals[m - 1] + vals[m]) / 2)
    return (med(pes), med(pbs), sum(caps.values()) if caps else None, len(pes), len(pbs))


def index_card(q, ticker, label, now=None):
    """Full picture of one index: recent performance and trend from the last
    year, then the long-term reference from everything on file."""
    rows = q("SELECT ts, close FROM index_prices WHERE ticker = %s "
             "AND ts >= now() - interval '40 years' AND close IS NOT NULL ORDER BY ts",
             (ticker,))
    if not rows:
        return f"{esc(label)}: no data"
    closes = [c for _, c in rows]
    ts, last = rows[-1]
    start = jan1(now or ts)
    before_year = [c for t, c in rows if t < start]

    def since(then):
        return pct(change(last, then)) if then else None

    window = closes[-252:]
    ranged = len(window) >= 60
    span = "52-week" if len(window) >= 240 else f"{len(window)}-session"
    strength = rsi(closes[-60:])
    parts = [
        f"<b>{esc(label)}</b>",
        f"<b>{num(last)}</b>  {pct(change(last, back(closes, 1)), 2)}  <i>{ts:%d %b} close</i>",
        kv([("1 week", since(back(closes, 5))),
            ("1 month", since(back(closes, 21))),
            ("3 months", since(back(closes, 63))),
            ("Year to date", since(before_year[-1] if before_year else None)),
            ("1 year", since(back(closes, 252)))]),
        kv([(f"{span} high", px(max(window)) if ranged else None),
            (f"{span} low", px(min(window)) if ranged else None),
            ("Below the high", pct(change(last, max(window))) if ranged else None),
            ("vs 50-day average", since(sma(closes, 50))),
            ("vs 200-day average", since(sma(closes, 200))),
            ("RSI (14)", None if strength is None else f"{strength:.0f}")]),
    ]
    # Index-level quant values from constituents (median P/E, P/B, total cap).
    universe = INDEX_UNIVERSE.get(ticker)
    if universe:
        q_ = index_quant(q, universe)
        if q_:
            med_pe, med_pb, total_cap, n_pe, n_pb = q_
            parts.append(kv([
                ("P/E (median)", num(med_pe, 1) if med_pe is not None else "–"),
                ("P/B (median)", num(med_pb, 1) if med_pb is not None else "–"),
                ("Market cap", big(total_cap) if total_cap else "–"),
                (f"Constituents", f"{n_pe} w/ P/E · {n_pb} w/ P/B")]))
    parts += long_term_block(rows, ticker)
    return "\n".join(p for p in parts if p)


def index_text(q, arg):
    if not arg:
        return ("Give an index name, e.g. <code>/index nikkei</code> — or /world "
                "for all of them.")
    rows = find_index(q, arg)
    if not rows:
        return f"No index matching <b>{esc(arg)}</b>. /world lists them all."
    if len(rows) > 1 and rows[0][0].upper() != arg.upper():
        return "Several matches:\n" + "\n".join(
            f"<code>/index {esc(t)}</code> — {esc(n)}" for t, n in rows)
    return index_card(q, rows[0][0], rows[0][1])


# ---------------------------------------------------------------- stock YTD

def ytd_map(q, symbols, now=None):
    """{symbol: YTD change} for stocks, two bounded scans of stock_prices."""
    symbols = sorted(set(symbols))
    if not symbols:
        return {}
    start = jan1(now)
    base = dict(q(
        "SELECT DISTINCT ON (symbol) symbol, close FROM stock_prices "
        "WHERE symbol = ANY(%s) AND ts < %s::timestamptz AND ts >= %s::timestamptz "
        "ORDER BY symbol, ts DESC", (symbols, start, start - timedelta(days=20))))
    last = dict(q(
        "SELECT DISTINCT ON (symbol) symbol, close FROM stock_prices "
        "WHERE symbol = ANY(%s) AND ts >= now() - interval '15 days' "
        "ORDER BY symbol, ts DESC", (symbols,)))
    return {sym: change(last.get(sym), base.get(sym)) for sym in symbols
            if change(last.get(sym), base.get(sym)) is not None}


def ytd_tag(value):
    return "" if value is None else f" · YTD {pct(value)}"


# ---------------------------------------------------------------- symbols

def resolve(q, arg):
    """Universe rows matching a user-typed symbol or name fragment.

    Exact symbol (bare NSE symbols get .NS tried too) wins outright; otherwise
    up to 6 name matches so the caller can ask the user to pick.
    Rows: (symbol, name, sector, universe, currency).
    """
    arg = (arg or "").strip()
    if not SYMBOL_RE.fullmatch(arg):
        return []
    up = arg.upper()
    rows = q(
        "SELECT symbol, name, sector, universe, currency FROM universe_constituents "
        "WHERE upper(symbol) = ANY(%s) ORDER BY (upper(symbol) = %s) DESC LIMIT 1",
        ([up, up + ".NS"], up),
    )
    if rows:
        return rows
    return q(
        "SELECT symbol, name, sector, universe, currency FROM universe_constituents "
        "WHERE name ILIKE %s ORDER BY length(name), symbol LIMIT 6",
        (f"%{arg}%",),
    )


def pick_one_ex(q, arg, asker=None):
    """(row, None, note) for a unique match, else (None, message for the user, None).

    Exact symbol / name first (resolve); if nothing matches, the forgiving resolver (resolver.py):
    fuzzy spelling correction, then — if enabled — an AI guess validated against the universe.
    `note` tells the user how a corrected query was read (None for an exact hit)."""
    if not arg:
        return None, "Give a symbol or company name, e.g. <code>/stock TCS</code>", None
    rows = resolve(q, arg)
    if len(rows) > 1:
        return None, "Several matches — use the symbol:\n" + "\n".join(
            f"<code>{esc(s)}</code> — {esc(n)}" for s, n, *_ in rows), None
    if rows:
        return rows[0], None, None
    import resolver
    row, how = resolver.suggest(q, arg, asker)
    if row:
        via = " (matched by AI)" if how == "ai" else ""
        return row, None, f"🔎 <i>“{esc(arg)}” → <b>{esc(row[0])}</b> {esc(row[1])}{via}</i>"
    if how:                                   # "Did you mean" list
        return None, f"No exact match for <b>{esc(arg)}</b>. {how}", None
    return None, f"No stock matching <b>{esc(arg)}</b> in NIFTY500 / SP500.", None


def pick_one(q, arg):
    """(row, None) for a unique match, else (None, message for the user). See pick_one_ex."""
    row, problem, _note = pick_one_ex(q, arg)
    return row, problem


# ---------------------------------------------------------------- /stock

SNAPSHOT = ["pe", "pb", "ev_ebitda", "roe", "operating_margin", "market_cap"]
ANNUAL = [
    "total_revenue", "eps", "net_income", "fcf", "total_debt", "shareholder_equity",
    "total_assets", "gross_profit", "ocf", "shares_outstanding", "current_assets",
    "current_liabilities", "operating_income",
]


FLOW_METRICS = frozenset({
    "total_revenue", "eps", "net_income", "fcf", "gross_profit", "ocf", "operating_income",
})


def series(rows):
    """{metric: [(ts, value), …] newest first} from annual fundamentals.

    rows: (metric, source, ts, value). Per metric the source with the newest
    filing wins (yfinance on a tie), so growth never mixes two sources'
    definitions of the same line item.
    """
    by = {}
    for metric, source, ts, value in rows:
        if value is not None:
            by.setdefault(metric, {}).setdefault(source, []).append((ts, value))
    out = {}
    for metric, sources in by.items():
        if metric in FLOW_METRICS and "yfinance" in sources:
            # EDGAR 10-Q rows are year-to-date (3/6/9 months), so the "newest filing"
            # rule below would compare a half-year with a full year (DQ-2, 2026-10-10).
            out[metric] = sorted(sources["yfinance"], reverse=True)
            continue
        best = max(sources.items(),
                   key=lambda item: (max(t for t, _ in item[1]), item[0] == "yfinance"))[1]
        out[metric] = sorted(best, reverse=True)
    return out


def latest_two(rows):
    """{metric: (latest, previous or None, fiscal ts)} — see series()."""
    return {metric: (vals[0][1], vals[1][1] if len(vals) > 1 else None, vals[0][0])
            for metric, vals in series(rows).items()}


def yoy(annual, metric):
    now_, prev, _ = annual.get(metric, (None, None, None))
    # growth off a negative or zero base is not a meaningful percentage
    return None if now_ is None or prev is None or prev <= 0 else pct(now_ / prev - 1)


def scientific_diagnostics(annual):
    """Compute peer-reviewed accounting & factor diagnostics from `latest_two` annual dict.

    Implements:
    - EV-10 Gross Profitability (GP/A = gross_profit / total_assets) — Novy-Marx (2013), JFE
    - EV-11 Asset Growth (CMA = YoY total_assets change) — Cooper, Gulen & Schill (2008), JF; Fama & French (2015)
    - EV-12 Operating Accruals ((net_income - ocf) / total_assets) — Sloan (1996), Accounting Review
    - EV-13 Net Share Issuance (YoY shares_outstanding change) — Pontiff & Woodgate (2008), JF
    - EV-14 Piotroski F-Score (9 binary accounting signals) — Piotroski (2000), JAR
    - Altman Z''-Score (non-manufacturing / emerging solvency) — Altman (1968, 2000)
    """
    def cur_prev(key):
        val = annual.get(key)
        if not val:
            return None, None
        return val[0], val[1]

    ta, ta_p = cur_prev("total_assets")
    gp, gp_p = cur_prev("gross_profit")
    ni, ni_p = cur_prev("net_income")
    ocf, _ = cur_prev("ocf")
    fcf, _ = cur_prev("fcf")
    rev, rev_p = cur_prev("total_revenue")
    debt, debt_p = cur_prev("total_debt")
    eq, _ = cur_prev("shareholder_equity")
    ca, ca_p = cur_prev("current_assets")
    cl, cl_p = cur_prev("current_liabilities")
    sh, sh_p = cur_prev("shares_outstanding")
    ebit, _ = cur_prev("operating_income")

    out = {}
    if gp is not None and ta and ta > 0:
        out["gpa"] = gp / ta
    cash_flow = ocf if ocf is not None else fcf
    if ni is not None and cash_flow is not None and ta and ta > 0:
        out["accruals"] = (ni - cash_flow) / ta
    if ta is not None and ta_p and ta_p > 0:
        out["asset_growth"] = ta / ta_p - 1.0
    if sh is not None and sh_p and sh_p > 0:
        out["share_issuance"] = sh / sh_p - 1.0

    # Piotroski (2000) 9-signal F-Score
    f_pts, f_tested = 0, 0
    if ni is not None and ta and ta > 0:
        f_tested += 1
        f_pts += int((ni / ta) > 0)
    if cash_flow is not None:
        f_tested += 1
        f_pts += int(cash_flow > 0)
    if ni is not None and ni_p is not None and ta and ta > 0 and ta_p and ta_p > 0:
        f_tested += 1
        f_pts += int((ni / ta) > (ni_p / ta_p))
    if cash_flow is not None and ni is not None:
        f_tested += 1
        f_pts += int(cash_flow > ni)
    if debt is not None and debt_p is not None and ta and ta > 0 and ta_p and ta_p > 0:
        f_tested += 1
        f_pts += int((debt / ta) <= (debt_p / ta_p))
    if ca is not None and cl and cl > 0 and ca_p is not None and cl_p and cl_p > 0:
        f_tested += 1
        f_pts += int((ca / cl) > (ca_p / cl_p))
    if sh is not None and sh_p and sh_p > 0:
        f_tested += 1
        f_pts += int(sh <= sh_p * 1.005)
    if gp is not None and rev and rev > 0 and gp_p is not None and rev_p and rev_p > 0:
        f_tested += 1
        f_pts += int((gp / rev) > (gp_p / rev_p))
    if rev is not None and ta and ta > 0 and rev_p is not None and ta_p and ta_p > 0:
        f_tested += 1
        f_pts += int((rev / ta) > (rev_p / ta_p))
    if f_tested >= 4:
        ratio = f_pts / f_tested
        f_lbl = "strong" if ratio >= 0.70 else ("weak" if ratio <= 0.35 else "neutral")
        out["f_score"] = (f_pts, f_tested, f_lbl)

    # Altman (1968, 2000) Z''-Score for non-manufacturing / general industrials
    if ta and ta > 0 and ca is not None and cl is not None and eq is not None and ebit is not None:
        x1 = (ca - cl) / ta
        x2 = eq / ta
        x3 = ebit / ta
        x4 = (eq / debt) if (debt and debt > 0) else 3.0
        z2 = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * min(x4, 10.0)
        zone = "safe" if z2 > 2.60 else ("distress" if z2 < 1.10 else "grey")
        out["altman_z2"] = (z2, zone)

    return out


def reprice(snapshot, close, snapshot_close):
    """Carry price-based ratios from the weekly Yahoo snapshot to today's close.

    stock_fundamentals stores P/E, P/B and market cap as Yahoo reported them on
    the fetch day (Saturdays). Earnings, book value and share count do not move
    between fetches, so each scales with price. EV/EBITDA is left alone (net
    debt does not scale). Returns (values, repriced?).
    """
    out = dict(snapshot)
    if not close or not snapshot_close:
        return out, False
    factor = close / snapshot_close
    for metric in ("pe", "pb", "market_cap"):
        if out.get(metric) is not None:
            out[metric] = out[metric] * factor
    return out, True


def _median(vals):
    s = sorted(v for v in vals if v is not None and v > 0)
    if not s:
        return None
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def grouped_valuations(fv_row, f, annual, close, universe=""):
    """Build grouped Practitioner and Research-Backed (Academic) intrinsic valuation
    estimates from `v_latest_valuations` (`fv_row`) plus snapshot (`f`) and `annual`.

    Returns dict with:
      - `methods`: dict of {method_key: per_share_value}
      - `practitioner_median`, `practitioner_count`, `practitioner_groups`
      - `academic_median`, `academic_count`, `academic_groups`
    """
    by_key = {}
    r_rate = 0.115 if (universe or "").startswith("NIFTY") else 0.095
    g_rev = None
    if fv_row and len(fv_row) >= 13:
        col_names = ("graham", "peer_multiple", "fcf_yield", "dcf", "ddm",
                     "epv", "residual_income", "peg_lynch", "damodaran_fcff",
                     "graham_growth")
        for k, v in zip(col_names, fv_row[3:13]):
            if v is not None and v > 0:
                by_key[k] = float(v)
        if len(fv_row) >= 14 and fv_row[13]:
            r_rate = float(fv_row[13])
        if len(fv_row) >= 15 and fv_row[14] is not None:
            g_rev = float(fv_row[14])

    f = f or {}
    annual = annual or {}
    mcap = f.get("market_cap")
    shares = (mcap / close) if (mcap and mcap > 0 and close and close > 0) else None
    eps_ann = annual.get("eps", (None, None))[0]
    eps = eps_ann if (eps_ann and eps_ann > 0) else (
        (close / f["pe"]) if (close and f.get("pe") and f["pe"] > 0) else None
    )
    eq = annual.get("shareholder_equity", (None, None))[0]
    bvps = (eq / shares) if (eq and eq > 0 and shares) else (
        (close / f["pb"]) if (close and f.get("pb") and f["pb"] > 0) else None
    )
    fcf = annual.get("fcf", (None, None))[0]
    debt = annual.get("total_debt", (None, None))[0] or 0.0
    roe = f.get("roe")
    gp = annual.get("gross_profit", (None, None))[0]
    ebit = annual.get("operating_income", (None, None))[0]

    if g_rev is None:
        rev_now, rev_prev, *_ = annual.get("total_revenue", (None, None, None))
        if rev_now and rev_prev and rev_prev > 0:
            g_rev = max(0.0, min(0.15, rev_now / rev_prev - 1.0))

    eps_now, eps_prev, *_ = annual.get("eps", (None, None, None))
    g_eps = None
    if eps_now and eps_prev and eps_prev > 0 and eps_now > 0:
        g_eps = max(0.05, min(0.25, eps_now / eps_prev - 1.0))
    elif g_rev is not None and g_rev > 0:
        g_eps = max(0.05, min(0.25, g_rev))

    # Fill standard methods if not already populated from DB row
    if "graham" not in by_key and eps and bvps:
        by_key["graham"] = (22.5 * eps * bvps) ** 0.5
    if "graham_growth" not in by_key and eps and g_eps is not None:
        gg = min(0.15, g_eps)
        y_pct = (r_rate - 0.045) * 100.0 + 1.0
        if y_pct > 0:
            by_key["graham_growth"] = eps * (8.5 + 2.0 * gg * 100.0) * 4.4 / y_pct
    if "peg_lynch" not in by_key and eps and g_eps is not None:
        by_key["peg_lynch"] = eps * g_eps * 100.0
    if "fcf_yield" not in by_key and fcf and fcf > 0 and shares and r_rate > 0:
        by_key["fcf_yield"] = (fcf / shares) / r_rate
    if "dcf" not in by_key and fcf and fcf > 0 and shares and g_rev is not None and r_rate > 0.025:
        pv, fc = 0.0, fcf
        for t in range(1, 6):
            fc *= 1.0 + g_rev
            pv += fc / ((1.0 + r_rate) ** t)
        pv += (fc * 1.025 / (r_rate - 0.025)) / ((1.0 + r_rate) ** 5)
        by_key["dcf"] = pv / shares
    if "residual_income" not in by_key and roe and bvps and roe > r_rate and r_rate > 0.025:
        jpb = min((roe - 0.025) / (r_rate - 0.025), 8.0)
        by_key["residual_income"] = jpb * bvps
    if "epv" not in by_key and ebit and ebit > 0 and shares and r_rate > 0:
        epv_eq = (ebit * 0.75 / r_rate) - debt
        if epv_eq > 0:
            by_key["epv"] = epv_eq / shares
    if ("damodaran_fcff" not in by_key and ebit and ebit > 0 and shares
            and mcap and eq and eq > 0 and g_rev is not None):
        nopat = ebit * 0.75
        roc = nopat / (debt + eq)
        rf = r_rate - 0.045
        kd = (rf + 0.02) * 0.75
        wacc = (mcap * r_rate + debt * kd) / (mcap + debt)
        if roc > 0.025 and wacc > 0.025:
            rr = max(0.0, min(0.90, g_rev / roc))
            pv, n = 0.0, nopat
            for t in range(1, 6):
                n *= 1.0 + g_rev
                pv += n * (1.0 - rr) / ((1.0 + wacc) ** t)
            rr_t = max(0.0, min(0.90, 0.025 / roc))
            pv += (n * 1.025 * (1.0 - rr_t) / (wacc - 0.025)) / ((1.0 + wacc) ** 5)
            if pv - debt > 0:
                by_key["damodaran_fcff"] = (pv - debt) / shares

    # Extended Practitioner & Research-Backed methods
    if fcf and fcf > 0 and shares and r_rate > 0.025:
        oe_eq = fcf * 1.025 / (r_rate - 0.025) - debt
        if oe_eq > 0:
            by_key["owner_earnings"] = oe_eq / shares
    if by_key.get("peg_lynch") and r_rate > 0:
        by_key["easton_mpeg"] = by_key["peg_lynch"] / ((r_rate * 10.0) ** 2)
    if (gp and ebit is not None and gp > ebit and eq and eq > 0
            and shares and roe and r_rate > 0.025):
        ovh = gp - ebit
        adj_eq = eq + 1.5 * ovh
        adj_roe = (roe * eq + 0.10 * ovh) / adj_eq
        if adj_roe > r_rate:
            adj_jpb = min((adj_roe - 0.025) / (r_rate - 0.025), 8.0)
            by_key["intangible_ri"] = adj_jpb * (adj_eq / shares)
    sh_now, sh_prev, *_ = annual.get("shares_outstanding", (None, None, None))
    if sh_now and sh_prev and sh_prev > 0 and sh_now < sh_prev and mcap and shares and r_rate > 0.025:
        bb_yield = min(0.08, (sh_prev - sh_now) / sh_prev)
        bb_cash = bb_yield * mcap
        if fcf and fcf > 0:
            bb_cash = min(bb_cash, fcf)
        gp_rate = min(0.06, max(0.0, g_rev or 0.025))
        if bb_cash > 0 and r_rate > gp_rate:
            by_key["total_payout_ddm"] = (bb_cash / shares) * (1.0 + gp_rate) / (r_rate - gp_rate)

    prac_groups = [
        ("Graham Conservative", [("Graham #", by_key.get("graham")),
                                 ("Graham 74", by_key.get("graham_growth"))]),
        ("GARP & Peer Multiples", [("Lynch PEG=1", by_key.get("peg_lynch")),
                                   ("Sector peer", by_key.get("peer_multiple"))]),
        ("Cash-Flow & Buffett", [("FCF yield", by_key.get("fcf_yield")),
                                 ("2-stage DCF", by_key.get("dcf")),
                                 ("Owner earn.", by_key.get("owner_earnings"))]),
    ]
    acad_groups = [
        ("Clean-Surplus & Intangibles (Ohlson / Peters-Taylor)",
         [("Residual inc.", by_key.get("residual_income")),
          ("Intangible RI", by_key.get("intangible_ri"))]),
        ("Abnormal Earnings Growth (Easton 2004 / OJ 2005)",
         [("Easton MPEG", by_key.get("easton_mpeg"))]),
        ("Corporate Finance (Damodaran / Greenwald)",
         [("Damodaran FCFF", by_key.get("damodaran_fcff")),
          ("Greenwald EPV", by_key.get("epv"))]),
        ("Shareholder Payout (Gordon 1956 / Boudoukh 2007)",
         [("Gordon DDM", by_key.get("ddm")),
          ("Buyback+Div DDM", by_key.get("total_payout_ddm"))]),
    ]
    prac_vals = [v for _, items in prac_groups for _, v in items if v is not None]
    acad_vals = [v for _, items in acad_groups for _, v in items if v is not None]
    return {
        "methods": by_key,
        "practitioner_median": _median(prac_vals),
        "practitioner_count": len(prac_vals),
        "practitioner_groups": [(g, [(l, v) for l, v in items if v is not None])
                                for g, items in prac_groups
                                if any(v is not None for _, v in items)],
        "academic_median": _median(acad_vals),
        "academic_count": len(acad_vals),
        "academic_groups": [(g, [(l, v) for l, v in items if v is not None])
                            for g, items in acad_groups
                            if any(v is not None for _, v in items)],
    }


# ------------------------------------------------------------------ /valuation

# (column, label, lens). lens matches grouped_valuations' Practitioner /
# Research-Backed split; ev_ebitda_peer is a multiple-based comp → Practitioner.
VALUATION_METHODS = (
    ("graham", "Graham sqrt(22.5×EPS×BVPS)", "Practitioner"),
    ("graham_growth", "Graham 1974 growth", "Practitioner"),
    ("peg_lynch", "Lynch PEG=1", "Practitioner"),
    ("peer_multiple", "Street comps (sector P/E)", "Practitioner"),
    ("fcf_yield", "FCF yield perpetuity", "Practitioner"),
    ("dcf", "2-stage FCF DCF", "Practitioner"),
    ("owner_earnings", "Owner earnings", "Practitioner"),
    ("ev_ebitda_peer", "EV/EBITDA peer", "Practitioner"),
    ("epv", "Greenwald EPV", "Research-Backed"),
    ("residual_income", "Residual income / justified P/B", "Research-Backed"),
    ("intangible_ri", "Intangible RI (Peters–Taylor)", "Research-Backed"),
    ("easton_mpeg", "Easton MPEG", "Research-Backed"),
    ("damodaran_fcff", "Damodaran FCFF", "Research-Backed"),
    ("ddm", "Gordon DDM", "Research-Backed"),
    ("total_payout_ddm", "Buyback + Div DDM", "Research-Backed"),
)


def valuation_text(q, arg):
    """/valuation <symbol> — every intrinsic valuation method for one stock.

    Reads v_latest_valuations (the 4-day bound lives inside the view, rule 9).
    Shows the close, the median fair value + upside, practitioner vs academic
    medians, and all 15 methods with each one's per-share value, flagging the
    methods that sit above today's close. Descriptive only (ADR 0026) — never
    advice, never a score input.
    """
    row, problem, note = pick_one_ex(q, arg)
    if problem:
        return problem
    symbol, name, sector, universe, currency = row

    # Schema 25 added ev_ebitda_peer + dual-lens columns to stock_valuations
    # and re-created v_latest_valuations to expose them. If Schema 25 hasn't
    # been applied yet, fall back to the pre-Schema 25 column set so the
    # command still works (Schema 25 methods show as n/a).
    try:
        fv = q(
            "SELECT ts, close, graham, peer_multiple, fcf_yield, dcf, ddm, epv, "
            "residual_income, peg_lynch, damodaran_fcff, graham_growth, "
            "fair_value, upside_pct, methods, discount_rate, growth_used, "
            "methods_above_close, ev_ebitda_peer, owner_earnings, easton_mpeg, "
            "intangible_ri, total_payout_ddm, practitioner_fair_value, "
            "practitioner_methods, academic_fair_value, academic_methods, "
            "all_fair_value, all_methods "
            "FROM v_latest_valuations WHERE symbol = %s",
            (symbol,),
        )
        schema25 = True
    except Exception as exc:  # noqa: BLE001 — degrade when Schema 25 is pending
        if exc.__class__.__name__ not in ("UndefinedColumn", "UndefinedTable"):
            raise
        fv = q(
            "SELECT ts, close, graham, peer_multiple, fcf_yield, dcf, ddm, epv, "
            "residual_income, peg_lynch, damodaran_fcff, graham_growth, "
            "fair_value, upside_pct, methods, discount_rate, growth_used, "
            "methods_above_close "
            "FROM v_latest_valuations WHERE symbol = %s",
            (symbol,),
        )
        schema25 = False
    if not fv:
        return "\n".join(
            [f"<b>{esc(name)}</b>",
             f"{esc(symbol)} · {esc(sector or 'n/a')} · {esc(universe)}",
             "No valuation rows in the last 4 days."])
    row = fv[0]
    (ts, close, graham, peer_multiple, fcf_yield, dcf, ddm, epv,
     residual_income, peg_lynch, damodaran_fcff, graham_growth,
     fair_value, upside_pct, methods, discount_rate, growth_used,
     methods_above_close) = row[:18]
    if schema25:
        (ev_ebitda_peer, owner_earnings, easton_mpeg, intangible_ri,
         total_payout_ddm, practitioner_fair_value, practitioner_methods,
         academic_fair_value, academic_methods, all_fair_value, all_methods) = row[18:]
    else:
        ev_ebitda_peer = owner_earnings = easton_mpeg = intangible_ri = None
        total_payout_ddm = practitioner_fair_value = practitioner_methods = None
        academic_fair_value = academic_methods = all_fair_value = all_methods = None

    head = [f"<b>{esc(name)}</b>",
            f"{esc(symbol)} · {esc(sector or 'n/a')} · {esc(universe)}"]
    if note:
        head.insert(0, note)
    out = head

    vals = {
        "graham": graham, "peer_multiple": peer_multiple, "fcf_yield": fcf_yield,
        "dcf": dcf, "ddm": ddm, "epv": epv, "residual_income": residual_income,
        "peg_lynch": peg_lynch, "damodaran_fcff": damodaran_fcff,
        "graham_growth": graham_growth, "ev_ebitda_peer": ev_ebitda_peer,
        "owner_earnings": owner_earnings, "easton_mpeg": easton_mpeg,
        "intangible_ri": intangible_ri, "total_payout_ddm": total_payout_ddm,
    }
    above_close = {col for col, v in vals.items()
                   if v is not None and close and v > close}

    out += ["", f"<b>{num(close)} {esc(currency or '')}</b>  <i>{ts:%d %b} close</i>"]
    out.append(
        f"<b>Fair value</b> {num(fair_value)}  ({upside_pct:+.1f}% vs close)  "
        f"<i>{methods} method(s) produced a value · {methods_above_close} above close</i>")
    if practitioner_fair_value is not None and academic_fair_value is not None:
        out.append(
            f"Practitioner median <b>{num(practitioner_fair_value)}</b> "
            f"({practitioner_methods}m) · "
            f"Academic median <b>{num(academic_fair_value)}</b> ({academic_methods}m)")
    if discount_rate is not None:
        out.append(
            f"<i>Discount rate {discount_rate * 100:.1f}% · growth used "
            f"{growth_used * 100:.1f}% (ADR 0026 assumptions)</i>")

    for lens in ("Practitioner", "Research-Backed"):
        lines = []
        for col, label, method_lens in VALUATION_METHODS:
            if method_lens != lens:
                continue
            v = vals.get(col)
            mark = " ↑above close" if col in above_close else ""
            lines.append(f"{label}: {num(v)}{mark}")
        out.append(f"<b>{lens} methods</b>")
        out += lines

    out += ["<i>All 15 methods from v_latest_valuations; fair_value is the median of the "
            "methods that produced a number. Descriptive only — not advice.</i>"]
    return "\n".join(line for i, line in enumerate(out) if line or i == 2)


# ------------------------------------------------------------------ /calibration

CALIBRATION_KIND_LABELS = {
    "factor_ic": "Factor IC (cross-sectional Spearman rank IC)",
    "sector_ic": "Sector IC (distortion by sector)",
    "archetype_ret": "Practitioner Archetype Excess Return",
    "academic_archetype_ret": "Academic Archetype Excess Return",
    "mb_gmv": "Multibagger Gate Marginal Value",
    "shrunk_weight": "Shrunk Factor Weight (vs prior)",
}
CALIBRATION_KIND_ORDER = ("factor_ic", "sector_ic", "archetype_ret",
                          "academic_archetype_ret", "mb_gmv", "shrunk_weight")


def calibration_text(q):
    """/calibration — latest factor & archetype calibration results.

    Reads v_latest_calibration (written by ops-tools/classifier_loop.py,
    ADR 0033). Shows factor ICs, archetype cohort excess returns, multibagger
    gate marginal value, and shrunk factor weights vs the prior (0.25/0.25/0.20/0.20/0.10).
    Evidence-based — never a score input, never a forecast.
    """
    try:
        rows = q(
            "SELECT horizon_days, kind, metric_key, universe, sample_n, value, aux_value, status "
            "FROM v_latest_calibration ORDER BY kind, metric_key")
    except Exception as exc:  # noqa: BLE001 - table may not exist yet
        if not table_missing(exc):
            raise
        return ("Calibration tables not created yet. "
                "Run the Schema 25 job, then ops-tools/classifier_loop.py.")
    if not rows:
        return "No calibration results yet — the classifier loop runs daily."

    by_kind = {}
    for horizon, kind, metric_key, universe, sample_n, value, aux_value, status in rows:
        by_kind.setdefault(kind, []).append(
            (horizon, metric_key, universe, sample_n, value, aux_value, status))

    out = ["<b>Factor &amp; Archetype Calibration</b>  <i>(classifier loop, latest run)</i>"]
    for kind in CALIBRATION_KIND_ORDER:
        items = by_kind.get(kind)
        if not items:
            continue
        out.append(f"<b>{CALIBRATION_KIND_LABELS[kind]}</b>")
        for horizon, metric_key, universe, sample_n, value, aux_value, status in items:
            v_str = num(value, 3) if value is not None else "–"
            a_str = f" / {num(aux_value, 3)}" if aux_value is not None else ""
            out.append(
                f"  {esc(metric_key)} ({universe}, n={sample_n}): {v_str}{a_str}  "
                f"<i>{esc(status or '—')}</i>")
    out += [
        "",
        "<i>Calibration measures empirical selection value — not a forecast. "
        "ADR 0033 guardrails: max ±5% factor-weight tilt, min 6 cross-sections.</i>"]
    return "\n".join(line for i, line in enumerate(out) if line or i == 2)


# ------------------------------------------------------------------ /methods

def methods_text(q):
    """/methods — valuation & scientific method catalog (Schema 25).

    Reads v_valuation_methods. Shows all catalogued metrics with their
    lens, sub-school, formula and peer-reviewed source. Reference only —
    descriptive, never advice.
    """
    try:
        rows = q(
            "SELECT method_key, method_name, category, lens, sub_school, "
            "source_ref, formula FROM v_valuation_methods "
            "ORDER BY display_order, method_key")
    except Exception as exc:  # noqa: BLE001 - table may not exist yet
        if not table_missing(exc):
            raise
        return ("Valuation method catalog not created yet. "
                "Run the Schema 25 job.")
    if not rows:
        return "No valuation methods catalogued yet."

    by_category = {}
    for key, name, category, lens, sub_school, source_ref, formula in rows:
        by_category.setdefault(category, []).append(
            (key, name, lens, sub_school, source_ref, formula))

    out = ["<b>Valuation &amp; Scientific Method Catalog</b>  "
           "<i>(Schema 25, v_valuation_methods)</i>"]
    for category in sorted(by_category):
        out.append(f"<b>{esc(category)}</b>")
        for key, name, lens, sub_school, source_ref, formula in by_category[category]:
            src = f" — {esc(source_ref)}" if source_ref else ""
            out.append(f"  • {esc(name)} [{esc(lens)}]{src}")
            out.append(f"    {esc(formula)}")
    out += [
        "",
        "<i>Catalogued metrics span valuations, scientific diagnostics, factor "
        "pillars, composites, archetype taxonomies and multibagger screens. "
        "Descriptive only — not advice.</i>"]
    return "\n".join(line for i, line in enumerate(out) if line or i == 2)


def stock_card(q, row):
    symbol, name, sector, universe, currency = row
    head = [f"<b>{esc(name)}</b>",
            f"{esc(symbol)} · {esc(sector or 'n/a')} · {esc(universe)}"]

    prices = q(
        "SELECT ts, close FROM stock_prices WHERE symbol = %s "
        "AND ts >= now() - interval '120 days' ORDER BY ts DESC LIMIT 60",
        (symbol,),
    )
    if not prices:
        return "\n".join(head + ["No price rows in the last 120 days."])
    ts, close = prices[0]
    prev = prices[1][1] if len(prices) > 1 else None
    out = head + ["", f"<b>{num(close)} {esc(currency or '')}</b>  "
                      f"{pct(change(close, prev), 2)}  <i>{ts:%d %b} close</i>"]

    start = jan1(ts)
    year_start = q(
        "SELECT close FROM stock_prices WHERE symbol = %s "
        "AND ts < %s::timestamptz AND ts >= %s::timestamptz "
        "ORDER BY ts DESC LIMIT 1", (symbol, start, start - timedelta(days=20)))
    ytd = change(close, year_start[0][0]) if year_start else None

    # daily closes, the same basis as the screener's pos_52w — intraday
    # highs/lows are unadjusted and would contradict the Trend block
    hi52 = q(
        "SELECT max(close), min(close) FROM stock_prices WHERE symbol = %s "
        "AND ts >= now() - interval '52 weeks'", (symbol,))
    hi52v, lo52v = (hi52[0] if hi52 else (None, None))

    m = dict(q(
        "SELECT metric, value FROM stock_metrics_daily WHERE symbol = %s AND ts = ("
        "SELECT max(ts) FROM stock_metrics_daily WHERE symbol = %s "
        "AND ts >= now() - interval '30 days')",
        (symbol, symbol),
    ))

    def frac(key, fmt=pct, shift=0.0):
        return None if m.get(key) is None else fmt(m[key] + shift)

    strength = rsi([c for _, c in reversed(prices)])
    out += ["<b>Performance</b>", kv([
        ("1 month", frac("ret_1m")), ("6 months", frac("ret_6m")),
        ("Year to date", None if ytd is None else pct(ytd)),
        ("12 months", frac("ret_12m")),
        ("52-week high (close)", px(hi52v) if hi52v is not None else None),
        ("52-week low (close)", px(lo52v) if lo52v is not None else None)])]
    out += ["<b>Trend and risk</b>", kv([
        ("vs 50-day average", frac("sma50_ratio", shift=-1.0)),
        ("vs 200-day average", frac("sma200_ratio", shift=-1.0)),
        ("Position in 52-w (% low→high)", frac("pos_52w", share)),
        ("RSI (14)", None if strength is None else f"{strength:.0f}"),
        ("Volatility, annualised", frac("vol_180d", share)),
        ("Max drawdown, 1 year", frac("max_dd_252d", lambda v: pct(v, 0))),
        ("Traded per day, 20d avg", None if m.get("dol_vol_20d") is None
         else big(m["dol_vol_20d"]))])]

    score = q(
        "SELECT ts, composite, rank, value_score, quality_score, growth_score, "
        "momentum_score, risk_score, eligible, coverage FROM stock_scores "
        "WHERE symbol = %s AND ts >= now() - interval '30 days' ORDER BY ts DESC LIMIT 1",
        (symbol,),
    )
    if score:
        sts, comp, rank, v, qu, g, mo, r, eligible, coverage = score[0]
        total = eligible_count(q, sts, universe)
        place = f"rank <b>{rank}</b> of {total}" if eligible and rank else "not eligible"
        conf = screener_confidence(coverage, (v, qu, g, mo, r),
                                   rank_history(q, [symbol]).get(symbol, []), total)
        arch = stock_archetype((v, qu, g, mo, r))
        acad_arch = academic_archetype((v, qu, g, mo, r))
        acad_comp = academic_composite((v, qu, g, mo, r))
        arch_diff = archetype_comparison((v, qu, g, mo, r), comp)
        drivers = stock_drivers((v, qu, g, mo, r))
        out += [f"<b>Screener</b>  {place}  <i>run of {sts:%d %b}</i>", kv([
            ("Composite score", num(comp, 1)),
            ("Value", num(v)), ("Quality", num(qu)),
            ("Growth", num(g)), ("Momentum", num(mo)),
            ("Risk (higher = steadier)", num(r)),
            ("Confidence", f"{conf} {conf_label(conf)}")]),
            f"Profile: <b>{esc(arch)}</b> · drivers: {esc(drivers)}",
            f"<b>Practitioner vs Academic lens:</b> Practitioner = <b>{esc(arch)}</b> "
            f"(25/25/20/20/10 composite {num(comp, 1)}) · Academic (FF5/QMJ/1/N) = "
            f"<b>{esc(acad_arch)}</b> (equal-weight 1/N composite {num(acad_comp, 1)}) — "
            f"<i>{esc(arch_diff)}</i>",
            SCREENER_NOTE, CONF_NOTE]

    snap = {metric: (value, fts) for metric, value, fts in q(
        "SELECT DISTINCT ON (metric) metric, value, ts FROM stock_fundamentals "
        "WHERE symbol = %s AND metric = ANY(%s) ORDER BY metric, ts DESC",
        (symbol, SNAPSHOT))}
    annual = latest_two(q(
        "SELECT metric, source, ts, value FROM stock_fundamentals "
        "WHERE symbol = %s AND metric = ANY(%s)", (symbol, ANNUAL)))
    if snap:
        snap_ts = max(fts for _, fts in snap.values())
        then = q(
            "SELECT close FROM stock_prices WHERE symbol = %s "
            "AND ts <= %s::timestamptz AND ts >= %s::timestamptz "
            "ORDER BY ts DESC LIMIT 1", (symbol, snap_ts, snap_ts - timedelta(days=10)))
        f, repriced = reprice({k: v for k, (v, _) in snap.items()}, close,
                              then[0][0] if then else None)
        when = (f"at the {ts:%d %b} close — P/E, P/B and market cap scaled from "
                f"the {snap_ts:%d %b} snapshot by the price move (earnings, book "
                f"value and shares assumed unchanged)" if repriced else
                f"as reported {snap_ts:%d %b} — from the weekly fundamentals snapshot")
        fcf = annual.get("fcf", (None,))[0]
        debt = annual.get("total_debt", (None,))[0]
        equity = annual.get("shareholder_equity", (None,))[0]
        out += [f"<b>Valuation</b>  <i>{when}</i>", kv([
            ("P/E", None if f.get("pe") is None else num(f["pe"], 1)),
            ("P/B", None if f.get("pb") is None else num(f["pb"], 1)),
            ("Market cap", None if f.get("market_cap") is None else big(f["market_cap"])),
            ("FCF yield", share(fcf / f["market_cap"], 1)
             if fcf is not None and f.get("market_cap") else None),
            (f"EV/EBITDA ({snap_ts:%d %b})",
             None if f.get("ev_ebitda") is None else num(f["ev_ebitda"], 1)),
            ("ROE", None if f.get("roe") is None else share(f["roe"])),
            ("Operating margin", None if f.get("operating_margin") is None
             else share(f["operating_margin"])),
            ("Debt / equity", f"{debt / equity:.2f}"
             if debt is not None and equity and equity > 0 else None)])]
        # Deterministic fair-value panel (ADR 0026/0030) — labelled, never advice.
        try:
            fv = q("SELECT fair_value, upside_pct, methods, graham, peer_multiple, "
                   "fcf_yield, dcf, ddm, epv, residual_income, peg_lynch, "
                   "damodaran_fcff, graham_growth, discount_rate, growth_used "
                   "FROM v_latest_valuations WHERE symbol = %s", (symbol,))
        except Exception as exc:  # noqa: BLE001 - only the missing-table case is expected
            if not table_missing(exc):
                raise
            fv = []
        if fv and fv[0][0] is not None:
            fair, up, nm = fv[0][:3]
            out += [f"Fair value <b>{num(fair)}</b> ({up:+.0f}% vs close, "
                    f"median of {nm} methods — <i>ADR 0026, not advice</i>)"]
        gv = grouped_valuations(fv[0] if fv else None, f, annual, close, universe)
        if gv["practitioner_groups"] or gv["academic_groups"]:
            p_med, a_med = gv["practitioner_median"], gv["academic_median"]
            p_up = f" ({(p_med / close - 1) * 100:+.0f}%)" if (p_med and close) else ""
            a_up = f" ({(a_med / close - 1) * 100:+.0f}%)" if (a_med and close) else ""
            if p_med and a_med:
                out += [f"<b>Practitioner vs Research-Backed valuations:</b> "
                        f"Practitioner median <b>{num(p_med)}</b>{p_up} ({gv['practitioner_count']}m) · "
                        f"Research-Backed median <b>{num(a_med)}</b>{a_up} ({gv['academic_count']}m)"]
            for grp_name, items in gv["practitioner_groups"]:
                joined = " · ".join(f"{lbl} <b>{num(val)}</b>" for lbl, val in items)
                out += [f"• <i>[Practitioner — {esc(grp_name)}]</i> {joined}"]
            for grp_name, items in gv["academic_groups"]:
                joined = " · ".join(f"{lbl} <b>{num(val)}</b>" for lbl, val in items)
                out += [f"• <i>[Research-Backed — {esc(grp_name)}]</i> {joined}"]
    if annual:
        year = max(v[2] for v in annual.values())
        block = kv([("Revenue", yoy(annual, "total_revenue")), ("EPS", yoy(annual, "eps")),
                    ("Net income", yoy(annual, "net_income"))])
        if block:
            out += [f"<b>Latest fiscal year</b>  <i>to {year:%b %Y}, vs the year before</i>",
                    block]
        sci = scientific_diagnostics(annual)
        if sci:
            f_str = (f"{sci['f_score'][0]}/{sci['f_score'][1]} {sci['f_score'][2]}"
                     if "f_score" in sci else None)
            z_str = (f"{sci['altman_z2'][0]:.2f} {sci['altman_z2'][1]}"
                     if "altman_z2" in sci else None)
            sci_block = kv([
                ("Gross prof / assets", share(sci["gpa"], 1) if "gpa" in sci else None),
                ("Accruals (Sloan)", pct(sci["accruals"], 1) if "accruals" in sci else None),
                ("Asset growth (CMA)", pct(sci["asset_growth"], 1) if "asset_growth" in sci else None),
                ("Share issuance", pct(sci["share_issuance"], 1) if "share_issuance" in sci else None),
                ("Piotroski F-score", f_str),
                ("Altman Z''-score", z_str),
            ])
            if sci_block:
                out += [
                    "<b>Scientific diagnostics</b>  <i>Novy-Marx · Sloan · Fama-French · Piotroski</i>",
                    sci_block,
                ]

    news = news_rows(q, symbol, days=14, limit=3)
    if news:
        out += ["<b>News</b>  <i>last 14 days</i>"] + [news_line(n) for n in news]
    ai_note = symbol_narrative(q, symbol)
    if ai_note:
        out += ["<b>AI note</b>  <i>(fact-checked)</i>", esc(ai_note)]
    return "\n".join(line for i, line in enumerate(out) if line or i == 2)


# ---------------------------------------------------------------- /news

def symbol_narrative(q, symbol):
    """Latest fact-checked AI summary for `symbol` from llm_narratives (ML-7b per_symbol
    or ML-7c event_brief, status='ok'). Gated on BOT_AI_RESOLVE=1 or STOCK_NARRATIVE=1."""
    if os.environ.get("BOT_AI_RESOLVE") != "1" and os.environ.get("STOCK_NARRATIVE") != "1":
        return None
    try:
        rows = q(
            "SELECT kind, summary FROM llm_narratives "
            "WHERE status = 'ok' AND ts >= current_date - 14 "
            "AND ((kind = 'per_symbol' AND symbol = %s) "
            "  OR (kind = 'event_brief' AND summary LIKE %s)) "
            "ORDER BY ts DESC, (kind = 'event_brief') DESC LIMIT 1",
            (symbol, f"[{symbol}:%"),
        )
    except Exception as exc:  # noqa: BLE001
        if not table_missing(exc):
            return None
        return None
    if not rows or not rows[0][1]:
        return None
    text = re.sub(r"^\[[^\]]+\]\s*", "", rows[0][1]).strip()
    return text or None


def news_rows(q, symbol, days=14, limit=5):
    """[(ts, headline, source, sentiment or None, 'event, event' or None)]"""
    return q(
        "SELECT ts, headline, source, sentiment, events FROM ("
        "  SELECT DISTINCT ON (h.id) h.ts, h.headline, h.source, "
        "    (SELECT n.sentiment_score FROM news_sentiment n "
        "      WHERE n.headline = h.headline ORDER BY n.ts DESC LIMIT 1) AS sentiment, "
        "    (SELECT string_agg(DISTINCT e.event_type, ', ') FROM headline_events e "
        "      WHERE e.headline_id = h.id) AS events "
        "  FROM headline_entities en INNER JOIN raw_headlines h ON h.id = en.headline_id "
        "  WHERE en.symbol = %s AND en.ts >= now() - %s * interval '1 day' "
        "  ORDER BY h.id) x ORDER BY ts DESC LIMIT %s",
        (symbol, days, limit),
    )


def news_line(row):
    ts, headline, _source, sentiment, events = row
    meta = [f"{ts:%d %b}"]
    if events:
        meta.append(esc(events))
    if sentiment is not None:
        meta.append(f"sentiment {sentiment:+.2f}")
    return f"• {esc(headline)}\n  <i>{' · '.join(meta)}</i>"


def news_text(q, row, days=14):
    symbol, name = row[0], row[1]
    rows = news_rows(q, symbol, days=days, limit=8)
    out = [f"<b>{esc(name)}</b>", f"{esc(symbol)} · headlines, last {days} days"]
    if not rows:
        return "\n".join(out + ["No tagged headlines."])
    scored = [r[3] for r in rows if r[3] is not None]
    if scored:
        out.append(f"Average sentiment <b>{sum(scored) / len(scored):+.2f}</b> "
                   f"over {len(scored)} headline(s)")
    ai_note = symbol_narrative(q, symbol)
    if ai_note:
        out.append(f"AI brief: <i>{esc(ai_note)}</i>")
    return "\n".join(out + [""] + [news_line(r) for r in rows])


# ---------------------------------------------------------------- watchlist

WATCH_USAGE = (
    "<b>/watch</b> — list\n"
    "<code>/watch add TCS</code> — watch only\n"
    "<code>/watch add TCS 10 3500 core holding</code> — qty, avg cost, note\n"
    "<code>/watch rm TCS</code> — remove"
)

WATCH_SQL = (
    "SELECT w.symbol, w.qty, w.avg_cost, w.note, u.name, u.currency, "
    "       p.ts, p.close, p.prev, s.rank "
    "FROM watchlist w "
    "LEFT JOIN LATERAL (SELECT name, currency FROM universe_constituents uc "
    "                   WHERE uc.symbol = w.symbol LIMIT 1) u ON true "
    "LEFT JOIN LATERAL (SELECT max(ts) AS ts, "
    "                          (array_agg(close ORDER BY ts DESC))[1] AS close, "
    "                          (array_agg(close ORDER BY ts DESC))[2] AS prev "
    "                   FROM (SELECT ts, close FROM stock_prices sp "
    "                         WHERE sp.symbol = w.symbol "
    "                           AND sp.ts >= now() - interval '15 days' "
    "                         ORDER BY ts DESC LIMIT 2) last2) p ON true "
    "LEFT JOIN LATERAL (SELECT rank FROM stock_scores ss WHERE ss.symbol = w.symbol "
    "                     AND ss.ts >= now() - interval '15 days' AND ss.eligible "
    "                   ORDER BY ts DESC LIMIT 1) s ON true "
    "WHERE w.chat_id = %s "      # per-user watchlist (ADR 0012): one parameter, the chat id
    "ORDER BY w.symbol"
)


def parse_watch(text):
    """'/watch …' → (action, symbol_arg, qty, cost, note, error).

    action is 'list', 'add' or 'rm'. qty and avg cost come as a pair of
    positive numbers or not at all; anything after them is the note.
    """
    parts = text.split()[1:]
    if not parts:
        return "list", None, None, None, None, None
    verb = parts[0].lower()
    if verb in ("rm", "remove", "del", "delete"):
        if len(parts) != 2:
            return "rm", None, None, None, None, "Usage: <code>/watch rm TCS</code>"
        return "rm", parts[1], None, None, None, None
    if verb != "add" or len(parts) < 2:
        return "list", None, None, None, None, WATCH_USAGE
    symbol, rest = parts[1], parts[2:]
    qty = cost = None
    if rest:
        try:
            qty, cost = float(rest[0]), float(rest[1])
        except (ValueError, IndexError):
            qty = cost = None
        else:
            if not (math.isfinite(qty) and math.isfinite(cost) and qty > 0 and cost > 0):
                return "add", symbol, None, None, None, "Quantity and cost must be positive."
            rest = rest[2:]
    note = " ".join(rest)[:200] or None
    return "add", symbol, qty, cost, note, None


def watch_block(rows, ytd=None, notes=True):
    """Watchlist table + per-currency totals for held positions (+ notes).

    rows: WATCH_SQL rows; ytd: optional {symbol: change} from ytd_map().
    Returns a list of message lines.
    """
    ytd = ytd or {}
    held = any(r[1] and r[2] for r in rows)
    head = ("", "Close", "Day", "YTD") + (("P&L",) if held else ())
    body, totals, noted = [], {}, []
    for symbol, qty, cost, note, name, currency, _ts, close, prev, _rank in rows:
        label = clip(bare(symbol), 10)
        if close is None:
            body.append((label, "n/a", "", "") + (("",) if held else ()))
            continue
        row = (label, px(close), pct(change(close, prev)),
               "" if ytd.get(symbol) is None else pct(ytd[symbol], 0))
        if held:
            row += (pct(close / cost - 1, 0) if qty and cost else "",)
        body.append(row)
        if qty and cost:
            t = totals.setdefault(currency or "?", [0.0, 0.0, 0.0])
            t[0] += qty * close
            t[1] += qty * cost
            t[2] += qty * (close - prev) if prev else 0.0
        if note and notes:
            noted.append(f"<i>{esc(bare(symbol))}: {esc(note)}</i>")
    lines = [table(head, body, "lrrrr"[:len(head)])]
    for currency, (value, basis, day_pl) in sorted(totals.items()):
        lines.append(
            f"Holdings {esc(currency)}: <b>{num(value, 0)}</b> · "
            f"P&amp;L <b>{value - basis:+,.0f}</b> ({pct((value - basis) / basis)}) · "
            f"today {day_pl:+,.0f}")
    return lines + noted


def watch_text(q, chat_id=None):
    rows = q(WATCH_SQL, (chat_id,))
    if not rows:
        return "Watchlist is empty.\n" + WATCH_USAGE
    return "\n".join(["<b>Watchlist</b>"]
                     + watch_block(rows, ytd_map(q, [r[0] for r in rows])))


# ---------------------------------------------------------------- confidence

# Every recommendation carries a 0-100 confidence. It measures how complete
# and consistent the EVIDENCE behind the pick is — never the chance that the
# price rises (nothing in this pipeline can estimate that).
CONF_HIGH, CONF_MEDIUM = 70, 45
CONF_NOTE = ("<i>Conf = how complete and consistent the evidence is (0-100), "
             "not the chance the price rises.</i>")
# One explanation of the derived scores, shared by every surface that shows
# them (/top tables, stock cards) so the wording cannot drift.
SCREENER_NOTE = ("<i>Scores are percentile ranks within the universe "
                 "(1.00 = best); composite is 0-100. Screening output, "
                 "not advice — /track shows how picks have done.</i>")


def conf_label(score):
    return "high" if score >= CONF_HIGH else "medium" if score >= CONF_MEDIUM else "low"


def stock_archetype(factors):
    """Classify a stock's factor profile (value, quality, growth, momentum, risk) in [0,1]."""
    v, qu, g, mo, r = [(x if x is not None else 0.5) for x in factors]
    if qu >= 0.70 and g >= 0.65 and r >= 0.55:
        return "Quality Compounder"
    if g >= 0.70 and v >= 0.55 and qu >= 0.55:
        return "GARP"
    if v >= 0.75 and qu >= 0.50:
        return "Deep Value"
    if mo >= 0.75 and g >= 0.55:
        return "Momentum Leader"
    if g >= 0.75 and v < 0.35:
        return "High-Growth Speculative"
    if r >= 0.70 and qu >= 0.65:
        return "Defensive Quality"
    if v >= 0.70 and mo >= 0.60 and qu < 0.45:
        return "Turnaround / Cyclical"
    if v >= 0.65 and qu < 0.35 and mo < 0.40:
        return "Value Trap"
    if qu < 0.35 and r < 0.35:
        return "High-Risk / Stressed"
    if (v + qu + g + mo + r) / 5.0 < 0.38 and max(v, qu, g, mo, r) < 0.65:
        return "Lagging / Weak"
    return "Balanced"


def stock_drivers(factors, top_k=3):
    """Return top factor drivers (percentile >= 0.55) formatted for human display."""
    names = ("Value", "Quality", "Growth", "Momentum", "Low-Risk")
    scored = [(val, name) for name, val in zip(names, factors) if val is not None and val >= 0.55]
    scored.sort(reverse=True)
    if not scored:
        return "none ≥ 55th pct"
    return ", ".join(f"{name} ({val * 100:.0f}th)" for val, name in scored[:top_k])


def academic_composite(factors):
    """DeMiguel, Garlappi & Uppal (2009) 1/N equal-weighted composite (20% each, 0-100 scale)."""
    vals = [(x if x is not None else 0.5) for x in factors]
    return (sum(vals) / len(vals)) * 100.0 if vals else 50.0


def academic_archetype(factors):
    """Peer-reviewed factor-interaction classification (FF5 2015 / Asness QMJ 2019 / Piotroski 2000 / Mohanram 2005)."""
    v, qu, g, mo, r = [(x if x is not None else 0.5) for x in factors]
    if qu >= 0.70 and r >= 0.60 and mo >= 0.50:
        return "QMJ + BAB Compounder (Asness 2019 / Frazzini 2014)"
    if v >= 0.70 and qu >= 0.60:
        return "Piotroski-Filtered Value (Piotroski 2000 / Novy-Marx 2013)"
    if v >= 0.65 and mo >= 0.65:
        return "Value × Momentum Crossover (Asness et al. 2013)"
    if g >= 0.70 and qu >= 0.60:
        return "Mohanram G-Score Growth (Mohanram 2005)"
    if mo >= 0.75 and r >= 0.45:
        return "Jegadeesh-Titman Momentum (Jegadeesh & Titman 1993)"
    if r >= 0.75 and qu >= 0.55 and v >= 0.45:
        return "Conservative Low-Vol (van Vliet 2018 / Ang 2006)"
    if g >= 0.65 and v < 0.35 and qu < 0.50:
        return "Unprofitable Glamour Risk (Lakonishok 1994)"
    if (v >= 0.65 and qu < 0.35 and mo < 0.40) or (qu < 0.35 and r < 0.35):
        return "Distress / Value-Trap Puzzle (Campbell et al. 2008)"
    if (v + qu + g + mo + r) / 5.0 < 0.38 and max(v, qu, g, mo, r) < 0.65:
        return "Bottom-Quintile Factor Lag"
    return "1/N Factor-Neutral (DeMiguel 2009)"


def archetype_comparison(factors, prac_comp=None):
    """Explain the difference between the Practitioner and Academic readings for `factors`."""
    v, qu, g, mo, r = [(x if x is not None else 0.5) for x in factors]
    prac = stock_archetype(factors)
    acad = academic_archetype(factors)
    eq_comp = academic_composite(factors)
    w_comp = prac_comp if prac_comp is not None else (0.25 * v + 0.25 * qu + 0.20 * g + 0.20 * mo + 0.10 * r) * 100.0
    gap = eq_comp - w_comp
    if g >= 0.65 and qu < 0.50:
        return ("Practitioner rewards headline growth (20% weight); Academic discounts "
                "unconfirmed growth without high Profitability (RMW, Fama-French 2015)")
    if v >= 0.70 and qu >= 0.60:
        return ("Both lenses agree: cheap multiples are backed by high Profitability "
                "(Novy-Marx 2013), separating true value from value traps")
    if r >= 0.70 and gap >= 1.5:
        return (f"Academic 1/N weights Low-Vol 20% vs Practitioner's 10% (+{gap:.1f} pts; "
                "Ang et al. 2006 / Betting Against Beta)")
    if qu >= 0.70 and r >= 0.60:
        return ("Confirmed by both: Practitioner sees growth/quality; Academic attributes "
                "edge to Quality Minus Junk × Low-Vol (Asness 2019)")
    return (f"Practitioner ({prac}) weights Value/Quality 25% each and Risk 10%; "
            f"Academic ({acad.split(' (')[0]}) equal-weights all 5 pillars at 20% (1/N Δ {gap:+.1f} pts)")


def screener_confidence(coverage, factors, ranks, universe_size):
    """Confidence in a screener pick.

    40 — data coverage: share of the factor inputs that were available.
    30 — agreement: how many of the five factor scores are above the median.
    30 — stability: how little the composite rank moved over the recent runs
         (a 20%-of-universe swing scores zero; one run only earns half).
    """
    known = [f for f in factors if f is not None]
    agree = sum(f >= 0.5 for f in known) / 5
    if len(ranks) >= 2 and universe_size:
        stable = 1 - scaled((max(ranks) - min(ranks)) / universe_size, 0.20)
    else:
        stable = 0.5
    return round(40 * scaled(coverage, 1.0) + 30 * agree + 30 * stable)


def rank_history(q, symbols):
    """{symbol: [rank, …]} over the screener runs of the last 10 days."""
    out = {}
    for symbol, rank in q(
            "SELECT symbol, rank FROM stock_scores WHERE symbol = ANY(%s) "
            "AND ts >= now() - interval '10 days' AND eligible AND rank IS NOT NULL",
            (sorted(set(symbols)),)):
        out.setdefault(symbol, []).append(rank)
    return out


def eligible_count(q, ts, universe):
    return q("SELECT count(*) FROM stock_scores WHERE ts = %s AND universe = %s "
             "AND eligible", (ts, universe))[0][0]


# ---------------------------------------------------------------- screener views

def full_runs(q, universe, limit=2):
    """Latest run timestamps that scored the whole universe (canaries skipped)."""
    return [r[0] for r in q(
        "SELECT ts FROM stock_scores WHERE universe = %s "
        "AND ts >= now() - interval '30 days' GROUP BY ts "
        "HAVING count(*) FILTER (WHERE eligible) >= %s ORDER BY ts DESC LIMIT %s",
        (universe, MIN_FULL_RUN, limit),
    )]


def top_text(q, factor, universe, n=10):
    """Recommendation list: best `n` eligible names by one factor score."""
    column = FACTORS[factor]          # whitelist — never user text in SQL
    runs = full_runs(q, universe, limit=1)
    if not runs:
        return f"No full screener run for {universe} in the last 30 days."
    rows = q(
        "SELECT s.symbol, COALESCE(u.name, s.symbol), s." + column + ", s.composite, s.rank, "
        "s.coverage, s.value_score, s.quality_score, s.growth_score, s.momentum_score, "
        "s.risk_score "
        "FROM stock_scores s LEFT JOIN universe_constituents u "
        "  ON u.symbol = s.symbol AND u.universe = s.universe "
        "WHERE s.ts = %s AND s.universe = %s AND s.eligible AND s." + column + " IS NOT NULL "
        "ORDER BY s." + column + " DESC, s.rank LIMIT %s",
        (runs[0], universe, n),
    )
    symbols = [r[0] for r in rows]
    ytd = ytd_map(q, symbols)
    history = rank_history(q, symbols)
    size = eligible_count(q, runs[0], universe)

    def year(sym):
        return "" if ytd.get(sym) is None else pct(ytd[sym], 0)

    def conf(r):
        return str(screener_confidence(r[5], r[6:11], history.get(r[0], []), size))

    if factor == "composite":
        tbl = table(("#", "", "Composite", "YTD", "Conf"),
                    [(r[4], clip(bare(r[0]), 11), num(r[3], 1), year(r[0]), conf(r))
                     for r in rows], "rlrrr")
    else:
        tbl = table(("", factor.capitalize(), "Rank", "YTD", "Conf"),
                    [(clip(bare(r[0]), 11), num(r[2]), r[4], year(r[0]), conf(r))
                     for r in rows], "lrrrr")
    names = "\n".join(
        f"{esc(bare(r[0]))} — {esc(r[1])} · <i>{esc(stock_archetype(r[6:11]))} "
        f"({esc(stock_drivers(r[6:11], top_k=2))})</i>"
        for r in rows[:5]
    )
    return "\n".join([
        f"<b>Top {len(rows)} by {factor} — {universe}</b>  <i>run of {runs[0]:%d %b}</i>",
        tbl, names, SCREENER_NOTE, CONF_NOTE])


def parse_top(text):
    """'/top growth sp500' → (factor, universe, error). Defaults: composite, NIFTY500."""
    factor, universe = "composite", "NIFTY500"
    for word in text.lower().split()[1:]:
        if word in FACTORS:
            factor = word
        elif word in UNIVERSES:
            universe = UNIVERSES[word]
        else:
            return None, None, ("Usage: <code>/top [" + "|".join(FACTORS) +
                                "] [nifty|sp500]</code>")
    return factor, universe, None


def diff_shortlist(latest, previous):
    """({symbol: (name, rank)} now, same before) → (entered, left), rank-sorted."""
    entered = sorted(((r, s, n) for s, (n, r) in latest.items() if s not in previous))
    left = sorted(((r, s, n) for s, (n, r) in previous.items() if s not in latest))
    return ([(s, n, r) for r, s, n in entered], [(s, n, r) for r, s, n in left])


def movers(q, universe):
    """(latest_ts, prev_ts, entered, left) for the top-TOP_N shortlist, or None."""
    runs = full_runs(q, universe, limit=2)
    if len(runs) < 2:
        return None
    rows = q(
        "SELECT s.ts, s.symbol, COALESCE(u.name, s.symbol), s.rank "
        "FROM stock_scores s LEFT JOIN universe_constituents u "
        "  ON u.symbol = s.symbol AND u.universe = s.universe "
        "WHERE s.universe = %s AND s.ts = ANY(%s) AND s.eligible AND s.rank <= %s",
        (universe, runs, TOP_N),
    )
    latest = {s: (n, r) for ts, s, n, r in rows if ts == runs[0]}
    previous = {s: (n, r) for ts, s, n, r in rows if ts == runs[1]}
    entered, left = diff_shortlist(latest, previous)
    return runs[0], runs[1], entered, left


def movers_lines(universe, result, ytd=None):
    latest, prev, entered, left = result
    if not entered and not left:
        return []
    ytd = ytd or {}
    lines = [f"<b>{universe} top {TOP_N}</b>  <i>{prev:%d %b} → {latest:%d %b}</i>"]
    lines += [f"▲ In at #{r}: <b>{esc(n)}</b> ({esc(bare(s))}){ytd_tag(ytd.get(s))}"
              for s, n, r in entered]
    lines += [f"▼ Out, was #{r}: {esc(n)} ({esc(bare(s))})" for s, n, r in left]
    return lines


def movers_text(q):
    out = []
    for universe in ("NIFTY500", "SP500"):
        result = movers(q, universe)
        if result is None:
            out.append(f"<b>{universe}</b>: fewer than two full runs yet.")
        else:
            out += movers_lines(universe, result,
                                ytd_map(q, [e[0] for e in result[2]])) or [
                f"<b>{universe} top {TOP_N}</b>: unchanged "
                f"<i>{result[1]:%d %b} → {result[0]:%d %b}</i>"]
    return "\n".join(out)


# ---------------------------------------------------------------- /multibagger

# Gates a company must pass before it is scored. They describe what past
# multibaggers usually looked like BEFORE the run — they do not predict one.
MB_MIN_GROWTH = 0.15      # revenue AND earnings compounding, per year
MB_MIN_ROE = 0.15
MB_MAX_DEBT_EQUITY = 1.0  # not applied to financials (leverage is the business)
MB_MAX_PEG = 2.0          # P/E divided by earnings growth in % per year
MB_SIZE_CUT = 0.70        # largest 30% of the universe by market cap are out
MB_MIN_YEARS = 1.9        # growth needs at least ~2 fiscal years between the ends
MB_ANNUAL = ["total_revenue", "eps", "net_income", "fcf", "total_debt", "shareholder_equity"]
MB_SNAPSHOT = ["pe", "roe", "market_cap", "operating_margin"]


def cagr(points):
    """Compound annual growth from [(ts, value), …] newest first, or None.

    Uses the newest and oldest year; both must be positive (growth from a loss
    is not a rate) and at least MB_MIN_YEARS apart. Rejects series where the
    most recent fiscal year contracted >15% vs the prior year (trough-base bias).
    """
    if len(points) < 2:
        return None
    (new_ts, new), (old_ts, old) = points[0], points[-1]
    years = (new_ts - old_ts).days / 365.25
    if years < MB_MIN_YEARS or new <= 0 or old <= 0:
        return None
    if len(points) >= 3 and points[1][1] is not None and points[1][1] > 0:
        if new < points[1][1] * 0.85:
            return None
    return (new / old) ** (1 / years) - 1


def scaled(value, full):
    """0..1 share of `full`, clamped — one component of the score."""
    return 0.0 if value is None else max(0.0, min(value / full, 1.0))


FINANCIAL_SECTORS = ("Financial Services", "Financials")


def multibagger_candidate(symbol, sector, annual, snap, metrics, size_pct):
    """Score one company, or return (None, reason) for the gate it failed.

    annual: series() output; snap: {metric: value}; metrics: latest
    stock_metrics_daily values; size_pct: market-cap percentile in the
    universe (0 = smallest). Score is 0-100:
      growth 35 · quality 25 · valuation 15 · momentum 15 · size 10.
    """
    rev = cagr(annual.get("total_revenue", []))
    earn = cagr(annual.get("eps", [])) or cagr(annual.get("net_income", []))
    if rev is None or earn is None:
        return None, "growth history"
    if rev < MB_MIN_GROWTH or earn < MB_MIN_GROWTH:
        return None, "growth"
    roe = snap.get("roe")
    equity = annual.get("shareholder_equity", [(None, None)])[0][1]
    income = annual.get("net_income", [(None, None)])[0][1]
    if roe is None and equity and equity > 0 and income is not None:
        roe = income / equity
    if roe is None or roe < MB_MIN_ROE:
        return None, "return on equity"
    debt = annual.get("total_debt", [(None, None)])[0][1]
    leverage = debt / equity if debt is not None and equity and equity > 0 else None
    if sector not in FINANCIAL_SECTORS and leverage is not None \
            and leverage > MB_MAX_DEBT_EQUITY:
        return None, "debt"
    if size_pct is None or size_pct > MB_SIZE_CUT:
        return None, "size"
    pe = snap.get("pe")
    peg = pe / (earn * 100) if pe and pe > 0 else None
    if peg is None or peg > MB_MAX_PEG:
        return None, "valuation"
    trend = metrics.get("sma200_ratio")
    if trend is None or trend < 1.0:
        return None, "trend"

    fcf = annual.get("fcf", [(None, None)])[0][1]
    score = (
        17.5 * scaled(rev, 0.40) + 17.5 * scaled(earn, 0.50)
        + 15 * scaled(roe, 0.35) + (10 if fcf is not None and fcf > 0 else 0)
        + 15 * (1 - scaled(peg - 0.5, MB_MAX_PEG - 0.5))
        + 10 * scaled(metrics.get("ret_12m"), 1.0) + 5 * scaled(metrics.get("pos_52w"), 1.0)
        + 10 * (1 - size_pct / MB_SIZE_CUT)
    )
    return {"symbol": symbol, "score": score, "rev": rev, "earn": earn, "roe": roe,
            "peg": peg, "cap": snap.get("market_cap"), "leverage": leverage,
            "conf": multibagger_confidence(annual, snap, metrics, rev, earn)}, None


def multibagger_confidence(annual, snap, metrics, rev, earn):
    """Confidence in a multibagger candidate (see the note above CONF_HIGH).

    30 — history: a full three fiscal years behind the growth rate (two = half).
    20 — earnings confirmed: EPS and net income both compound above the gate.
    15 — margin over the gates: how far the slower growth rate clears 15%.
    15 — cash: free cash flow is positive.
    10 — return on equity is as reported, not derived from the statements.
    10 — price sits in the upper half of its 52-week range.
    """
    revenue = annual.get("total_revenue", [])
    years = (revenue[0][0] - revenue[-1][0]).days / 365.25 if len(revenue) > 1 else 0
    both = [cagr(annual.get(k, [])) for k in ("eps", "net_income")]
    confirmed = sum(c is not None and c >= MB_MIN_GROWTH for c in both)
    fcf = annual.get("fcf", [(None, None)])[0][1]
    pos = metrics.get("pos_52w")
    return round(
        30 * (1.0 if years >= 2.9 else 0.5)
        + 10 * confirmed
        + 15 * scaled(min(rev, earn) - MB_MIN_GROWTH, MB_MIN_GROWTH)
        + (15 if fcf is not None and fcf > 0 else 0)
        + (10 if snap.get("roe") is not None else 0)
        + (10 if pos is not None and pos >= 0.5 else 0))


def multibagger_rank(universe_rows, fundamentals, metric_rows):
    """(candidates best-first, {gate: failures}) for a whole universe.

    universe_rows: (symbol, name, sector); fundamentals: (symbol, metric,
    source, ts, value); metric_rows: (symbol, metric, value) latest per symbol.
    """
    per, snap, metrics = {}, {}, {}
    for symbol, metric, source, ts, value in fundamentals:
        if metric in MB_SNAPSHOT:
            if value is not None and (metric not in snap.get(symbol, {})
                                      or ts > snap[symbol][metric][0]):
                snap.setdefault(symbol, {})[metric] = (ts, value)
        if metric in MB_ANNUAL:
            per.setdefault(symbol, []).append((metric, source, ts, value))
    for symbol, metric, value in metric_rows:
        metrics.setdefault(symbol, {})[metric] = value
    snap = {sym: {k: v for k, (_, v) in vals.items()} for sym, vals in snap.items()}

    caps = sorted(v["market_cap"] for v in snap.values() if v.get("market_cap"))
    out, failed = [], {}
    for symbol, _name, sector in universe_rows:
        cap = snap.get(symbol, {}).get("market_cap")
        size_pct = (sum(c < cap for c in caps) / len(caps)) if cap and caps else None
        got, reason = multibagger_candidate(
            symbol, sector, series(per.get(symbol, [])), snap.get(symbol, {}),
            metrics.get(symbol, {}), size_pct)
        if got:
            out.append(got)
        else:
            failed[reason] = failed.get(reason, 0) + 1
    return sorted(out, key=lambda c: -c["score"]), failed


def multibagger_academic_candidate(symbol, sector, annual, snap, metrics, size_pct):
    """Academic SQM (Size × Quality × 12-1 Momentum) compounder screen.

    Scientific backing vs Practitioner (Lynch PEG-Growth):
    1. Size control (size_pct <= 0.70): Asness, Frazzini, Israel, Moskowitz & Pedersen (2018)
       show the size premium is strong ONLY when conditioned on high quality ("Size Matters,
       If You Control Your Junk").
    2. Profitability & Accrual Quality (ROE >= 15% AND hard FCF > 0): Novy-Marx (2013),
       Fama & French (2015 RMW), and Sloan (1996). Unlike the Practitioner screen (where FCF > 0
       is optional), negative-FCF accrual stories are strictly rejected.
    3. Safety (D/E <= 1.0, Financials exempt): Asness, Frazzini & Pedersen (2019 QMJ).
    4. 12-1 Momentum & 52w High Proximity (ret_12_1 > 0, pos_52w >= 0.50, sma200_ratio >= 1.0):
       Jegadeesh & Titman (1993) and George & Hwang (2004). Skips the reversing 1-month return.
    5. Cash-Flow / Earnings Yield (FCF yield >= 1.5% or 0 < P/E <= 40): Loughran & Wellman (2011)
       and Easton (2004). Replaces backward-looking PEG <= 2.0 and 3y CAGR extrapolation, which
       suffer from mean-reversion (Chan, Karceski & Lakonishok 2003).
    6. Equal 25/25/25/25 pillar weights (DeMiguel, Garlappi & Uppal 2009 1/N rule).
    """
    if size_pct is None or size_pct > MB_SIZE_CUT:
        return None, "size (SMB)"
    roe = snap.get("roe")
    equity = annual.get("shareholder_equity", [(None, None)])[0][1]
    income = annual.get("net_income", [(None, None)])[0][1]
    if roe is None and equity and equity > 0 and income is not None:
        roe = income / equity
    op_margin = snap.get("operating_margin")
    if (roe is None or roe < MB_MIN_ROE) and (op_margin is None or op_margin < 0.12):
        return None, "profitability (RMW)"
    fcf = annual.get("fcf", [(None, None)])[0][1]
    if fcf is None or fcf <= 0:
        return None, "cash flow / accruals (Sloan)"
    debt = annual.get("total_debt", [(None, None)])[0][1]
    leverage = debt / equity if debt is not None and equity and equity > 0 else None
    if sector not in FINANCIAL_SECTORS and leverage is not None and leverage > MB_MAX_DEBT_EQUITY:
        return None, "leverage (QMJ)"
    ret_12m = metrics.get("ret_12m")
    ret_1m = metrics.get("ret_1m")
    ret_12_1 = metrics.get("ret_12_1")
    if ret_12_1 is None and ret_12m is not None:
        ret_12_1 = ((1.0 + ret_12m) / (1.0 + ret_1m) - 1.0) if (ret_1m is not None and ret_1m > -0.95) else ret_12m
    pos_52w = metrics.get("pos_52w")
    trend = metrics.get("sma200_ratio")
    if trend is None or trend < 1.0 or ret_12_1 is None or ret_12_1 <= 0 or pos_52w is None or pos_52w < 0.50:
        return None, "12-1 momentum / 52w high"
    cap = snap.get("market_cap")
    fcf_yield = (fcf / cap) if (cap and cap > 0) else None
    pe = snap.get("pe")
    earn_yield = (1.0 / pe) if (pe and pe > 0) else None
    if (fcf_yield is None or fcf_yield < 0.015) and (pe is None or pe <= 0 or pe > 40.0):
        return None, "cash/earnings yield"

    # DeMiguel et al. (2009) 1/N equal 25-point weights across the 4 academic pillars
    p_prof = 15.0 * scaled(roe or op_margin, 0.30) + 10.0 * scaled(fcf_yield, 0.06)
    p_mom = 15.0 * scaled(ret_12_1, 0.60) + 10.0 * scaled(pos_52w, 1.0)
    p_val = 15.0 * scaled(fcf_yield, 0.08) + 10.0 * scaled(earn_yield, 0.08)
    p_size_safe = 15.0 * (1.0 - size_pct / MB_SIZE_CUT) + 10.0 * (
        1.0 if leverage is None else max(0.0, 1.0 - leverage / MB_MAX_DEBT_EQUITY)
    )
    score = p_prof + p_mom + p_val + p_size_safe
    return {
        "symbol": symbol,
        "score": score,
        "roe": roe or op_margin or 0.0,
        "fcf_yield": fcf_yield,
        "ret_12_1": ret_12_1,
        "pos_52w": pos_52w,
        "cap": cap,
        "leverage": leverage,
    }, None


def multibagger_academic_rank(universe_rows, fundamentals, metric_rows):
    """(academic_candidates best-first, {gate: failures}) for a whole universe."""
    per, snap, metrics = {}, {}, {}
    for symbol, metric, source, ts, value in fundamentals:
        if metric in MB_SNAPSHOT:
            if value is not None and (metric not in snap.get(symbol, {})
                                      or ts > snap[symbol][metric][0]):
                snap.setdefault(symbol, {})[metric] = (ts, value)
        if metric in MB_ANNUAL:
            per.setdefault(symbol, []).append((metric, source, ts, value))
    for symbol, metric, value in metric_rows:
        metrics.setdefault(symbol, {})[metric] = value
    snap = {sym: {k: v for k, (_, v) in vals.items()} for sym, vals in snap.items()}

    caps = sorted(v["market_cap"] for v in snap.values() if v.get("market_cap"))
    out, failed = [], {}
    for symbol, _name, sector in universe_rows:
        cap = snap.get(symbol, {}).get("market_cap")
        size_pct = (sum(c < cap for c in caps) / len(caps)) if cap and caps else None
        got, reason = multibagger_academic_candidate(
            symbol, sector, series(per.get(symbol, [])), snap.get(symbol, {}),
            metrics.get(symbol, {}), size_pct)
        if got:
            out.append(got)
        else:
            failed[reason] = failed.get(reason, 0) + 1
    return sorted(out, key=lambda c: -c["score"]), failed


def multibagger_text(q, universe, n=10):
    names = q("SELECT symbol, name, sector FROM universe_constituents "
              "WHERE universe = %s AND active", (universe,))
    if not names:
        return f"No constituents for {universe}."
    fundamentals = q(
        "SELECT f.symbol, f.metric, f.source, f.ts, f.value FROM stock_fundamentals f "
        "INNER JOIN universe_constituents u ON u.symbol = f.symbol AND u.universe = %s "
        "WHERE f.metric = ANY(%s)", (universe, MB_ANNUAL + MB_SNAPSHOT))
    metric_rows = q(
        "SELECT DISTINCT ON (m.symbol, m.metric) m.symbol, m.metric, m.value "
        "FROM stock_metrics_daily m "
        "INNER JOIN universe_constituents u ON u.symbol = m.symbol AND u.universe = %s "
        "WHERE m.ts >= now() - interval '10 days' AND m.metric = ANY(%s) "
        "ORDER BY m.symbol, m.metric, m.ts DESC",
        (universe, ["sma200_ratio", "ret_12m", "ret_1m", "ret_12_1", "pos_52w"]))
    ranked, failed = multibagger_rank(names, fundamentals, metric_rows)
    acad_ranked, acad_failed = multibagger_academic_rank(names, fundamentals, metric_rows)
    head = f"<b>Multibagger candidates — {universe}</b>"
    if not ranked and not acad_ranked:
        return head + "\nNo company passes every gate right now."
    label = dict((s, nm) for s, nm, _ in names)
    prac_syms = {c["symbol"] for c in ranked}
    acad_syms = {c["symbol"] for c in acad_ranked}
    both_syms = prac_syms & acad_syms

    out = [head]
    if ranked:
        top = ranked[:n]
        body = [(clip(bare(c["symbol"]), 9), f"{c['score']:.0f}", pct(c["rev"], 0),
                 pct(c["earn"], 0), share(c["roe"]), str(c["conf"])) for c in top]
        detail = "\n".join(
            f"{esc(bare(c['symbol']))} — {esc(label.get(c['symbol'], ''))} · "
            f"cap {big(c['cap'])} · PEG {c['peg']:.1f} · confidence {conf_label(c['conf'])}"
            for c in top[:5])
        order = ["growth history", "growth", "return on equity", "debt", "size",
                 "valuation", "trend"]
        funnel = ", ".join(f"{failed[k]} on {k}" for k in order if failed.get(k))
        out += [
            f"<b>1. Practitioner Lens (Lynch / PEG-Growth)</b> — {len(ranked)} of {len(names)} companies pass every gate",
            table(("", "Score", "Rev", "EPS", "ROE", "Conf"),
                  body, "lrrrrr"),
            detail,
            f"<i>Gates: revenue and earnings each compounding {share(MB_MIN_GROWTH)}+ a year "
            f"over the fiscal years on file (2-3), ROE {share(MB_MIN_ROE)}+, debt/equity under "
            f"{MB_MAX_DEBT_EQUITY:.0f} (financials exempt), outside the largest "
            f"{share(1 - MB_SIZE_CUT)} by market cap, PEG under {MB_MAX_PEG:.0f}, price above "
            f"its 200-day average. Dropped: {funnel}.</i>",
            f"<i>Rev/EPS = revenue/earnings CAGR over fiscal years on file; ROE = return on equity. "
            f"Score = growth 35 + quality 25 + valuation 15 + momentum 15 + size 10 (0-100). "
            f"Conf = history 30 + earnings confirmed 20 + margin above gate 15 + cash flow 15 + "
            f"ROE as reported 10 + price in upper 52-w half 10.</i>",
        ]
    else:
        out.append("<b>1. Practitioner Lens (Lynch / PEG-Growth)</b>: No company passes every gate right now.")

    if acad_ranked:
        atop = acad_ranked[:5]
        adetail = "\n".join(
            f"• <b>{esc(bare(c['symbol']))}</b> ({esc(label.get(c['symbol'], ''))}) — "
            f"SQM score <b>{c['score']:.0f}</b> · ROE {share(c['roe'])} · "
            f"FCF yield {share(c['fcf_yield'], 1) if c.get('fcf_yield') else 'n/a'} · "
            f"12-1 mom {pct(c['ret_12_1'], 0)}"
            + (" · <b>[Both Lenses]</b>" if c["symbol"] in both_syms else " · <i>[Academic-only]</i>")
            for c in atop
        )
        a_order = ["size (SMB)", "profitability (RMW)", "cash flow / accruals (Sloan)",
                   "leverage (QMJ)", "12-1 momentum / 52w high", "cash/earnings yield"]
        a_funnel = ", ".join(f"{acad_failed[k]} on {k}" for k in a_order if acad_failed.get(k))
        consensus_line = (
            f"<b>Consensus (pass both lenses):</b> {', '.join(esc(bare(s)) for s in sorted(both_syms))}"
            if both_syms else "<b>Consensus:</b> none (the two lenses select distinct profiles right now)"
        )
        out += [
            "",
            f"<b>2. Academic Lens (SQM: Size × Quality × 12-1 Momentum)</b> — "
            f"{len(acad_ranked)} of {len(names)} pass",
            adetail,
            consensus_line,
            f"<i>How Academic differs from Practitioner: replaces multi-year CAGR extrapolation "
            f"(Chan et al. 2003 mean-reversion) and backward PEG ≤ 2 with hard FCF &gt; 0 "
            f"(Sloan 1996 accruals), Profitability RMW (Novy-Marx 2013), 12-1 momentum skipping "
            f"1m reversal (Jegadeesh &amp; Titman 1993), 52w-high proximity (George &amp; Hwang 2004), "
            f"and FCF/Earnings yield (Loughran &amp; Wellman 2011) in an equal 25/25/25/25 1/N composite "
            f"(Asness et al. 2018 'Size Matters, If You Control Your Junk'). Dropped: {a_funnel}.</i>",
        ]

    out += [
        "<i>This finds companies with the profile multibaggers usually had before the "
        "run. Most such companies still do not become one. Not advice.</i>",
        CONF_NOTE,
    ]
    return "\n".join(out)


# The run list and start bound arrive as parameters (chunk-pruning note in the
# module docstring).
TRACK_SQL = """
WITH px AS (
  SELECT symbol, ts, close,
         row_number() OVER (PARTITION BY symbol ORDER BY ts) AS rn
  FROM stock_prices
  WHERE ts >= %(start)s::timestamptz
    AND symbol IN (SELECT symbol FROM universe_constituents WHERE universe = %(u)s)),
entry AS (
  SELECT s.ts AS run_ts, s.symbol, p.rn, p.close,
         (s.eligible AND s.rank <= %(n)s) AS pick
  FROM stock_scores s
  INNER JOIN px p ON p.symbol = s.symbol AND p.ts = s.ts
  WHERE s.universe = %(u)s AND s.ts = ANY(%(runs)s) AND p.close > 0),
fwd AS (
  SELECT e.run_ts, h.n, e.pick, x.close / e.close - 1 AS ret
  FROM entry e
  CROSS JOIN unnest(%(h)s::int[]) AS h(n)
  INNER JOIN px x ON x.symbol = e.symbol AND x.rn = e.rn + h.n)
SELECT n, run_ts,
       avg(ret) FILTER (WHERE pick) AS picks_ret,
       avg(ret) AS universe_ret,
       count(*) FILTER (WHERE pick) AS picks,
       count(*) FILTER (WHERE pick AND ret > 0) AS winners
FROM fwd GROUP BY n, run_ts ORDER BY n, run_ts
"""


def track_rows(q, universe):
    runs = [r[0] for r in q(
        "SELECT ts FROM stock_scores WHERE universe = %s "
        "AND ts >= now() - interval '400 days' GROUP BY ts "
        "HAVING count(*) FILTER (WHERE eligible) >= %s ORDER BY ts",
        (universe, MIN_FULL_RUN))]
    if not runs:
        return []
    return q(TRACK_SQL, {"u": universe, "n": TOP_N, "h": list(HORIZONS),
                         "runs": runs, "start": runs[0]})


def summarise_track(rows):
    """Per horizon: runs, mean pick return, mean universe return, share of runs
    where the picks beat the universe, share of individual picks that rose."""
    out = {}
    for n, _run, picks_ret, univ_ret, picks, winners in rows:
        if picks_ret is None or not picks:
            continue
        h = out.setdefault(n, {"runs": 0, "picks": 0.0, "univ": 0.0, "beat": 0,
                               "n_picks": 0, "winners": 0})
        h["runs"] += 1
        h["picks"] += picks_ret
        h["univ"] += univ_ret
        h["beat"] += picks_ret > univ_ret
        h["n_picks"] += picks
        h["winners"] += winners
    for h in out.values():
        h["picks"] /= h["runs"]
        h["univ"] /= h["runs"]
    return out


def track_text(q):
    labels = {1: "1 day", 5: "1 week", 21: "1 month", 63: "3 months"}
    out = ["<b>Screener track record</b>",
           f"Top-{TOP_N} picks against the whole universe, equal-weighted, close to close."]
    for universe in ("NIFTY500", "SP500"):
        stats = summarise_track(track_rows(q, universe))
        body = []
        for n in HORIZONS:
            h = stats.get(n)
            body.append((labels[n], "–", "–", "–", "–") if not h else (
                labels[n], pct(h["picks"], 2), pct(h["univ"], 2),
                pct(h["picks"] - h["univ"], 2), f"{h['beat']}/{h['runs']}"))
        out += [f"<b>{universe}</b>",
                table(("After", "Picks", "Univ", "Edge", "Won"), body, "lrrrr")]
    out.append("<i>Picks/Univ = average return of the picks / of the whole universe over "
               "that horizon; Edge = Picks − Univ; Won = runs where the picks beat the "
               "universe; – = that horizon has not completed yet. Daily runs overlap, so "
               "longer horizons are not independent; under ~60 runs this is anecdote.</i>")
    return "\n".join(out)


# ---------------------------------------------------------------- /macro

def gold_inr_10g(usd_per_oz, usdinr):
    """International gold parity in rupees per 10 g (no duty/GST/premium)."""
    return usd_per_oz * usdinr / OZ_GRAMS * 10


def spread_bp(yields, a, b):
    if a not in yields or b not in yields:
        return None
    return (yields[a] - yields[b]) * 100


def macro_text(q):
    out = ["<b>Derived macro</b>"]

    gold = q("SELECT ts, close FROM index_prices WHERE ticker = 'GC=F' "
             "AND ts >= now() - interval '15 days' ORDER BY ts DESC LIMIT 2", ())
    vals = []
    for ts, close in gold:
        fx = q("SELECT rate FROM fx_rates WHERE pair = 'USDINR=X' "
               "AND ts < %s::timestamptz + interval '1 day' "
               "AND ts >= %s::timestamptz - interval '7 days' "
               "ORDER BY ts DESC LIMIT 1", (ts, ts))
        if fx:
            vals.append((ts, gold_inr_10g(close, fx[0][0])))
    if vals:
        day = f"  {pct(vals[0][1] / vals[1][1] - 1, 2)}" if len(vals) > 1 else ""
        out += [f"<b>Gold in rupees</b>  <i>{vals[0][0]:%d %b}</i>",
                f"<b>{num(vals[0][1], 0)}</b> per 10 g{day}",
                "<i>International parity — before duty, GST and premium.</i>"]

    y = dict((i, v) for i, _, v in q(
        "SELECT DISTINCT ON (instrument) instrument, ts, yield FROM bond_yields "
        "WHERE ts >= now() - interval '15 days' ORDER BY instrument, ts DESC", ()))
    spreads = kv([(label, None if spread_bp(y, a, b) is None
                   else f"{spread_bp(y, a, b):+.0f} bp")
                  for label, a, b in (("US 10Y - 3M", "^TNX", "^IRX"),
                                      ("US 30Y - 5Y", "^TYX", "^FVX"),
                                      ("India 10Y - 3Y", "IN_GS_10Y", "IN_GS_3Y"),
                                      ("India 10Y - 91d bill", "IN_GS_10Y", "IN_TBILL_91D"),
                                      ("India 10Y - US 10Y", "IN_GS_10Y", "^TNX"))])
    if spreads:
        out += ["<b>Yield spreads</b>", spreads]

    flow = q(
        "WITH f AS (SELECT ts::date AS d, sum(amount) AS fii FROM capital_flows "
        "           WHERE flow_type = 'FII' AND direction = 'net' "
        "             AND ts >= now() - interval '90 days' GROUP BY 1), "
        "n AS (SELECT ts::date AS d, close / lag(close) OVER (ORDER BY ts) - 1 AS ret "
        "      FROM index_prices WHERE ticker = '^NSEI' "
        "        AND ts >= now() - interval '100 days'), "
        "j AS (SELECT f.d, f.fii, n.ret FROM f INNER JOIN n ON n.d = f.d "
        "      WHERE n.ret IS NOT NULL ORDER BY f.d DESC LIMIT 20) "
        "SELECT count(*), corr(fii, ret), sum(fii), exp(sum(ln(1 + ret))) - 1 FROM j", ())
    if flow and flow[0][0] and flow[0][0] >= 5:
        sessions, corr, fii_sum, nifty_ret = flow[0]
        out += [f"<b>FII flows and the Nifty</b>  <i>last {sessions} sessions</i>", kv([
            ("FII net, Rs cr", f"{fii_sum:+,.0f}"),
            ("Nifty 50", pct(nifty_ret)),
            ("Daily correlation", None if corr is None else f"{corr:+.2f}")])]
    return "\n".join(out) if len(out) > 1 else "No macro inputs in the last 15 days."


# ---------------------------------------------------------------- alerts

def crosses(metric_rows, now=None):
    """Moving-average crossings from stock_metrics_daily rows.

    metric_rows: (symbol, metric, ts, value) newest first per (symbol, metric).
    Returns [(symbol, 'sma200_ratio', 'above'|'below', ts)] where the latest
    FRESH value sits on the other side of 1.0 from the one before it.
    """
    seen, out = {}, []
    for symbol, metric, ts, value in metric_rows:
        pair = seen.setdefault((symbol, metric), [])
        if len(pair) < 2 and value is not None:
            pair.append((ts, value))
    for (symbol, metric), pair in sorted(seen.items()):
        if len(pair) < 2 or not is_fresh(pair[0][0], now):
            continue
        (ts, cur), (_, prev) = pair
        if (cur - 1) * (prev - 1) < 0:
            out.append((symbol, metric, "above" if cur > 1 else "below", ts))
    return out


# ADR 0029 tier separation: movers/regime/digest tops stay on the flagships;
# the extended universes (SP400/SP600/NIFTYMICROCAP250) appear only explicitly
# (/top sp600, /screener pages) or tagged (valuation watch).
CORE_UNIVERSES = ("NIFTY500", "SP500")


def alert_common(q, now=None):
    """Shared change-alert lines (screener entries/exits + valuation watch) —
    identical for every user."""
    lines = []
    for universe in CORE_UNIVERSES:
        result = movers(q, universe)
        if result and is_fresh(result[0], now):
            lines += movers_lines(universe, result, ytd_map(q, [e[0] for e in result[2]]))
    lines += alert_valuation(q, now)
    return lines


# Valuation-watch gates (ADR 0026 amendment, owner-approved 2026-10-26).
VAL_MIN_UPSIDE = 25.0     # median-fair-value upside, percent
VAL_MIN_METHODS = 4       # methods that produced a number
VAL_MIN_ABOVE = 3         # methods INDIVIDUALLY above the close (agreement)
VAL_MAX_RANK = 50         # screener quality gate
VAL_MAX_LINES = 5         # per run, across both universes
VAL_EARNINGS_GAP_DAYS = 3  # suppress entries reporting within N days

# ADR 0032 contract: the agreement count (methods_above_close) and the 4-day
# bound live INSIDE v_valuations_recent — one definition for bot/UI/reports.
VAL_CAND_SQL = (
    "SELECT ts, symbol, universe, close, fair_value, upside_pct, methods, "
    "methods_above_close FROM v_valuations_recent "
    "WHERE upside_pct >= %s AND methods >= %s ORDER BY ts DESC")

VAL_CTX_SQL = (
    "SELECT s.symbol, s.rank, s.composite, c.name, c.sector, st.note "
    "FROM (SELECT DISTINCT ON (symbol) symbol, rank, composite, eligible "
    "      FROM stock_scores WHERE symbol = ANY(%s) "
    "      AND ts >= now() - interval '14 days' ORDER BY symbol, ts DESC) s "
    "JOIN universe_constituents c ON c.symbol = s.symbol "
    "LEFT JOIN sector_styles st ON st.sector = c.sector "
    "WHERE s.eligible AND s.rank IS NOT NULL AND s.rank <= %s")


def alert_valuation(q, now=None):
    """Valuation-watch lines: a name ENTERING the high-agreement value set —
    screener rank <= 50 AND fair-value upside >= 25% with >= 4 methods, >= 3 of
    them individually above the close (ADR 0026 amendment: deterministic data
    echo with the reasoning in the line; clearly labelled, never advice).
    Stateless like every alert here: compares today's valuation run with the
    previous one, so a name alerts once on entry, not daily."""
    try:
        rows = q(VAL_CAND_SQL, (VAL_MIN_UPSIDE, VAL_MIN_METHODS))
    except Exception as exc:  # noqa: BLE001 - only the missing-table case is expected
        if not table_missing(exc):
            raise
        return []
    if not rows:
        return []
    latest = max(r[0] for r in rows)
    if not is_fresh(latest, now):
        return []
    today = {r[1]: r for r in rows if r[0] == latest and r[7] >= VAL_MIN_ABOVE}
    prior = {r[1] for r in rows if r[0] != latest and r[7] >= VAL_MIN_ABOVE}
    entered = [s for s in today if s not in prior]
    if not entered:
        return []
    ctx = {r[0]: r for r in q(VAL_CTX_SQL, (entered, VAL_MAX_RANK))}
    # Earnings gate (2026-10-26): a watch line right before a report is the
    # most dangerous moment for it — suppress entries reporting within 3 days;
    # if the setup survives the print, it re-enters on the next run.
    reporting = earnings_soon(q, list(ctx), days=VAL_EARNINGS_GAP_DAYS)
    ctx = {s: r for s, r in ctx.items() if s not in reporting}
    lines = []
    for sym in sorted(ctx, key=lambda s: -today[s][5])[:VAL_MAX_LINES]:
        _ts, _s, uni, close, fair, up, nm, above = today[sym]
        _sym, rank, comp, name, sector, note = ctx[sym]
        # The rank is within ITS universe — tag it so an extended-tier #2
        # (ADR 0029) can't be read as a flagship rank.
        reason = (f"fair <b>{num(fair)}</b> vs close {num(close)} ({up:+.0f}%, "
                  f"{above}/{nm} methods above close); screener rank <b>#{rank}</b> "
                  f"in {esc(uni)}, composite {num(comp, 0)}")
        lens = f" {esc(sector)}: <i>{esc(note)}</i>." if note else ""
        lines.append(f"🟢 <b>{esc(name)}</b> ({esc(bare(sym))}) entered the "
                     f"valuation watch — {reason}.{lens} "
                     f"<i>ADR 0026 — not advice.</i>")
    return lines


def sectors_card(q, text=""):
    """/sectors [universe] — latest sector aggregates, cheapest first.
    Any UNIVERSES alias works (v28: 'sp' in text matched sp600 to SP500)."""
    words = (text or "").lower().split()[1:]
    universe = "NIFTY500"
    if words:
        if words[0] not in UNIVERSES:
            return (f"Unknown universe '{esc(words[0])}'. One of: "
                    f"{', '.join(sorted(set(UNIVERSES)))}")
        universe = UNIVERSES[words[0]]
    rows = q(
        "SELECT m.sector, m.members, m.median_pe, m.median_upside_pct, st.note "
        "FROM (SELECT DISTINCT ON (sector) * FROM sector_metrics "
        "      WHERE universe = %s AND ts >= now() - interval '7 days' "
        "      ORDER BY sector, ts DESC) m "
        "LEFT JOIN sector_styles st ON st.sector = m.sector "
        "ORDER BY m.median_upside_pct DESC NULLS LAST", (universe,))
    if not rows:
        return f"No sector data for {universe} yet."
    width = max(len(s) for s, *_ in rows)
    body = [f"{'Sector':<{width}}  {'n':>3}  {'P/E':>6}  {'Upside':>7}"]
    for sector, n, pe, up, _note in rows:
        body.append(f"{sector:<{width}}  {n:>3}  "
                    f"{num(pe, 1) if pe is not None else '–':>6}  "
                    f"{f'{up:+.0f}%' if up is not None else '–':>7}")
    return (f"<b>Sectors — {universe}</b>  <i>median fair-value gap, ADR 0026</i>\n"
            f"<pre>{esc(chr(10).join(body))}</pre>\n"
            f"<i>Practitioner vs Academic lens: table ranks by Practitioner intrinsic upside "
            f"(10-method DCF/Graham/Peer median); Academic sector research (Moskowitz &amp; Grinblatt "
            f"1999 Industry Momentum + Novy-Marx 2013 Profitability) requires positive 6m/12m trend "
            f"and high ROE to confirm cheap sectors are not value traps. "
            f"Lens per sector: /stock &lt;symbol&gt; shows what matters. Not advice.</i>")


def alert_sections(q, now=None, chat_id=None):
    """Lines for one user's daily change alert; empty list = nothing worth sending."""
    return alert_common(q, now) + alert_watch(q, chat_id, now)


def alert_watch(q, chat_id, now=None):
    """Personal lines: that user's watchlist moves, average crossings and tagged news."""
    lines = []
    try:
        watch = q(WATCH_SQL, (chat_id,))
    except Exception as exc:  # noqa: BLE001 - only the missing-table case is expected
        if not table_missing(exc):
            raise
        print("WARN watchlist table missing — watchlist alerts skipped", flush=True)
        watch = []
    symbols = [r[0] for r in watch]
    if not symbols:
        return lines

    mine = []
    for symbol, *_rest, ts, close, prev, _rank in watch:
        if close and prev and is_fresh(ts, now) and abs(close / prev - 1) >= BIG_MOVE:
            mine.append(f"<b>{esc(bare(symbol))}</b> moved {pct(close / prev - 1)} "
                        f"to {num(close)}  <i>{ts:%d %b}</i>")

    labels = {"sma50_ratio": "50-day", "sma200_ratio": "200-day"}
    for symbol, metric, side, ts in crosses(q(
            "SELECT symbol, metric, ts, value FROM stock_metrics_daily "
            "WHERE symbol = ANY(%s) AND metric = ANY(%s) "
            "AND ts >= now() - interval '15 days' ORDER BY symbol, metric, ts DESC",
            (symbols, list(labels))), now):
        mine.append(f"<b>{esc(bare(symbol))}</b> crossed {side} its {labels[metric]} "
                    f"average  <i>{ts:%d %b}</i>")

    events = q(
        "SELECT DISTINCT ON (h.id) e.symbol, e.event_type, h.headline, h.ts "
        "FROM headline_events e INNER JOIN raw_headlines h ON h.id = e.headline_id "
        "WHERE e.symbol = ANY(%s) AND e.ts >= now() - interval '24 hours' "
        "ORDER BY h.id, e.event_type", (symbols,))
    for symbol, event, headline, _ts in events:
        mine.append(f"<b>{esc(bare(symbol))}</b> · {esc(event)}\n  {esc(headline)}")

    for symbol, edate in sorted(earnings_soon(q, symbols, days=3).items(),
                                key=lambda kv: (kv[1], kv[0])):
        mine.append(f"<b>{esc(bare(symbol))}</b> reports earnings {edate:%a %d %b}")
    if mine:
        lines += ["<b>Your watchlist</b>"] + mine
    return lines


# ---- Earnings calendar (schema 19, 2026-10-26) ----

EARNINGS_SOON_SQL = (
    "SELECT symbol, min(event_date) FROM earnings_calendar "
    "WHERE symbol = ANY(%s) AND event_date BETWEEN CURRENT_DATE "
    "AND CURRENT_DATE + %s GROUP BY symbol")


def earnings_soon(q, symbols, days=7):
    """{symbol: next event_date} within `days`; {} when the table is missing."""
    if not symbols:
        return {}
    try:
        return dict(q(EARNINGS_SOON_SQL, (list(symbols), days)))
    except Exception as exc:  # noqa: BLE001 - only the missing-table case is expected
        if not table_missing(exc):
            raise
        return {}


def earnings_lines(q, symbols, days=7):
    """Digest block: upcoming earnings for the given names, soonest first."""
    soon = earnings_soon(q, symbols, days)
    return [f"• <b>{esc(bare(s))}</b> — {d:%a %d %b}"
            for s, d in sorted(soon.items(), key=lambda kv: (kv[1], kv[0]))]


# ---- Market regime (descriptive traffic light, 2026-10-26) ----
# Three deterministic legs per market; a leg with no fresh data drops out
# rather than guessing. Descriptive display only — never a score input (EV-0).

REGIME_MARKETS = (("US", "SP500", "^GSPC"), ("India", "NIFTY500", "^CRSLDX"))

REGIME_BREADTH_SQL = (
    "SELECT avg((m.value > 1)::int) FROM "
    "(SELECT DISTINCT ON (symbol) symbol, value FROM stock_metrics_daily "
    " WHERE metric = 'sma200_ratio' AND ts >= now() - interval '7 days' "
    " ORDER BY symbol, ts DESC) m "
    "JOIN universe_constituents c ON c.symbol = m.symbol "
    "WHERE c.universe = %s AND c.active")

REGIME_TREND_SQL = (
    "SELECT (SELECT close FROM index_prices WHERE ticker = %s "
    "        AND ts >= now() - interval '7 days' ORDER BY ts DESC LIMIT 1) "
    "     > (SELECT avg(close) FROM (SELECT close FROM index_prices "
    "        WHERE ticker = %s AND ts >= now() - interval '320 days' "
    "        ORDER BY ts DESC LIMIT 200) t)")

REGIME_MOM_SQL = (
    "SELECT avg(value) > 0 FROM (SELECT value FROM factor_returns "
    "WHERE market = %s AND factor = 'MOM' AND freq = 'M' "
    "AND ts >= now() - interval '200 days' ORDER BY ts DESC LIMIT 3) t")


def regime(q):
    """[(label, light, parts)] per market. Legs: breadth (share of the universe
    above its 200-day SMA), benchmark trend (close vs its own 200-bar mean),
    3-month factor momentum (Ken French / IIM-A; drops out when stale)."""
    out = []
    for label, universe, bench in REGIME_MARKETS:
        market = "US" if universe == "SP500" else "IN"
        legs, parts = [], []
        row = q(REGIME_BREADTH_SQL, (universe,))
        breadth = row[0][0] if row and row[0][0] is not None else None
        if breadth is not None:
            legs.append(breadth > 0.5)
            parts.append(f"breadth {breadth * 100:.0f}% above 200d")
        row = q(REGIME_TREND_SQL, (bench, bench))
        trend = row[0][0] if row else None
        if trend is not None:
            legs.append(bool(trend))
            parts.append(f"{bench} {'above' if trend else 'below'} 200d mean")
        row = q(REGIME_MOM_SQL, (market,))
        mom = row[0][0] if row else None
        if mom is not None:
            legs.append(bool(mom))
            parts.append(f"3m momentum {'positive' if mom else 'negative'}")
        greens = sum(legs)
        light = ("🟢" if legs and greens == len(legs) and len(legs) >= 2
                 else "🔴" if legs and greens == 0 else "🟡")
        out.append((label, light, parts))
    return out


def regime_line(q):
    """One digest line, e.g. '🟢 US · 🟡 India — /regime for the legs'."""
    rows = regime(q)
    if not rows:
        return ""
    return ("<b>Regime</b> " +
            " · ".join(f"{light} {label}" for label, light, _ in rows) +
            "  <i>(descriptive — /regime)</i>")


def regime_text(q):
    """/regime — the legs behind the traffic lights."""
    rows = regime(q)
    lines = ["<b>Market regime</b> <i>(three deterministic legs; "
             "descriptive only, never in scores)</i>"]
    for label, light, parts in rows:
        lines.append(f"{light} <b>{label}</b> — " +
                     ("; ".join(parts) if parts else "no fresh inputs"))
    return "\n".join(lines)


CORE_TOP_SQL = (
    "SELECT symbol FROM (SELECT DISTINCT ON (symbol) symbol, rank "
    "FROM stock_scores WHERE ts >= now() - interval '7 days' AND eligible "
    "AND universe IN ('SP500', 'NIFTY500') ORDER BY symbol, ts DESC) s "
    "WHERE rank <= 25")


def earnings_text(q, chat_id=None, days=14):
    """/earnings — upcoming dates for YOUR watchlist + the core top-25s."""
    symbols, watch = set(), set()
    try:
        watch = {r[0] for r in q(WATCH_SQL, (chat_id,))}
    except Exception as exc:  # noqa: BLE001 - only the missing-table case is expected
        if not table_missing(exc):
            raise
    symbols = watch | {r[0] for r in q(CORE_TOP_SQL)}
    soon = earnings_soon(q, sorted(symbols), days=days)
    if not soon:
        return (f"No earnings dates in the next {days} days for your watchlist "
                "or the core top-25s. (Calendar refreshes Sundays; NSE coverage "
                "is patchy by source.)")
    lines = [f"<b>Earnings — next {days} days</b> <i>(watchlist ★ + core top-25)</i>"]
    for sym, d in sorted(soon.items(), key=lambda kv: (kv[1], kv[0])):
        star = " ★" if sym in watch else ""
        lines.append(f"• <b>{esc(bare(sym))}</b>{star} — {d:%a %d %b}")
    return "\n".join(lines)


def scorecard_text(q):
    """/scorecard — prediction scorecard summary (ADR 0028): snapshot counts,
    and top-vs-bottom decile excess once outcomes accrue."""
    try:
        counts = q("SELECT kind, count(*), min(ts)::date, max(ts)::date "
                   "FROM prediction_snapshots GROUP BY kind ORDER BY kind")
    except Exception as exc:  # noqa: BLE001 - only the missing-table case is expected
        if not table_missing(exc):
            raise
        return "Scorecard tables not created yet."
    if not counts:
        return "No prediction snapshots yet — the tracker runs daily at 02:40 UTC."
    lines = ["<b>Prediction scorecard</b> <i>(ADR 0028 — write-once, "
             "benchmark-relative)</i>"]
    for kind, n, first, last in counts:
        lines.append(f"• {esc(kind)}: {n:,} snapshots, {first:%d %b} → {last:%d %b}")
    deciles = q(
        "SELECT s.kind, o.horizon_days, "
        "avg(CASE WHEN s.dec = 1  THEN o.realized_ret - coalesce(o.bench_ret, 0) END), "
        "avg(CASE WHEN s.dec = 10 THEN o.realized_ret - coalesce(o.bench_ret, 0) END), "
        "count(*) "
        "FROM (SELECT kind, symbol, ts, ntile(10) OVER "
        "      (PARTITION BY kind, ts, universe ORDER BY value) AS dec "
        "      FROM prediction_snapshots) s "
        "JOIN prediction_outcomes o ON o.kind = s.kind AND o.symbol = s.symbol "
        "AND o.ts = s.ts GROUP BY 1, 2 ORDER BY 1, 2")
    if not deciles:
        first_ts = min(r[2] for r in counts)
        lines.append(f"\nNo resolved outcomes yet — the first land ~30 days "
                     f"after {first_ts:%d %b} (then 91d, 182d). The marketview "
                     f"/scorecard page has the full decile tables.")
        return "\n".join(lines)
    lines.append("\n<b>Decile 1 vs 10 — mean excess vs benchmark</b>")
    for kind, horizon, top, bottom, n in deciles:
        t = f"{top * 100:+.1f}%" if top is not None else "–"
        b = f"{bottom * 100:+.1f}%" if bottom is not None else "–"
        lines.append(f"• {esc(kind)} @ {horizon}d: d1 {t} vs d10 {b} (n={n:,})")
    lines.append("<i>Not advice; deciles ranked within each universe.</i>")
    return "\n".join(lines)


def scorecard_line(q):
    """One-line digest summary of the ADR 0028 prediction scorecard."""
    try:
        deciles = q(
            "SELECT s.kind, o.horizon_days, "
            "avg(CASE WHEN s.dec = 1  THEN o.realized_ret - coalesce(o.bench_ret, 0) END), "
            "avg(CASE WHEN s.dec = 10 THEN o.realized_ret - coalesce(o.bench_ret, 0) END), "
            "count(*) "
            "FROM (SELECT kind, symbol, ts, ntile(10) OVER "
            "      (PARTITION BY kind, ts, universe ORDER BY value) AS dec "
            "      FROM prediction_snapshots WHERE kind = 'screener_rank') s "
            "JOIN prediction_outcomes o ON o.kind = s.kind AND o.symbol = s.symbol "
            "AND o.ts = s.ts WHERE o.horizon_days = 30 GROUP BY 1, 2 LIMIT 1")
    except Exception as exc:  # noqa: BLE001
        if not table_missing(exc):
            raise
        return ""
    if deciles and deciles[0][4]:
        _kind, horizon, top, bottom, n = deciles[0]
        t = f"{top * 100:+.1f}%" if top is not None else "–"
        b = f"{bottom * 100:+.1f}%" if bottom is not None else "–"
        return (f"<b>Scorecard</b> ({horizon}d excess vs bench): "
                f"top decile <b>{t}</b> vs bottom <b>{b}</b> (n={n:,})  <i>(/scorecard)</i>")
    return ""
