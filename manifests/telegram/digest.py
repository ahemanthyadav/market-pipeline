"""Daily market digest to Telegram (findings Tier 1 #6, added 2026-10-08).

Reads ONLY existing tables — no new ingestion, no DB writes (house rule: read-only
by default). Composes the 08:00 IST digest and posts it to the Telegram chat.
Index closes are a close/day/YTD table and the watchlist section a table plus
holdings totals (2026-10-10, stocks.py); the watchlist section is skipped
silently while the table is empty or not yet created.
The same Secret also backs the Grafana Telegram contact point
(manifests/grafana/alerting.yaml), so the bot is both the digest and the alert
channel; the token/chat ID stay deployment config, never code.

Since v9 (2026-10-18): an optional AI summary section from llm_narratives (ML-7a) when
DIGEST_NARRATIVE=1 and today's note passed the number check; otherwise the digest is unchanged.

Since 2026-10-25: an over-long digest is cut with stocks.trim_html (a raw slice mid-tag
is a Telegram 400 — the digest would be lost), and headlines() is time-bounded (rule 9).

Env: PGHOST, PGUSER, PGPASSWORD, PGDATABASE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (the owner;
other users come from the bot_users table — ADR 0012). One personalised message per user.
Exit 0 only when the Bot API accepts the message (CronJob failure signal).
"""

import html
import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg
import requests

import stocks
import users

IST = timezone(timedelta(hours=5, minutes=30))
TELEGRAM_LIMIT = 4096

FX_PAIRS = [("USDINR=X", "USD/INR")]
TOP_HEADLINES = 3


def connect():
    conn = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    conn.autocommit = True  # read-only path: house pattern for SELECTs
    return conn


def fx(cur):
    out = []
    for pair, label in FX_PAIRS:
        cur.execute(
            "SELECT ts, rate FROM fx_rates WHERE pair = %s ORDER BY ts DESC LIMIT 1",
            (pair,),
        )
        row = cur.fetchone()
        if row:
            out.append((label, row[0], row[1]))
        else:
            print(f"WARN no data for {label}", flush=True)
    return out


def sentiment(cur):
    cur.execute(
        "SELECT AVG(sentiment_score) FROM news_sentiment WHERE ts > now() - interval '7 days'"
    )
    return cur.fetchone()[0]


def flows(cur):
    # Rule 9: bound both scans — an unbounded max(ts) opens every chunk.
    cur.execute(
        "SELECT flow_type, amount FROM capital_flows "
        "WHERE direction = 'net' AND ts >= now() - interval '90 days' "
        "AND ts = (SELECT max(ts) FROM capital_flows "
        "          WHERE ts >= now() - interval '90 days') "
        "ORDER BY flow_type"
    )
    return cur.fetchall()


def stale_feeds(cur):
    cur.execute(
        "SELECT feed, last_data_ts, max_age_hours FROM pipeline_heartbeat "
        "WHERE fresh = false ORDER BY feed"
    )
    return cur.fetchall()


def headlines(cur):
    # Latest scored headlines, no relevance filter: ticker_relevance is a
    # sector/index keyword tag or 'general' (sentiment-score.py), not a level.
    # Rule 9: ts >= bound so the planner prunes chunks (3d spans a weekend gap).
    cur.execute(
        "SELECT ts, headline, source FROM news_sentiment "
        "WHERE ts >= now() - interval '3 days' "
        "ORDER BY ts DESC LIMIT %s",
        (TOP_HEADLINES,),
    )
    return cur.fetchall()


def narrative(cur):
    """Today's AI market note (ML-7a, ADR 0022) — only if it passed the number check (status ok).
    None when switched off (DIGEST_NARRATIVE != 1), missing, or the table does not exist yet."""
    if os.environ.get("DIGEST_NARRATIVE", "0") != "1":
        return None
    try:
        cur.execute("SELECT summary FROM llm_narratives WHERE ts = %s AND kind = 'daily_market' "
                    "AND status = 'ok'", (datetime.now(IST).date(),))
    except psycopg.Error as exc:
        if not stocks.table_missing(exc):
            raise
        return None
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def watchlist(cur, chat_id=None):
    """One user's compact watchlist lines; [] when empty or the table/column is missing."""
    def q(sql, args=()):
        cur.execute(sql, args)
        return cur.fetchall()
    try:
        rows = q(stocks.WATCH_SQL, (chat_id,))
        if not rows:
            return []
        return stocks.watch_block(rows, stocks.ytd_map(q, [r[0] for r in rows]),
                                  notes=False)
    except psycopg.Error as exc:
        if not stocks.table_missing(exc):
            raise
        print("WARN watchlist table missing — section skipped", flush=True)
        return []


TOP_SQL = """
    SELECT symbol FROM (
      SELECT DISTINCT ON (symbol) symbol, rank FROM stock_scores
      WHERE ts >= now() - interval '7 days' AND eligible
        AND universe IN ('SP500', 'NIFTY500')
      ORDER BY symbol, ts DESC) s
    WHERE rank <= 25
"""
# Flagships only (ADR 0029): the digest's earnings section tracks the core
# top-25s + the user's own watchlist; extended universes appear on request.


def earnings_week(cur, chat_id=None):
    """Upcoming-earnings lines for the user's watchlist + the top-25s."""
    def q(sql, args=()):
        cur.execute(sql, args)
        return cur.fetchall()
    try:
        symbols = {r[0] for r in q(stocks.WATCH_SQL, (chat_id,))}
        symbols |= {r[0] for r in q(TOP_SQL)}
        return stocks.earnings_lines(q, sorted(symbols), days=7)
    except psycopg.Error as exc:
        if not stocks.table_missing(exc):
            raise
        print("WARN earnings_calendar missing — section skipped", flush=True)
        return []


def facts_block(fx_rows, gauge, flow_rows):
    """FX / sentiment / flows <pre> block + its legend; pure, so tests can
    hold every line to the phone width. Keep kv() keys short: the longest key
    pads every line of the block, and past ~38 chars phones wrap the table."""
    facts = [(label, f"{rate:,.4f}") for label, _ts, rate in fx_rows]
    if gauge is not None:
        facts.append(("Sentiment, 7-day", f"{gauge:+.3f}"))
    facts += [(f"{flow} net, latest day (Rs cr)", f"{amount:+,.0f}")
              for flow, amount in flow_rows]
    if not facts:
        return []
    out = [stocks.kv(facts)]
    if gauge is not None:
        out.append("<i>Sentiment = 7-day average of FinBERT-scored headlines, "
                   "−1 most negative to +1 most positive.</i>")
    return out


def build_message(chat_id=None):
    """The digest for one user (their own watchlist); chat_id None = the owner."""
    chat_id = str(chat_id or os.environ.get("TELEGRAM_CHAT_ID", ""))
    conn = connect()
    with conn.cursor() as cur:
        def q(sql, args=()):
            cur.execute(sql, args)
            return cur.fetchall()

        lines = [f"<b>Market digest</b>  <i>{datetime.now(IST):%d %b %Y, %H:%M IST}</i>",
                 stocks.quote_table(q, stocks.DIGEST_QUOTES)]
        try:
            light = stocks.regime_line(q)
            if light:
                lines.append(light)
        except psycopg.Error as exc:
            if not stocks.table_missing(exc):
                raise
            print("WARN regime inputs missing — line skipped", flush=True)
        try:
            sc_line = stocks.scorecard_line(q)
            if sc_line:
                lines.append(sc_line)
        except psycopg.Error as exc:
            if not stocks.table_missing(exc):
                raise
            print("WARN scorecard inputs missing — line skipped", flush=True)
        note = narrative(cur)
        if note:
            lines.append("<b>Summary</b> <i>(AI-written; every number checked against the data)</i>\n"
                         + html.escape(note))

        lines += facts_block(fx(cur), sentiment(cur), flows(cur))

        stale = stale_feeds(cur)
        lines.append(
            "Stale feeds: <b>none</b>" if not stale
            else "<b>Stale feeds</b>\n" + "\n".join(
                f"• {html.escape(feed)} — last data {last:%d %b}" for feed, last, _ in stale
            )
        )

        watched = watchlist(cur, chat_id)
        if watched:
            lines.append("\n<b>Watchlist</b>\n" + "\n".join(watched))

        upcoming = earnings_week(cur, chat_id)
        if upcoming:
            lines.append("\n<b>Earnings this week</b> <i>(watchlist + top 25)</i>\n"
                         + "\n".join(upcoming))

        tops = headlines(cur)
        if tops:
            lines.append("\n<b>Latest headlines</b>\n" + "\n".join(
                f"• {html.escape(headline)} <i>{html.escape(source or '')}</i>"
                for _, headline, source in tops
            ))

    conn.close()
    # Never slice raw HTML: a cut mid-tag is a Telegram 400 and the digest is lost.
    return stocks.trim_html("\n".join(lines), TELEGRAM_LIMIT)


def send(text: str, chat=None) -> bool:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = str(chat or os.environ["TELEGRAM_CHAT_ID"])
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
            timeout=30,
        )
    except requests.RequestException as exc:
        print(f"ERROR telegram send failed: {exc}", flush=True)
        return False

    if r.status_code != 200 or not r.json().get("ok"):
        print(f"ERROR telegram {r.status_code}: {r.text[:200]}", flush=True)
        return False
    print(f"digest sent to chat {chat[:3]}*** ({len(text)} chars)", flush=True)
    return True


def main() -> int:
    owner = os.environ["TELEGRAM_CHAT_ID"]
    conn = connect()
    try:
        with conn.cursor() as cur:
            def q(sql, args=()):
                cur.execute(sql, args)
                return cur.fetchall()
            recipients = users.recipients_for_jobs(q, owner)
    finally:
        conn.close()
    rc = users.deliver(recipients, owner, build_message, send)
    print(f"DIGEST-DONE recipients={len(recipients)} rc={rc}", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
