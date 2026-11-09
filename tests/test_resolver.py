#!/usr/bin/env python3
"""Forgiving symbol lookup (resolver.py) against a REAL universe sample (public data: every query's top-6 candidates from the 998-row universe,
tests/fixtures/universe_sample.json, captured 2026-10-18 — fuzzy decisions are identical to the full universe). No network, no DB.

    python3 tests/test_resolver.py
"""
import json
import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
pg = types.ModuleType("psycopg")
pg.Error = type("Error", (Exception,), {})
sys.modules.setdefault("psycopg", pg)
sys.modules.setdefault("requests", types.ModuleType("requests"))
sys.path.insert(0, str(ROOT / "manifests" / "telegram"))
import resolver  # noqa: E402
import stocks  # noqa: E402

ROWS = [tuple(r) for r in json.loads((ROOT / "tests" / "fixtures" / "universe_sample.json").read_text())]


def q(sql, args=()):
    """Fake query: universe for the resolver; nothing for stocks.resolve's exact/ILIKE lookups."""
    return ROWS if "WHERE active ORDER BY symbol" in sql else []


class Fuzzy(unittest.TestCase):
    def setUp(self):
        resolver._universe.update(rows=None, at=0.0)
        resolver._ai_cache.clear()
        resolver._nickname_cache.clear()
        os.environ.pop("BOT_AI_RESOLVE", None)

    def auto(self, text):
        row, how = resolver.suggest(q, text)
        return (row[0], how) if row else (None, how)

    def test_nickname_map_resolves_before_fuzzy(self):
        for text, sym in [("reliance", "RELIANCE.NS"), ("tcs", "TCS.NS"),
                          ("hdfc bank", "HDFCBANK.NS"), ("nifty", "^NSEI")]:
            self.assertEqual(self.auto(text), (sym, "nickname"), text)

    def test_clear_misspellings_resolve_directly(self):
        for text, sym in [("infosis", "INFY.NS"), ("microsft", "MSFT"),
                          ("amazn", "AMZN"), ("bajaj finanse", "BAJFINANCE.NS")]:
            self.assertEqual(self.auto(text), (sym, "fuzzy"), text)

    def test_ambiguous_queries_ask_instead_of_guessing(self):
        for text, must_list in [("icici", "ICICIBANK.NS"), ("bajaj", "BAJAJFINSV.NS"),
                                ("relaince", "RELIANCE.NS")]:
            row, msg = resolver.suggest(q, text)
            self.assertIsNone(row, text)
            self.assertIn("Did you mean", msg)
            self.assertIn(must_list, msg)

    def test_short_text_is_never_auto_picked(self):
        row, msg = resolver.suggest(q, "coke")            # live 2026-10-19: was auto-picked as OKE
        self.assertIsNone(row)

    def test_nonsense_gives_no_match(self):
        self.assertEqual(resolver.suggest(q, "qwxzv"), (None, None))


class Ai(unittest.TestCase):
    def setUp(self):
        resolver._universe.update(rows=None, at=0.0)
        resolver._ai_cache.clear()
        os.environ.update(BOT_AI_RESOLVE="1", LLM_API_KEY="test")

    def tearDown(self):
        for k in ("BOT_AI_RESOLVE", "LLM_API_KEY"):
            os.environ.pop(k, None)

    def test_nickname_resolved_without_ai(self):
        calls = []

        def asker(prompt):
            calls.append(prompt)
            return "AAPL", "Apple Inc."
        # nickname resolves without calling AI
        self.assertEqual(resolver.suggest(q, "reliance", asker)[0][0], "RELIANCE.NS")
        self.assertEqual(len(calls), 0)                  # nickname, no AI call
        # but a non-nickname still uses AI and caches
        self.assertEqual(resolver.suggest(q, "aple", asker)[0][0], "AAPL")
        self.assertEqual(len(calls), 1)
        resolver.suggest(q, "aple", asker)
        self.assertEqual(len(calls), 1)                  # cached

    def test_ai_answer_must_be_a_real_universe_symbol(self):
        self.assertIsNone(resolver.ai_pick("x", [], ROWS, lambda p: ("NOTREAL", "Not Real Corp")))
        self.assertEqual(resolver.ai_pick("eternal", [], ROWS, lambda p: ("ETERNAL", "Eternal Ltd"))[0], "ETERNAL.NS")

    def test_ticker_and_company_must_agree(self):
        # live 2026-10-19: "big blue" -> SBILIFE.NS was a real symbol but the wrong company
        self.assertIsNone(resolver.ai_pick("big blue", [], ROWS, lambda p: ("AAPL", "International Business Machines")))
        resolver._ai_cache.clear()
        self.assertEqual(resolver.ai_pick("big blue", [], ROWS,
                                          lambda p: ("IBM", "International Business Machines Corporation"))[0], "IBM")

    def test_stale_ticker_resolved_through_company_name(self):
        self.assertEqual(resolver.ai_pick("facebook", [], ROWS, lambda p: ("FB", "Meta Platforms"))[0], "META")
        self.assertIsNone(resolver.ai_pick("zomato", [], ROWS, lambda p: ("ZOMATO.NS", "Zomato Limited")))

    def test_prompt_has_no_hints(self):
        seen = []
        resolver.ai_pick("aple", [(0.9, ROWS[0])], ROWS, lambda p: seen.append(p) or (None, None))
        self.assertNotIn(ROWS[0][1], seen[0])

    def test_gateway_down_falls_back_to_suggestions(self):
        def down(prompt):
            raise ConnectionError("gateway down")
        row, msg = resolver.suggest(q, "aple", down)
        self.assertIsNone(row)
        self.assertIn("AAPL", msg)

    def test_disabled_means_no_call(self):
        os.environ["BOT_AI_RESOLVE"] = "0"
        self.assertIsNone(resolver.ai_pick("facebook", [], ROWS, lambda p: self.fail("called")))

    def test_only_the_typed_text_is_sent(self):
        seen = []
        resolver.ai_pick("big blue", [], ROWS, lambda p: seen.append(p) or ("IBM", "IBM"))
        self.assertIn('"big blue"', seen[0])
        self.assertNotIn("chat", seen[0].lower())


class PickOne(unittest.TestCase):
    def setUp(self):
        resolver._universe.update(rows=None, at=0.0)
        os.environ.pop("BOT_AI_RESOLVE", None)

    def test_corrected_match_carries_a_note(self):
        row, problem, note = stocks.pick_one_ex(q, "infosis")
        self.assertEqual(row[0], "INFY.NS")
        self.assertIsNone(problem)
        self.assertIn("infosis", note)

    def test_pick_one_keeps_its_old_contract(self):
        row, problem = stocks.pick_one(q, "microsft")
        self.assertEqual((row[0], problem), ("MSFT", None))


if __name__ == "__main__":
    unittest.main(verbosity=1)
