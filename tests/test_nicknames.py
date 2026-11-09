#!/usr/bin/env python3
"""Tests for manifests/telegram/nicknames.py — nickname → symbol map.

No database, no network.

    python3 tests/test_nicknames.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "manifests" / "telegram"))
import nicknames  # noqa: E402


class NicknameTest(unittest.TestCase):
    def test_known_india_nickname(self):
        self.assertEqual(nicknames.NICKNAMES["reliance"], "RELIANCE.NS")
        self.assertEqual(nicknames.NICKNAMES["tcs"], "TCS.NS")
        self.assertEqual(nicknames.NICKNAMES["hdfc bank"], "HDFCBANK.NS")
        self.assertEqual(nicknames.NICKNAMES["infosys"], "INFY.NS")

    def test_index_nickname(self):
        self.assertEqual(nicknames.NICKNAMES["nifty"], "^NSEI")
        self.assertEqual(nicknames.NICKNAMES["sensex"], "^BSESN")

    def test_fx_nickname(self):
        self.assertEqual(nicknames.NICKNAMES["usdinr"], "USDINR=X")

    def test_no_short_symbols(self):
        """No symbol value should be <= 5 chars (to avoid secret-value collisions)."""
        for sym in nicknames.NICKNAMES.values():
            self.assertGreaterEqual(len(sym), 5, f"Symbol {sym} is too short")

    def test_command_aliases(self):
        self.assertEqual(nicknames.COMMAND_ALIASES["price"], "stock")
        self.assertEqual(nicknames.COMMAND_ALIASES["news"], "news")
        self.assertEqual(nicknames.COMMAND_ALIASES["fii"], "fii")

    def test_keyword_commands(self):
        self.assertEqual(nicknames.KEYWORD_COMMANDS["stock"], "stock")
        self.assertEqual(nicknames.KEYWORD_COMMANDS["vix"], "vix")
        self.assertEqual(nicknames.KEYWORD_COMMANDS["watch"], "watch")

    def test_phrase_routes(self):
        self.assertEqual(nicknames.PHRASE_ROUTES["price of"], ("stock", "{arg}"))
        self.assertEqual(nicknames.PHRASE_ROUTES["my watchlist"], ("watch", ""))
        self.assertEqual(nicknames.PHRASE_ROUTES["top growth"], ("top", "growth {arg}"))


if __name__ == "__main__":
    unittest.main()
