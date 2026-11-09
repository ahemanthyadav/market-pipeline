#!/usr/bin/env python3
"""Tests for manifests/telegram/router.py — natural-language command router.

No database, no network: pure string matching.

    python3 tests/test_router.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "manifests" / "telegram"))
import router  # noqa: E402

# a slice of bot.COMMANDS — enough for the exact-command cases below
COMMANDS = {"status", "digest", "help", "movers", "news", "watch", "top", "stock", "route"}


class RouteTest(unittest.TestCase):
    def test_exact_command(self):
        cmd, args, conf = router.route("/stock TCS")
        self.assertEqual(cmd, "stock")
        self.assertEqual(args, "TCS")
        self.assertEqual(conf, "exact")

    def test_price_of(self):
        cmd, args, conf = router.route("price of TCS")
        self.assertEqual(cmd, "stock")
        self.assertEqual(args, "TCS")
        self.assertEqual(conf, "phrase")

    def test_price_for(self):
        cmd, args, conf = router.route("quote for reliance")
        self.assertEqual(cmd, "stock")
        self.assertEqual(args, "reliance")
        self.assertEqual(conf, "phrase")

    def test_tcs_price_keyword_first(self):
        cmd, args, conf = router.route("tcs price")
        # "tcs" is both a nickname (TCS.NS) and keyword context; nickname wins
        self.assertEqual(cmd, "stock")
        self.assertEqual(args, "TCS.NS")
        self.assertEqual(conf, "nickname")

    def test_price_tcs_keyword_second(self):
        cmd, args, conf = router.route("price tcs")
        self.assertEqual(cmd, "stock")
        self.assertEqual(args, "TCS.NS")
        self.assertEqual(conf, "keyword")

    def test_reliance_nickname(self):
        cmd, args, conf = router.route("reliance price")
        self.assertEqual(cmd, "stock")
        self.assertEqual(args, "RELIANCE.NS")
        self.assertEqual(conf, "nickname")

    def test_nifty_keyword(self):
        cmd, args, conf = router.route("nifty level")
        # "nifty level" matches a phrase route → /nifty command
        self.assertEqual(cmd, "nifty")
        self.assertEqual(args, "")
        self.assertEqual(conf, "phrase")

    def test_fii_flows_phrase(self):
        cmd, args, conf = router.route("show me fii flows")
        self.assertEqual(cmd, "fii")
        self.assertEqual(args, "")
        self.assertEqual(conf, "phrase")

    def test_my_watchlist_phrase(self):
        cmd, args, conf = router.route("my watchlist")
        self.assertEqual(cmd, "watch")
        self.assertEqual(args, "")
        self.assertEqual(conf, "phrase")

    def test_top_growth_sp500_phrase(self):
        cmd, args, conf = router.route("top growth sp500")
        self.assertEqual(cmd, "top")
        self.assertEqual(args, "growth sp500")
        self.assertEqual(conf, "phrase")

    def test_daily_digest_phrase(self):
        cmd, args, conf = router.route("daily digest")
        self.assertEqual(cmd, "digest")
        self.assertEqual(args, "")
        self.assertEqual(conf, "phrase")

    def test_single_word_nickname(self):
        cmd, args, conf = router.route("reliance")
        self.assertEqual(cmd, "stock")
        self.assertEqual(args, "RELIANCE.NS")
        self.assertEqual(conf, "nickname")

    def test_single_word_keyword(self):
        cmd, args, conf = router.route("vix")
        self.assertEqual(cmd, "vix")
        self.assertEqual(args, "")
        self.assertEqual(conf, "keyword")

    def test_unknown_returns_none(self):
        cmd, args, conf = router.route("random gibberish xyz")
        self.assertIsNone(cmd)
        self.assertIsNone(args)
        self.assertIsNone(conf)

    def test_empty_returns_none(self):
        cmd, args, conf = router.route("")
        self.assertIsNone(cmd)
        self.assertIsNone(args)
        self.assertIsNone(conf)

    def test_format_route(self):
        out = router.format_route("stock", "RELIANCE.NS", "nickname")
        self.assertIn("/stock", out)
        self.assertIn("RELIANCE.NS", out)
        self.assertIn("nickname", out)

    # --- universe lookup (DB-backed layer, injected) -----------------------
    seen = []

    UNIVERSE = {"cub": "CUB.NS", "city union": "CUB.NS", "karnataka bank": "CUB.NS"}

    @classmethod
    def lookup(cls, text):
        """Fake universe resolver; records the queries it saw."""
        cls.seen.append(text)
        return cls.UNIVERSE.get(text.lower())

    def setUp(self):
        type(self).seen = []

    def test_no_argument_command_with_symbol_routes_to_stock(self):
        for text in ("/status cub", "/movers cub", "/digest cub"):
            cmd, args, conf = router.route(text, self.lookup, COMMANDS)
            self.assertEqual((cmd, args, conf), ("stock", "CUB.NS", "lookup"), text)

    def test_bare_unknown_symbol_routes_to_stock(self):
        cmd, args, conf = router.route("cub", self.lookup)
        self.assertEqual((cmd, args, conf), ("stock", "CUB.NS", "lookup"))

    def test_company_name_routes_to_stock(self):
        cmd, args, conf = router.route("karnataka bank", self.lookup, COMMANDS)
        self.assertEqual((cmd, args, conf), ("stock", "CUB.NS", "lookup"))

    def test_longest_nickname_prefix_wins(self):
        # "tata motors" is a nickname; "tata motors pv" must not be cut to it
        self.assertEqual(router.route("tata motors pv")[:2], ("stock", "TMPV.NS"))
        self.assertEqual(router.route("tata motors")[:2], ("stock", "TMCV.NS"))
        self.assertEqual(router.route("tata motors pv price")[:2], ("stock", "TMPV.NS"))

    def test_stale_nickname_now_points_at_live_symbol(self):
        # "city union" used to map to CITYUNION.NS, which the universe never had
        cmd, args, conf = router.route("city union")
        self.assertEqual((cmd, args), ("stock", "CUB.NS"))

    def test_argument_commands_keep_their_args(self):
        self.assertEqual(router.route("/news cub", self.lookup), ("news", "cub", "exact"))
        self.assertEqual(router.route("/watch cub", self.lookup), ("watch", "cub", "exact"))
        self.assertEqual(router.route("/top cub", self.lookup), ("top", "cub", "exact"))

    def test_filler_words_never_hit_the_lookup(self):
        self.assertEqual(router.route("/status me", self.lookup, COMMANDS), ("status", "me", "exact"))
        self.assertEqual(router.route("/digest please", self.lookup), ("digest", "please", "exact"))
        self.assertEqual(self.seen, [], f"lookup called for filler: {self.seen}")

    def test_lookup_errors_are_swallowed(self):
        def boom(_text):
            raise RuntimeError("db down")
        self.assertEqual(router.route("cub", boom), (None, None, None))

    def test_lookup_none_keeps_old_behaviour(self):
        self.assertEqual(router.route("cub"), (None, None, None))

    def test_symbol_from_args(self):
        self.assertEqual(router.symbol_from_args("status", "cub", self.lookup), "CUB.NS")
        self.assertIsNone(router.symbol_from_args("news", "cub", self.lookup))
        self.assertIsNone(router.symbol_from_args("status", "", self.lookup))
        self.assertIsNone(router.symbol_from_args("status", "please", self.lookup))

    def test_question_routes_to_ask(self):
        for q, expected_arg in [
            ("tcs outlook", "tcs outlook"),
            ("why did infosys fall?", "why did infosys fall"),
            ("which sectors are cheapest?", "which sectors are cheapest"),
            ("explain market regime", "explain market regime"),
        ]:
            cmd, args, conf = router.route(q, self.lookup, COMMANDS)
            self.assertEqual((cmd, args, conf), ("ask", expected_arg, "question"), q)

    def test_fuzzy_slash_command_and_slash_ticker(self):
        self.assertEqual(router.route("/stok TCS", self.lookup, COMMANDS), ("stock", "TCS", "fuzzy_cmd"))
        self.assertEqual(router.route("/digst", self.lookup, COMMANDS), ("digest", "", "fuzzy_cmd"))
        self.assertEqual(router.route("/tcs", self.lookup, COMMANDS), ("stock", "TCS.NS", "nickname"))

    def test_ai_route_fallback_and_safety(self):
        router._ai_route_cache.clear()
        cmd, args, conf = router.ai_route("yield curve spread", COMMANDS | {"macro"},
                                          asker=lambda p: ("macro", ""))
        self.assertEqual((cmd, args, conf), ("macro", "", "ai"))
        # AI router must never allow admin commands or watchlist mutations
        self.assertEqual(router.ai_route("hack", COMMANDS, asker=lambda p: ("adduser", "123 bob")),
                         (None, None, None))
        self.assertEqual(router.ai_route("watch", COMMANDS, asker=lambda p: ("watch", "add TCS")),
                         (None, None, None))


if __name__ == "__main__":
    unittest.main()
