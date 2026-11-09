#!/usr/bin/env python3
"""Tests for the multi-user Telegram layer (ADR 0012): manifests/telegram/users.py, the per-user
WATCH_SQL / alert split in stocks.py, and bot.handle() end to end against an in-memory fake DB.

No database, no network: psycopg and requests are stubbed so this also runs in a bare CI env.

    python3 tests/test_multiuser.py
"""
import os
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "manifests" / "telegram"))

# --- stubs + env must exist before bot/digest are imported ------------------------------
psycopg_stub = types.ModuleType("psycopg")


class _PgError(Exception):
    pass


psycopg_stub.Error = _PgError
psycopg_stub.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no real DB in tests"))
sys.modules.setdefault("psycopg", psycopg_stub)
requests_stub = types.ModuleType("requests")
requests_stub.RequestException = type("RequestException", (Exception,), {})
requests_stub.post = requests_stub.get = lambda *a, **k: (_ for _ in ()).throw(
    AssertionError("no network in tests"))
sys.modules.setdefault("requests", requests_stub)
OWNER, ALICE, BOB, STRANGER, GROUP = "100000001", "200000002", "300000003", "400000004", "-1001234567890"
os.environ.update(TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID=OWNER, PGPASSWORD="x")

import stocks  # noqa: E402
import users  # noqa: E402
import bot  # noqa: E402

NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)


class FakeDB:
    """In-memory bot_users + watchlist behind the same query()/execute() the bot uses."""

    def __init__(self):
        self.members = {}      # chat -> [name, active]
        self.watch = {}        # (chat, symbol) -> (qty, avg_cost, note)
        self.writes = []

    def query(self, sql, args=()):
        if "FROM bot_users" in sql:
            return [(c, n) for c, (n, active) in self.members.items() if active]
        if "FROM watchlist GROUP BY" in sql:
            out = {}
            for chat, _sym in self.watch:
                out[chat] = out.get(chat, 0) + 1
            return list(out.items())
        if "FROM watchlist w" in sql:        # stocks.WATCH_SQL — scoped by its single parameter
            return [(sym, q, c, note, "Name", "INR", None, None, None, None)
                    for (chat, sym), (q, c, note) in sorted(self.watch.items()) if chat == args[0]]
        raise AssertionError(f"unscripted query: {sql[:70]}")

    def execute(self, sql, args=()):
        self.writes.append((sql, args))
        if sql.startswith("INSERT INTO bot_users"):
            chat, name, _by = args
            self.members[chat] = [name, True]
            return 1
        if sql.startswith("UPDATE bot_users"):
            row = self.members.get(args[0])
            if row and row[1]:
                row[1] = False
                return 1
            return 0
        if sql.startswith("INSERT INTO watchlist"):
            chat, sym, qty, cost, note = args
            old = self.watch.get((chat, sym), (None, None, None))
            self.watch[(chat, sym)] = (qty if qty is not None else old[0],
                                       cost if cost is not None else old[1],
                                       note if note is not None else old[2])
            return 1
        if sql.startswith("DELETE FROM watchlist"):
            return 1 if self.watch.pop((args[0], args[1]), None) is not None else 0
        raise AssertionError(f"unscripted write: {sql[:70]}")


class UsersModuleTest(unittest.TestCase):
    def test_chat_id_validation(self):
        for good in ("123456789", "-1001234567890", "1" * 20):
            self.assertTrue(users.valid_chat_id(good), good)
        for bad in ("", "abc", "12", "12 34", "1; DROP TABLE x", "-", "--1234567", None, "1" * 21):
            self.assertFalse(users.valid_chat_id(bad), bad)

    def test_parse_adduser(self):
        self.assertEqual(users.parse_adduser(f"/adduser {ALICE} Alice  Smith"),
                         (ALICE, "Alice Smith", None))
        self.assertEqual(users.parse_adduser(f"/adduser {ALICE}")[1], "user")
        self.assertEqual(users.parse_adduser(f"/adduser {ALICE} {'x' * 90}")[1], "x" * 40)
        self.assertIsNotNone(users.parse_adduser("/adduser")[2])
        self.assertIn("does not look like", users.parse_adduser("/adduser bob")[2])

    def test_parse_rmuser(self):
        self.assertEqual(users.parse_rmuser(f"/rmuser {ALICE}"), (ALICE, None))
        self.assertIsNotNone(users.parse_rmuser("/rmuser")[1])
        self.assertIsNotNone(users.parse_rmuser(f"/rmuser {ALICE} extra")[1])

    def test_registry_owner_always_allowed_members_from_table(self):
        db = FakeDB()
        db.members[ALICE] = ["Alice", True]
        reg = users.Registry(OWNER, db.query, ttl=0)
        self.assertTrue(reg.allowed(OWNER))
        self.assertTrue(reg.allowed(ALICE))
        self.assertFalse(reg.allowed(STRANGER))
        self.assertFalse(reg.allowed(""))
        self.assertTrue(reg.is_owner(OWNER) and not reg.is_owner(ALICE))
        self.assertEqual(reg.recipients(), [(OWNER, "owner"), (ALICE, "Alice")])

    def test_inactive_member_is_not_allowed(self):
        db = FakeDB()
        db.members[ALICE] = ["Alice", False]
        self.assertFalse(users.Registry(OWNER, db.query, ttl=0).allowed(ALICE))

    def test_missing_table_degrades_to_owner_only_without_error(self):
        class UndefinedTable(Exception):
            pass

        def boom(sql, args=()):
            raise UndefinedTable("bot_users")
        logs = []
        reg = users.Registry(OWNER, boom, ttl=0, log=logs.append)
        self.assertTrue(reg.allowed(OWNER))
        self.assertFalse(reg.allowed(ALICE))
        self.assertEqual(reg.recipients(), [(OWNER, "owner")])
        self.assertEqual(logs, [])   # the expected pre-migration case is silent

    def test_db_blip_keeps_last_known_members_and_never_locks_owner_out(self):
        db = FakeDB()
        db.members[ALICE] = ["Alice", True]
        state = {"fail": False}

        def flaky(sql, args=()):
            if state["fail"]:
                raise RuntimeError("connection reset")
            return db.query(sql, args)
        logs = []
        reg = users.Registry(OWNER, flaky, ttl=0, log=logs.append)
        self.assertTrue(reg.allowed(ALICE))
        state["fail"] = True
        self.assertTrue(reg.allowed(ALICE))     # stale cache, not locked out
        self.assertTrue(reg.allowed(OWNER))
        self.assertTrue(any("last known" in m for m in logs))

    def test_cache_ttl_and_invalidate(self):
        db = FakeDB()
        clock = [0.0]
        reg = users.Registry(OWNER, db.query, ttl=30, clock=lambda: clock[0])
        self.assertFalse(reg.allowed(ALICE))
        db.members[ALICE] = ["Alice", True]
        self.assertFalse(reg.allowed(ALICE))     # still cached
        clock[0] = 31
        self.assertTrue(reg.allowed(ALICE))      # expired
        db.members[BOB] = ["Bob", True]
        reg.invalidate()
        self.assertTrue(reg.allowed(BOB))

    def test_add_and_remove_user(self):
        db = FakeDB()
        reg = users.Registry(OWNER, db.query, ttl=300)
        self.assertIn("Added", users.add_user(db.execute, reg, ALICE, "Alice", OWNER))
        self.assertTrue(reg.allowed(ALICE))      # visible at once despite the long TTL
        self.assertIn("not an active", users.remove_user(db.execute, reg, BOB))
        self.assertIn("Removed", users.remove_user(db.execute, reg, ALICE))
        self.assertFalse(reg.allowed(ALICE))
        self.assertIn("owner chat", users.add_user(db.execute, reg, OWNER, "me", OWNER))
        self.assertIn("cannot be removed", users.remove_user(db.execute, reg, OWNER))

    def test_user_limit(self):
        db = FakeDB()
        reg = users.Registry(OWNER, db.query, ttl=0)
        for i in range(users.MAX_MEMBERS):
            users.add_user(db.execute, reg, str(500000000 + i), f"u{i}", OWNER)
        self.assertIn("limit", users.add_user(db.execute, reg, "600000006", "late", OWNER))
        # an existing member can still be renamed at the cap
        self.assertIn("Added", users.add_user(db.execute, reg, "500000000", "renamed", OWNER))

    def test_names_are_html_escaped(self):
        db = FakeDB()
        reg = users.Registry(OWNER, db.query, ttl=0)
        out = users.add_user(db.execute, reg, ALICE, "<b>x</b>", OWNER)
        self.assertNotIn("<b>x</b>", out)
        self.assertIn("&lt;b&gt;", out)

    def test_users_text_counts_symbols_but_never_holdings(self):
        db = FakeDB()
        db.members[ALICE] = ["Alice", True]
        db.watch[(ALICE, "TCS.NS")] = (10, 3500.0, "secret note")
        db.watch[(OWNER, "AAPL")] = (None, None, None)
        db.watch[(OWNER, "MSFT")] = (None, None, None)
        text = users.users_text(users.Registry(OWNER, db.query, ttl=0), db.query)
        self.assertIn("2 watched symbols", text)
        self.assertIn("1 watched symbol", text)
        for leaked in ("3500", "secret note", "TCS", "AAPL"):
            self.assertNotIn(leaked, text)

    def test_access_request_rate_limits(self):
        clock = [1000.0]
        req = users.AccessRequests(clock=lambda: clock[0])
        self.assertTrue(req.should_notify(STRANGER))
        self.assertFalse(req.should_notify(STRANGER))             # same chat, same day
        clock[0] += users.REQUEST_COOLDOWN + 1
        self.assertTrue(req.should_notify(STRANGER))               # next day: once more
        limit = users.REQUEST_BURST[0]
        sent = sum(req.should_notify(str(700000000 + i)) for i in range(limit + 10))
        self.assertLessEqual(sent, limit)                          # spam from many chats is capped

    def test_request_notice_gives_the_adduser_line_and_escapes(self):
        text = users.request_notice(STRANGER, "evil<script>", "/start <b>")
        self.assertIn(f"/adduser {STRANGER}", text)
        self.assertNotIn("<script>", text)
        self.assertIn("group", users.request_notice(GROUP, None, "/status"))
        self.assertIn("a user", users.request_notice(STRANGER, "12345", "x"))   # numeric username


class DeliverTest(unittest.TestCase):
    def run_deliver(self, recipients, fail=(), none=(), boom=()):
        sent, logs = [], []

        def make(chat):
            if chat in boom:
                raise RuntimeError("db gone")
            return None if chat in none else f"msg-{chat}"

        def send(text, chat):
            if chat in fail:
                return False
            sent.append((chat, text))
            return True
        rc = users.deliver(recipients, OWNER, make, send, log=logs.append)
        return rc, sent, logs

    R = [(OWNER, "owner"), (ALICE, "Alice"), (BOB, "Bob")]

    def test_everyone_gets_their_own_message(self):
        rc, sent, _ = self.run_deliver(self.R)
        self.assertEqual(rc, 0)
        self.assertEqual(sent, [(OWNER, f"msg-{OWNER}"), (ALICE, f"msg-{ALICE}"), (BOB, f"msg-{BOB}")])

    def test_a_member_who_blocked_the_bot_does_not_fail_the_job_or_the_others(self):
        rc, sent, logs = self.run_deliver(self.R, fail=(ALICE,))
        self.assertEqual(rc, 0)
        self.assertEqual([c for c, _ in sent], [OWNER, BOB])
        self.assertTrue(any("Alice" in m for m in logs))

    def test_owner_send_failure_fails_the_job(self):
        rc, sent, _ = self.run_deliver(self.R, fail=(OWNER,))
        self.assertEqual(rc, 1)
        self.assertEqual([c for c, _ in sent], [ALICE, BOB])       # members still served

    def test_everything_failing_fails_the_job(self):
        self.assertEqual(self.run_deliver(self.R, fail=(OWNER, ALICE, BOB))[0], 1)

    def test_nothing_to_send_is_success(self):
        self.assertEqual(self.run_deliver(self.R, none=(OWNER, ALICE, BOB)), (0, [], []))

    def test_a_build_error_for_one_member_is_isolated(self):
        rc, sent, logs = self.run_deliver(self.R, boom=(ALICE,))
        self.assertEqual(rc, 0)
        self.assertEqual([c for c, _ in sent], [OWNER, BOB])
        self.assertTrue(any("ERROR building" in m for m in logs))

    def test_a_build_error_for_the_owner_fails_the_job(self):
        self.assertEqual(self.run_deliver(self.R, boom=(OWNER,))[0], 1)

    def test_recipients_for_jobs_owner_first_and_missing_table_ok(self):
        db = FakeDB()
        db.members[ALICE] = ["Alice", True]
        self.assertEqual(users.recipients_for_jobs(db.query, OWNER), [(OWNER, "owner"), (ALICE, "Alice")])

        class UndefinedTable(Exception):
            pass

        def boom(sql, args=()):
            raise UndefinedTable()
        self.assertEqual(users.recipients_for_jobs(boom, OWNER), [(OWNER, "owner")])


class PerUserSqlTest(unittest.TestCase):
    def test_watch_sql_is_scoped_to_one_chat(self):
        self.assertIn("WHERE w.chat_id = %s", stocks.WATCH_SQL)
        self.assertEqual(stocks.WATCH_SQL.count("%s"), 1)

    def test_watch_text_passes_the_chat(self):
        seen = []

        def q(sql, args=()):
            seen.append(args)
            return []
        stocks.watch_text(q, ALICE)
        self.assertEqual(seen, [(ALICE,)])

    def test_undefined_column_counts_as_missing_schema(self):
        class UndefinedColumn(Exception):
            pass
        self.assertTrue(stocks.table_missing(UndefinedColumn()))
        self.assertTrue(users.table_missing(UndefinedColumn()))

    def test_alert_split_common_has_no_watchlist_query_and_personal_is_scoped(self):
        calls = []

        def q(sql, args=()):
            calls.append((sql, args))
            return []
        self.assertEqual(stocks.alert_common(q, NOW), [])
        self.assertFalse(any("watchlist" in s for s, _ in calls))
        calls.clear()
        self.assertEqual(stocks.alert_watch(q, ALICE, NOW), [])
        self.assertTrue(any(a == (ALICE,) and "watchlist" in s for s, a in calls))


class BotFlowTest(unittest.TestCase):
    """bot.handle() with a fake DB: who is answered, what they see, who may manage users."""

    def setUp(self):
        self.db = FakeDB()
        self.out = []
        self._saved = (bot.query, bot.execute, bot.send, bot.REGISTRY, bot.REQUESTS,
                       stocks.pick_one, stocks.ytd_map, stocks.pick_one_ex)
        bot.query, bot.execute = self.db.query, self.db.execute
        bot.send = lambda text, chat=None: self.out.append((str(chat or bot.OWNER), text))
        bot.REGISTRY = users.Registry(OWNER, self.db.query, ttl=0)
        bot.REQUESTS = users.AccessRequests(clock=lambda: 5000.0)
        stocks.pick_one = lambda q, arg: ((arg.upper() + ".NS", arg.upper()), None)
        stocks.pick_one_ex = lambda q, arg, asker=None: ((arg.upper() + ".NS", arg.upper()), None, None)
        stocks.ytd_map = lambda q, symbols: {}

    def tearDown(self):
        (bot.query, bot.execute, bot.send, bot.REGISTRY, bot.REQUESTS,
         stocks.pick_one, stocks.ytd_map, stocks.pick_one_ex) = self._saved

    def say(self, chat, text, uname="someone"):
        bot.handle({"message": {"chat": {"id": int(chat)}, "text": text,
                                "from": {"username": uname, "id": int(chat)}}})

    def replies(self, chat):
        return [t for c, t in self.out if c == chat]

    def test_unknown_chat_is_never_answered_but_the_owner_is_told_once(self):
        self.say(STRANGER, "/status", "mallory")
        self.say(STRANGER, "/index", "mallory")
        self.assertEqual(self.replies(STRANGER), [])
        notices = self.replies(OWNER)
        self.assertEqual(len(notices), 1)
        self.assertIn(f"/adduser {STRANGER}", notices[0])

    def test_a_stranger_cannot_add_themselves_or_anyone(self):
        self.say(STRANGER, f"/adduser {STRANGER} me")
        self.say(STRANGER, f"/adduser {ALICE} Alice")
        self.assertEqual(self.db.members, {})
        self.assertEqual(self.replies(STRANGER), [])
        self.assertEqual(self.db.writes, [])

    def test_empty_event_from_a_group_does_not_notify(self):
        self.say(GROUP, "")                      # e.g. the bot being added to a group
        self.assertEqual(self.out, [])

    def test_member_gets_answers_in_their_own_chat_only(self):
        self.say(OWNER, f"/adduser {ALICE} Alice")
        self.out.clear()
        self.say(ALICE, "/help")
        self.assertEqual(len(self.replies(ALICE)), 1)
        self.assertEqual(self.replies(OWNER), [])

    def test_help_shows_admin_line_to_owner_only(self):
        self.say(OWNER, f"/adduser {ALICE} Alice")
        self.out.clear()
        self.say(OWNER, "/help")
        self.say(ALICE, "/help")
        self.assertIn("manage who has access", self.replies(OWNER)[0])
        self.assertNotIn("manage who has access", self.replies(ALICE)[0])

    def test_corrected_symbol_never_writes_to_a_watchlist(self):
        stocks.pick_one_ex = lambda q, arg, asker=None: (("INFY.NS", "Infosys Ltd."), None,
                                                         "🔎 <i>“infosis” → <b>INFY.NS</b></i>")
        self.say(OWNER, "/watch add infosis 10 1500")
        self.assertEqual(self.db.watch, {})                          # nothing written
        self.assertIn("/watch add INFY.NS", self.out[-1][1])         # asks to confirm with the exact symbol

    def test_watch_without_add_verb_resolves_and_watches_stock(self):
        stocks.pick_one_ex = lambda q, arg, asker=None: (("TSLA", "Tesla, Inc."), None, None)
        self.say(OWNER, "/watch tesla")
        self.assertIn((OWNER, "TSLA"), self.db.watch)
        self.assertIn("Watching <b>Tesla, Inc.</b> (TSLA).", self.out[-1][1])

    def test_wrong_command_notifies_user_and_runs_target_command(self):
        orig_card = stocks.stock_card
        stocks.stock_card = lambda q, row: f"<b>{row[1]}</b> ({row[0]})"
        stocks.pick_one_ex = lambda q, arg, asker=None: (("TCS.NS", "TCS Ltd.", "IT", "NIFTY500", "INR"), None, None)
        try:
            # 1. Misspelled command (/stok TCS -> /stock TCS)
            self.say(OWNER, "/stok TCS")
            reply1 = self.out[-1][1]
            self.assertIn("<code>/stok</code> is not a command — running <code>/stock TCS</code>", reply1)
            self.assertIn("TCS Ltd.", reply1)
            # 2. Wrong command for argument (/sectors tcs -> /stock TCS.NS)
            self.say(OWNER, "/sectors tcs")
            reply2 = self.out[-1][1]
            self.assertIn("<code>/sectors</code> cannot handle <code>tcs</code> — running <code>/stock TCS.NS</code>", reply2)
            self.assertIn("TCS Ltd.", reply2)
            # 3. No-arg command given a stock (/status tcs -> /stock TCS.NS)
            self.say(OWNER, "/status tcs")
            reply3 = self.out[-1][1]
            self.assertIn("<code>/status</code> takes no stock argument — running <code>/stock TCS.NS</code>", reply3)
            self.assertIn("TCS Ltd.", reply3)
        finally:
            stocks.stock_card = orig_card

    def test_watchlists_are_private_per_chat(self):
        self.say(OWNER, f"/adduser {ALICE} Alice")
        self.say(OWNER, f"/adduser {BOB} Bob")
        self.out.clear()
        self.say(ALICE, "/watch add tcs 10 3500 secret plan")
        self.say(BOB, "/watch add infy")
        self.say(ALICE, "/watch")
        self.say(BOB, "/watch")
        self.say(OWNER, "/watch")
        alice, bob, owner = self.replies(ALICE)[-1], self.replies(BOB)[-1], self.replies(OWNER)[-1]
        self.assertIn("TCS", alice)
        self.assertNotIn("INFY", alice)
        self.assertIn("INFY", bob)
        self.assertNotIn("TCS", bob)
        self.assertIn("Watchlist is empty", owner)
        self.assertEqual(set(self.db.watch), {(ALICE, "TCS.NS"), (BOB, "INFY.NS")})

    def test_two_users_can_watch_the_same_stock_and_remove_independently(self):
        self.say(OWNER, f"/adduser {ALICE} Alice")
        self.out.clear()
        self.say(OWNER, "/watch add tcs")
        self.say(ALICE, "/watch add tcs 5 3600")
        self.assertEqual(set(self.db.watch), {(OWNER, "TCS.NS"), (ALICE, "TCS.NS")})
        self.say(ALICE, "/watch rm tcs")
        self.assertEqual(set(self.db.watch), {(OWNER, "TCS.NS")})
        self.say(ALICE, "/watch rm tcs")
        self.assertIn("was not on the watchlist", self.replies(ALICE)[-1])

    def test_member_cannot_manage_users(self):
        self.say(OWNER, f"/adduser {ALICE} Alice")
        self.out.clear()
        for cmd in (f"/adduser {BOB} Bob", f"/rmuser {OWNER}", "/users"):
            self.say(ALICE, cmd)
        self.assertEqual(self.replies(ALICE), ["That command is for the owner only."] * 3)
        self.assertNotIn(BOB, self.db.members)

    def test_owner_adds_lists_and_removes(self):
        self.say(OWNER, f"/adduser {ALICE} Alice")
        self.say(OWNER, "/users")
        self.assertIn("Alice", self.replies(OWNER)[-1])
        self.say(ALICE, "/help")        # allowed now
        self.assertTrue(self.replies(ALICE))
        self.out.clear()
        self.say(OWNER, f"/rmuser {ALICE}")
        self.say(ALICE, "/help")
        self.assertEqual(self.replies(ALICE), [])      # removed: silent again

    def test_removed_user_gets_their_watchlist_back_when_readded(self):
        self.say(OWNER, f"/adduser {ALICE} Alice")
        self.say(ALICE, "/watch add tcs")
        self.say(OWNER, f"/rmuser {ALICE}")
        self.say(OWNER, f"/adduser {ALICE} Alice")
        self.out.clear()
        self.say(ALICE, "/watch")
        self.assertIn("TCS", self.replies(ALICE)[-1])

    def test_a_group_can_be_a_member_but_is_a_shared_setup(self):
        self.say(OWNER, f"/adduser {GROUP} Family")
        self.out.clear()
        self.say(GROUP, "/help")
        self.assertEqual(len(self.replies(GROUP)), 1)

    def test_runbook_one_liners_still_work_without_a_chat(self):
        self.assertIn("Watchlist is empty", bot.COMMANDS["watch"]("/watch"))
        self.assertIn("Marketfredbot", bot.COMMANDS["help"]("/help"))


class MenuSyncTest(unittest.TestCase):
    """The Telegram command menu (setMyCommands) follows the code — v28's new
    commands were missing because MENU was not updated with COMMANDS."""

    ALIASES = {"start", "mb", "usdinr"}      # same handler under another name
    HIDDEN = {"users", "adduser", "rmuser"}  # owner-only, deliberately unlisted

    def test_every_menu_entry_is_a_real_command(self):
        for cmd, _desc in bot.MENU:
            self.assertIn(cmd, bot.COMMANDS)

    def test_every_user_command_is_in_the_menu(self):
        menu = {c for c, _ in bot.MENU}
        missing = set(bot.COMMANDS) - menu - self.ALIASES - self.HIDDEN
        self.assertEqual(missing, set(), f"commands missing from MENU: {missing}")



if __name__ == "__main__":
    unittest.main(verbosity=1)
