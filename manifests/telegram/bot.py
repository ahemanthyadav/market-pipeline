"""Two-way Telegram listener — Tier 2 interface (2026-10-09, ADR 0009 family).

Long-polling getUpdates loop: CGNAT (F10) makes webhooks impossible, so the
bot pulls. This is the ONLY getUpdates consumer — Telegram 409s a second
poller; the digest CronJob and Grafana's contact point only sendMessage.

Security model (ADR 0012, multi-user since 2026-10-12): the owner (the Secret's
TELEGRAM_CHAT_ID) plus the active rows of `bot_users` are answered; everyone has their
OWN watchlist, digest and alerts. Telegram bots are public — anyone who finds
@Marketfredbot can message it — so the allow-list IS the door. Unknown chats are logged and
never answered; the owner gets one "access request" notice per chat per day with a
ready-made /adduser line. Only the owner can /adduser /rmuser /users.

DB access: read-only SELECTs (house rule) with TWO exceptions the bot owns — /watch add|rm
writes `watchlist` (keyed by chat), and the owner's /adduser|/rmuser write `bot_users`.
Connections open per command and close after — long-idle pod→DB connections
get dropped (F26), so nothing is held across the sleep between polls. One
retry per query.

Commands: /help /status /vix /nifty /sensex /gold /fii /scores /digest
  /stock /news /watch /top /movers /track /macro /world /index /fx /multibagger /sp500 (stock/watchlist/screener
  views live in stocks.py, 2026-10-10). /start also maps to help. /digest
  re-runs digest.build_message() — the scripts share this image; the
  Deployment selects this entrypoint via args.

Crash semantics: offset is in-memory only. On start the backlog is DRAINED
without replying (a restart must not replay old commands); commands are
idempotent reads, so at worst a crash re-answers one message.

Env: PGHOST, PGUSER, PGPASSWORD, PGDATABASE, TELEGRAM_BOT_TOKEN,
TELEGRAM_CHAT_ID (the owner). Missing env exits 1 (k3s restarts); Telegram/DB blips are
retried with capped backoff and never fatal — including the startup getMe (2026-10-25).

Since 2026-10-25: over-long replies are cut with stocks.trim_html (a raw slice
mid-tag is a Telegram 400 and the message is lost), and the poll loop touches
/tmp/bot-alive each iteration for a future exec liveness probe.

Validate manually:
  kubectl logs -n market-data deploy/telegram-bot -f   # expect "listening as @"
"""

import html
import os
import sys
import time
from pathlib import Path

import psycopg
import requests

import stocks
import users
import router
from digest import TELEGRAM_LIMIT, build_message

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OWNER = os.environ["TELEGRAM_CHAT_ID"]
ALLOWED = OWNER   # back-compat alias for the single-user name
API = f"https://api.telegram.org/bot{TOKEN}"


# ---------------------------------------------------------------- DB helpers

def query(sql: str, args: tuple = ()) -> list:
    """Run one read-only SELECT; one reconnect-retry per F26."""
    for attempt in (1, 2):
        try:
            conn = psycopg.connect(
                host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
                user=os.environ.get("PGUSER", "postgres"),
                password=os.environ["PGPASSWORD"],
                dbname=os.environ.get("PGDATABASE", "postgres"),
            )
            try:
                conn.autocommit = True  # house pattern: SELECTs never hold a txn
                with conn.cursor() as cur:
                    cur.execute(sql, args)
                    return cur.fetchall()
            finally:
                conn.close()
        except psycopg.Error as exc:
            if attempt == 2:
                raise
            print(f"WARN db retry ({exc.__class__.__name__})", flush=True)


def symbol_lookup(text: str):
    """Universe symbol for a user-typed name/symbol, or None (never raises).

    Same resolution layers as /stock (stocks.pick_one_ex): exact symbol or name,
    then the forgiving resolver (fuzzy spelling, then AI when BOT_AI_RESOLVE=1).
    Lets every command understand stocks missing from the static nickname map —
    "/status cub" → CUB.NS, "city union" → CUB.NS.
    """
    try:
        row, _problem, _note = stocks.pick_one_ex(query, text)
    except Exception as exc:  # noqa: BLE001 — routing must never break the bot
        print(f"WARN symbol lookup {text!r}: {exc.__class__.__name__}", flush=True)
        return None
    return row[0] if row else None


def execute(sql: str, args: tuple = ()) -> int:
    """One atomic write to the bot-owned watchlist table; returns rowcount."""
    conn = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    try:
        conn.autocommit = True
        with conn.transaction():  # house pattern: one short write unit
            with conn.cursor() as cur:
                cur.execute(sql, args)
                return cur.rowcount
    finally:
        conn.close()


REGISTRY = users.Registry(OWNER, query)
REQUESTS = users.AccessRequests()


# ---------------------------------------------------------------- commands

def cmd_help(_text: str, chat: str = None) -> str:
    text = _help_text()
    if chat is None or REGISTRY.is_owner(chat):
        text += ("\n/users · /adduser · /rmuser — manage who has access (owner)")
    return text


def cmd_route(text: str, chat: str = None) -> str:
    """Explain how a natural-language query would be routed (/route tcs price)."""
    arg = text.partition(" ")[2].strip()
    if not arg:
        return ("Try <code>/route tcs price</code>, <code>/route what's nifty at</code>, "
                "<code>/route facebook quote</code>. The router matches nicknames, "
                "phrases and keywords to the right command automatically — you don't "
                "need to memorise /command syntax.")
    r_cmd, r_args, r_conf = router.route(arg, symbol_lookup, COMMANDS)
    if r_cmd is None:
        return f"I couldn't figure out a command for <code>{html.escape(arg)}</code>. "
    return router.format_route(r_cmd, r_args, r_conf)


def _help_text() -> str:
    return (
        "<b>Marketfredbot</b> — read-only commands:\n"
        "/status — pipeline heartbeat freshness\n"
        "/vix — VIX + India VIX\n"
        "/nifty · /sensex · /gold — close, day, YTD, 52w range, trend\n"
        "/fii — latest FII/DII net + 5-session FII trend\n"
        "/scores — screener top-5 today\n"
        "/stock TCS — price, trend, score, valuation, news for one stock\n"
        "/news TCS — tagged headlines + sentiment\n"
        "/ask TCS outlook — question answered from stored facts only (numbers checked); AI off → facts table\n"
        "/top growth sp500 — best by value|quality|growth|momentum|risk "
        "(universes: nifty, sp500, sp400, sp600, microcap)\n"
        "/multibagger sp500 — small, fast-growing, high-return candidates\n"
        "/movers — who entered/left the screener top-20\n"
        "/sectors sp500 — sector medians + fair-value gap (ADR 0026)\n"
        "/track — how the screener's picks have done\n"
        "/regime — breadth / trend / momentum traffic light per market\n"
        "/earnings — upcoming dates: your watchlist + core top-25s\n"
        "/scorecard — have the system's picks worked? (ADR 0028)\n"
        "/watch — YOUR watchlist + holdings P&amp;L (add/rm); private to this chat\n"
        "/world — every index: close, day, 1m, YTD (/world asia)\n"
        "/index nikkei — one index in depth: 1w–1y, 52w range, trend, RSI\n"
        "/sp500 — S&P 500: close, trend, median P/E/P/B, market cap\n"
        "/valuation PLTR — all 15 intrinsic valuation methods, fair value + upside\n"
        "/calibration — factor ICs, archetype excess returns, MB gate margins, shrunk weights\n"
        "/methods — valuation & scientific method catalog (formulas + sources)\n"
        "/fx — USD/INR and the other pairs\n"
        "/macro — gold in INR, yield spreads, FII vs Nifty\n"
        "/digest — your daily digest on demand\n"
        "Daily digest: 08:00 IST · change alerts after the screener run (shared screener "
        "changes + your own watchlist)\n"
        "AI: append <code>ai</code>, <code>explain</code>, <code>why</code> or <code>?</code> to any command "
        "(e.g. <code>/regime ai</code>, <code>/sectors sp500 explain</code>, <code>/fii why</code>) for a "
        "fact-checked AI takeaway, or ask naturally: <code>tcs outlook</code>, <code>why did infy fall?</code>"
    )


def cmd_status(_text: str) -> str:
    fresh, stale, last_check = query(
        "SELECT count(*) FILTER (WHERE fresh), count(*) FILTER (WHERE NOT fresh), "
        "max(last_check) FROM pipeline_heartbeat"
    )[0]
    head = f"Pipeline: <b>{fresh} fresh / {stale} stale</b> · last check {last_check:%d %b %H:%M UTC}"
    if stale:
        names = ", ".join(r[0] for r in query(
            "SELECT feed FROM pipeline_heartbeat WHERE NOT fresh ORDER BY feed"))
        head += f"\nStale: <b>{html.escape(names)}</b>"
    return head


def cmd_vix(_text: str) -> str:
    return "<b>Volatility</b>\n" + stocks.quote_table(query, ["^VIX", "^INDIAVIX"])


def cmd_nifty(_text: str) -> str:
    return stocks.index_card(query, "^NSEI", "Nifty 50")


def cmd_sensex(_text: str) -> str:
    return stocks.index_card(query, "^BSESN", "Sensex")


def cmd_gold(_text: str) -> str:
    return stocks.index_card(query, "GC=F", "Gold futures (USD/oz)")


def cmd_index(text: str) -> str:
    arg = _arg(text)
    out = stocks.index_text(query, arg)
    if arg and out.startswith("No index matching"):
        row, _problem, note = stocks.pick_one_ex(query, arg)
        if row and not str(row[0]).startswith("^"):
            notice = (f"↪️ <i><b>{html.escape(arg)}</b> is a stock, not an index — running "
                      f"<code>/stock {html.escape(row[0])}</code>:</i>\n")
            return notice + ((note + "\n") if note else "") + stocks.stock_card(query, row)
    return out


def cmd_sp500(_text: str) -> str:
    """SP500 in depth: close, performance, trend, plus constituent-level
    quant values (median P/E, P/B, total market cap)."""
    return stocks.index_card(query, "^GSPC", "S&P 500")


def cmd_valuation(text: str) -> str:
    """All 15 intrinsic valuation methods for one stock."""
    return stocks.valuation_text(query, _arg(text))


def cmd_calibration(_text: str) -> str:
    """Latest factor & archetype calibration results (classifier loop)."""
    return stocks.calibration_text(query)


def cmd_methods(_text: str) -> str:
    """Valuation & scientific method catalog (Schema 25 reference)."""
    return stocks.methods_text(query)


def cmd_fii(_text: str) -> str:
    # Rule 9: ts >= bound so the planner prunes chunks (90d covers the 5-session view).
    latest = query(
        "SELECT flow_type, amount FROM capital_flows "
        "WHERE direction = 'net' AND ts >= now() - interval '90 days' "
        "AND ts = (SELECT max(ts) FROM capital_flows "
        "          WHERE ts >= now() - interval '90 days') "
        "ORDER BY flow_type"
    )
    trend = query(
        "SELECT ts::date, amount FROM capital_flows "
        "WHERE flow_type = 'FII' AND direction = 'net' "
        "AND ts >= now() - interval '90 days' "
        "ORDER BY ts DESC LIMIT 5"
    )
    lines = [" · ".join(f"{f} <b>{a:,.0f}</b>" for f, a in latest) + " Rs cr"]
    lines += [f"{d}: {a:,.0f}" for d, a in reversed(trend)]
    return "\n".join(lines)


def cmd_scores(_text: str) -> str:
    return stocks.top_text(query, "composite", "NIFTY500", 5)


def cmd_digest(_text: str, chat: str = None) -> str:
    return build_message(chat or OWNER)


def _arg(text: str) -> str:
    return text.partition(" ")[2].strip()


def cmd_stock(text: str) -> str:
    arg = _arg(text)
    row, problem, note = stocks.pick_one_ex(query, arg)
    if row and str(row[0]).startswith("^"):
        idx = stocks.find_index(query, row[0]) or [(row[0], row[1] or row[0])]
        notice = (f"↪️ <i><b>{html.escape(arg)}</b> is an index — running "
                  f"<code>/index {html.escape(idx[0][0])}</code>:</i>\n")
        return notice + stocks.index_card(query, idx[0][0], idx[0][1])
    if problem and arg:
        idx = stocks.find_index(query, arg)
        if len(idx) == 1:
            notice = (f"↪️ <i><b>{html.escape(arg)}</b> is an index, not a stock — running "
                      f"<code>/index {html.escape(idx[0][0])}</code>:</i>\n")
            return notice + stocks.index_card(query, idx[0][0], idx[0][1])
    return problem or ((note + "\n") if note else "") + stocks.stock_card(query, row)


def cmd_news(text: str) -> str:
    row, problem, note = stocks.pick_one_ex(query, _arg(text))
    return problem or ((note + "\n") if note else "") + stocks.news_text(query, row)


def cmd_ask(text: str) -> str:
    import ask
    arg = _arg(text)
    if not arg:
        return "Ask a question about a stock, index, FX, flows or recent news, e.g. <code>/ask TCS outlook</code>"
    return ask.answer(query, arg)


def cmd_top(text: str) -> str:
    factor, universe, problem = stocks.parse_top(text)
    return problem or stocks.top_text(query, factor, universe)


def cmd_multibagger(text: str) -> str:
    words = text.lower().split()[1:]
    if len(words) > 1 or (words and words[0] not in stocks.UNIVERSES):
        return "Usage: <code>/multibagger [nifty|sp500]</code>"
    return stocks.multibagger_text(query, stocks.UNIVERSES[words[0]] if words else "NIFTY500")


def cmd_movers(_text: str) -> str:
    return stocks.movers_text(query)


def cmd_sectors(text: str) -> str:
    return stocks.sectors_card(query, text)


def cmd_track(_text: str) -> str:
    return stocks.track_text(query)


def cmd_regime(_text: str) -> str:
    return stocks.regime_text(query)


def cmd_earnings(_text: str, chat: str = None) -> str:
    return stocks.earnings_text(query, chat)


def cmd_scorecard(_text: str) -> str:
    return stocks.scorecard_text(query)


def cmd_macro(_text: str) -> str:
    return stocks.macro_text(query)


def cmd_world(text: str) -> str:
    region, problem = stocks.parse_world(text)
    return problem or stocks.world_text(query, region)


def cmd_fx(_text: str) -> str:
    return stocks.fx_text(query)


def cmd_watch(text: str, chat: str = None) -> str:
    chat = str(chat or OWNER)          # no chat (runbook one-liners) = the owner
    action, arg, qty, cost, user_note, problem = stocks.parse_watch(text)
    try:
        # Allow "/watch tesla" or "watch tesla" (implicit 'add' when the word after /watch is a stock)
        if problem == stocks.WATCH_USAGE and _arg(text) and _arg(text).lower() != "add":
            c_act, c_arg, c_qty, c_cost, c_note, c_prob = stocks.parse_watch("/watch add " + _arg(text))
            if not c_prob and c_arg:
                full_q = f"{c_arg} {c_note}".strip() if (c_qty is None and c_note) else c_arg
                row_check, prob_check, _ = stocks.pick_one_ex(query, full_q)
                if row_check or (prob_check and not prob_check.startswith("No stock matching")):
                    action, arg, qty, cost, user_note, problem = (
                        c_act, full_q if row_check and full_q != c_arg else c_arg,
                        c_qty, c_cost, None if (row_check and full_q != c_arg) else c_note, None
                    )
        if problem:
            return problem
        if action == "list":
            return stocks.watch_text(query, chat)
        row, problem, resolve_note = stocks.pick_one_ex(query, arg)
        if problem:
            return problem
        if resolve_note:   # a corrected/AI guess never writes to a watchlist — confirm with the exact symbol
            return (f"{resolve_note}\nNot changed. If that is right, send it again with the exact symbol: "
                    f"<code>/watch {html.escape(action)} {html.escape(row[0])}</code>")
        symbol = row[0]
        if action == "rm":
            gone = execute("DELETE FROM watchlist WHERE chat_id = %s AND symbol = %s",
                           (chat, symbol))
            return f"Removed {html.escape(symbol)}." if gone else (
                f"{html.escape(symbol)} was not on the watchlist.")
        # Re-adding keeps an existing position/note unless new values are given.
        execute(
            "INSERT INTO watchlist (chat_id, symbol, qty, avg_cost, note) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (chat_id, symbol) DO UPDATE SET "
            "qty = COALESCE(EXCLUDED.qty, watchlist.qty), "
            "avg_cost = COALESCE(EXCLUDED.avg_cost, watchlist.avg_cost), "
            "note = COALESCE(EXCLUDED.note, watchlist.note)",
            (chat, symbol, qty, cost, user_note),
        )
        held = f" — {qty:g} @ {cost:,.2f}" if qty else ""
        return f"Watching <b>{html.escape(row[1])}</b> ({html.escape(symbol)}){held}."
    except psycopg.Error as exc:
        if stocks.table_missing(exc):
            return ("Watchlist table not ready yet (schema 09 multi-user — "
                    "schema-configmap.yaml → schema job).")
        raise


def cmd_users(_text: str, chat: str = None) -> str:
    return users.users_text(REGISTRY, query)


def cmd_adduser(text: str, chat: str = None) -> str:
    target, name, problem = users.parse_adduser(text)
    if problem:
        return problem
    try:
        return users.add_user(execute, REGISTRY, target, name, str(chat or OWNER))
    except psycopg.Error as exc:
        if stocks.table_missing(exc):
            return "bot_users table not created yet (schema 09 — run the schema job)."
        raise


def cmd_rmuser(text: str, chat: str = None) -> str:
    target, problem = users.parse_rmuser(text)
    if problem:
        return problem
    try:
        return users.remove_user(execute, REGISTRY, target)
    except psycopg.Error as exc:
        if stocks.table_missing(exc):
            return "bot_users table not created yet (schema 09 — run the schema job)."
        raise


# Commands that need to know WHO is asking get (text, chat); the rest stay (text) so
# one-liners like COMMANDS['nifty']('/nifty') keep working. ADMIN is owner-only.
PERSONAL = {"start", "help", "digest", "watch", "users", "adduser", "rmuser",
            "earnings", "route"}
ADMIN = {"users", "adduser", "rmuser"}

COMMANDS = {
    "start": cmd_help,
    "help": cmd_help,
    "route": cmd_route,
    "status": cmd_status,
    "vix": cmd_vix,
    "nifty": cmd_nifty,
    "sensex": cmd_sensex,
    "gold": cmd_gold,
    "fii": cmd_fii,
    "scores": cmd_scores,
    "digest": cmd_digest,
    "stock": cmd_stock,
    "news": cmd_news,
    "ask": cmd_ask,
    "top": cmd_top,
    "movers": cmd_movers,
    "sectors": cmd_sectors,
    "multibagger": cmd_multibagger,
    "mb": cmd_multibagger,
    "track": cmd_track,
    "regime": cmd_regime,
    "earnings": cmd_earnings,
    "scorecard": cmd_scorecard,
    "macro": cmd_macro,
    "world": cmd_world,
    "index": cmd_index,
    "sp500": cmd_sp500,
    "valuation": cmd_valuation,
    "calibration": cmd_calibration,
    "methods": cmd_methods,
    "fx": cmd_fx,
    "usdinr": cmd_fx,
    "watch": cmd_watch,
    "users": cmd_users,
    "adduser": cmd_adduser,
    "rmuser": cmd_rmuser,
}


AI_FLAGS = frozenset({"ai", "explain", "why", "summary", "?"})
NO_AI_WRAP = frozenset({"help", "start", "ask", "route", "users", "adduser", "rmuser"})


def split_ai_flag(text: str):
    """Strip a trailing AI request flag ('ai', 'explain', 'why', 'summary', '?') from a command
    so the underlying handler parses its normal arguments, returning (clean_text, want_ai)."""
    raw = (text or "").strip()
    want_env = os.environ.get("BOT_AI_COMMENTARY") == "1"
    if raw.endswith("?") and len(raw) > 1:
        return raw[:-1].rstrip(), True
    parts = raw.split()
    if len(parts) >= 2 and parts[-1].lower().rstrip("?") in AI_FLAGS:
        return " ".join(parts[:-1]), True
    return raw, want_env


def with_ai_takeaway(result: str, cmd: str, want_ai: bool, asker=None) -> str:
    """Append a fact-checked AI takeaway to `result` when requested and supported."""
    if not want_ai or cmd in NO_AI_WRAP or not isinstance(result, str):
        return result
    try:
        import ask
        return result + ask.summarize_card(result, command=cmd, asker=asker)
    except Exception:  # noqa: BLE001 — AI takeaway is optional, never breaks a command
        return result


# ---------------------------------------------------------------- transport

def send(text: str, chat: str = None) -> None:
    # Never slice raw HTML: a cut mid-tag is a Telegram 400 and the message is lost.
    text = stocks.trim_html(text, TELEGRAM_LIMIT)
    r = requests.post(f"{API}/sendMessage",
                      json={"chat_id": str(chat or OWNER), "text": text, "parse_mode": "HTML"},
                      timeout=30)
    if r.status_code != 200 or not r.json().get("ok"):
        print(f"ERROR sendMessage {r.status_code}: {r.text[:200]}", flush=True)


def handle(update: dict) -> None:
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat = str(msg.get("chat", {}).get("id", ""))
    text = (msg.get("text") or "").strip()

    if not REGISTRY.allowed(chat):
        who = (msg.get("from") or {})
        username = who.get("username")
        print(f"IGNORED chat={chat} user={username or who.get('id')} "
              f"text={text[:40]!r}", flush=True)
        if text and REQUESTS.should_notify(chat):   # tell the owner once; never answer them
            send(users.request_notice(chat, username, text), OWNER)
        return

    if not text:
        return
    clean_text, want_ai = split_ai_flag(text)
    typed_slash = clean_text.startswith("/")
    cmd = clean_text.split()[0].lstrip("/").split("@")[0].lower()
    handler = COMMANDS.get(cmd)
    if handler is None:
        # 1. Deterministic router first (exact aliases, fuzzy command typos, nicknames, phrases, universe lookup)
        r_cmd, r_args, r_conf = router.route(clean_text, symbol_lookup, COMMANDS)
        # 2. If deterministic routing fails, AI router takes over
        if not r_cmd:
            r_cmd, r_args, r_conf = router.ai_route(clean_text, COMMANDS)
        if r_cmd and r_cmd in COMMANDS:
            full_text = f"/{r_cmd} {r_args}".strip() if r_args else f"/{r_cmd}"
            if r_conf == "ai":
                notice = f"↪️ <i>Routed by AI to <code>{html.escape(full_text)}</code>:</i>\n"
            elif typed_slash and r_cmd != cmd:
                notice = (f"↪️ <i><code>/{html.escape(cmd)}</code> is not a command — "
                          f"running <code>{html.escape(full_text)}</code>:</i>\n")
            else:
                notice = ""
            try:
                out = COMMANDS[r_cmd](full_text, chat) if r_cmd in PERSONAL else COMMANDS[r_cmd](full_text)
                send(notice + with_ai_takeaway(out, r_cmd, want_ai), chat)
                print(f"routed /{r_cmd} ({r_conf})", flush=True)
                return
            except Exception as exc:  # noqa: BLE001 - one bad query must not kill the loop
                print(f"ERROR routed /{r_cmd}: {exc}", flush=True)
                send("Command failed — see logs (kubectl logs deploy/telegram-bot).", chat)
                return
        # 3. Fallback: if text maps to stored DB facts, answer via /ask (fact-checked AI)
        try:
            import ask
            q_text = clean_text.lstrip("/")
            facts, _subj = ask.build_facts(query, q_text)
            if facts:
                notice = (f"↪️ <i><code>/{html.escape(cmd)}</code> is not a command — "
                          f"answering via <code>/ask</code>:</i>\n") if typed_slash else ""
                send(notice + cmd_ask(f"/ask {q_text}"), chat)
                print("routed /ask (facts-fallback)", flush=True)
                return
        except Exception:  # noqa: BLE001
            pass
        send(f"Unknown command. {chr(10)}" + cmd_help("", chat), chat)
        return
    if cmd in ADMIN and not REGISTRY.is_owner(chat):
        send("That command is for the owner only.", chat)
        return
    # A no-argument command followed by a stock name means that stock:
    # "/status cub", "/movers cub" → /stock CUB.NS (ARG_COMMANDS keep their args).
    args_text = clean_text.split(None, 1)[1].strip() if len(clean_text.split()) > 1 else ""
    reroute_notice = ""
    sym = router.symbol_from_args(cmd, args_text, symbol_lookup)
    if sym:
        reroute_notice = (f"↪️ <i><code>/{html.escape(cmd)}</code> takes no stock argument — "
                          f"running <code>/stock {html.escape(sym)}</code>:</i>\n")
        clean_text, cmd, handler = f"/stock {sym}", "stock", COMMANDS["stock"]
        print(f"rerouted /status-style → /stock {sym}", flush=True)
    try:
        result = handler(clean_text, chat) if cmd in PERSONAL else handler(clean_text)
        # If the handler returned an argument/usage error (e.g. "/top tcs", "/sectors tcs",
        # "/world tcs", "/multibagger tcs"), try deterministic router first, then AI router
        if isinstance(result, str) and result.startswith(
            ("Usage:", "Unknown universe", "Unknown region", "<b>/watch</b> — list")
        ) and args_text:
            r_cmd, r_args, r_conf = router.route(args_text, symbol_lookup, COMMANDS)
            if not r_cmd or r_cmd == cmd:
                r_cmd, r_args, r_conf = router.ai_route(args_text, COMMANDS)
            if r_cmd and r_cmd in COMMANDS and r_cmd != cmd:
                full_text = f"/{r_cmd} {r_args}".strip() if r_args else f"/{r_cmd}"
                via = " (routed by AI)" if r_conf == "ai" else ""
                notice = (f"↪️ <i><code>/{html.escape(cmd)}</code> cannot handle "
                          f"<code>{html.escape(args_text)}</code> — running "
                          f"<code>{html.escape(full_text)}</code>{via}:</i>\n")
                try:
                    out = COMMANDS[r_cmd](full_text, chat) if r_cmd in PERSONAL else COMMANDS[r_cmd](full_text)
                    send(notice + with_ai_takeaway(out, r_cmd, want_ai), chat)
                    print(f"routed /{r_cmd} ({r_conf}) after handler failure", flush=True)
                    return
                except Exception as exc:  # noqa: BLE001
                    print(f"ERROR routed /{r_cmd}: {exc}", flush=True)
        send(reroute_notice + with_ai_takeaway(result, cmd, want_ai), chat)
        print(f"answered /{cmd}", flush=True)
    except Exception as exc:  # noqa: BLE001 - one bad query must not kill the loop
        print(f"ERROR /{cmd}: {exc}", flush=True)
        send("Command failed — see logs (kubectl logs deploy/telegram-bot).", chat)


def poll_forever() -> None:
    offset = None
    drain = True  # first fetch: skip the backlog, never replay old commands
    failures = 0
    while True:
        try:   # heartbeat for a future exec liveness probe (file age = loop health)
            Path("/tmp/bot-alive").touch()
        except OSError:
            pass   # best-effort: a full /tmp must never stop the loop
        params = {"timeout": 25}
        if offset is not None:
            params["offset"] = offset
        try:
            r = requests.get(f"{API}/getUpdates", params=params, timeout=30)
            updates = r.json().get("result", [])
            failures = 0
        except (requests.RequestException, ValueError) as exc:
            failures += 1
            wait = min(60, 3 * failures)
            print(f"WARN getUpdates failed ({exc.__class__.__name__}), retry in {wait}s",
                  flush=True)
            time.sleep(wait)
            continue

        if updates:
            offset = updates[-1]["update_id"] + 1
        if drain:
            if updates:
                print(f"drained {len(updates)} backlog update(s)", flush=True)
            drain = False
            continue

        for update in updates:
            handle(update)


MENU = [
    ("stock", "Price, trend, score, valuation, news — /stock TCS"),
    ("watch", "Your watchlist + holdings P&L (add / rm)"),
    ("top", "Best by factor — /top growth sp500"),
    ("multibagger", "Small, fast-growing, high-return candidates"),
    ("movers", "Entered / left the screener top-20"),
    ("sectors", "Sector medians + fair-value gap — /sectors sp600"),
    ("earnings", "Upcoming earnings: watchlist + core top-25"),
    ("scorecard", "Have the system's picks worked? (ADR 0028)"),
    ("regime", "Breadth / trend / momentum traffic light"),
    ("track", "How the screener's picks have done"),
    ("news", "Tagged headlines + sentiment — /news TCS"),
    ("ask", "Question answered from stored facts only — /ask TCS outlook"),
    ("world", "All world indices: day, 1m, YTD — /world asia"),
    ("index", "One index in depth — /index nikkei"),
    ("sp500", "S&P 500 in depth — close, trend, median P/E/P/B, cap"),
    ("valuation", "All 15 intrinsic valuation methods — /valuation PLTR"),
    ("calibration", "Factor ICs, archetype returns, MB gate margins, shrunk weights"),
    ("methods", "Valuation & scientific method catalog — formulas + sources"),
    ("fx", "USD/INR and other currency pairs"),
    ("macro", "Gold in INR, yield spreads, FII vs Nifty"),
    ("scores", "Screener top-5"),
    ("digest", "Daily digest on demand"),
    ("status", "Pipeline heartbeat freshness"),
    ("vix", "VIX + India VIX"),
    ("nifty", "Nifty 50"),
    ("sensex", "Sensex"),
    ("gold", "Gold futures"),
    ("fii", "FII/DII net flows"),
    ("help", "All commands"),
    ("route", "Natural-language → command: /route tcs price"),
]


def register_menu() -> None:
    """Best-effort: the Telegram command menu follows the code, not BotFather."""
    try:
        r = requests.post(f"{API}/setMyCommands", timeout=15, json={
            "commands": [{"command": c, "description": d} for c, d in MENU]})
        if r.status_code != 200 or not r.json().get("ok"):
            print(f"WARN setMyCommands {r.status_code}: {r.text[:120]}", flush=True)
    except (requests.RequestException, ValueError) as exc:
        print(f"WARN setMyCommands failed ({exc.__class__.__name__})", flush=True)


def main() -> int:
    me = {}
    for attempt in (1, 2, 3):   # one network blip at pod start must not crash-loop the pod
        try:
            me = requests.get(f"{API}/getMe", timeout=15).json().get("result", {})
            break
        except (requests.RequestException, ValueError) as exc:
            if attempt == 3:
                print(f"ERROR getMe failed after {attempt} attempts "
                      f"({exc.__class__.__name__})", flush=True)
                return 1
            print(f"WARN getMe attempt {attempt} failed ({exc.__class__.__name__}), "
                  "retrying in 3s", flush=True)
            time.sleep(3)
    register_menu()
    print(f"listening as @{me.get('username')} for owner chat {OWNER[:2]}*** "
          f"+ {len(REGISTRY.members())} member(s)", flush=True)
    poll_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
