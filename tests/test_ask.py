#!/usr/bin/env python3
"""ask.py: fact-gathering, the number/direction gate, and the degrade-to-facts path. No network, no DB.

    python3 tests/test_ask.py
"""
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
import ask  # noqa: E402
import resolver  # noqa: E402

FACTS = {"symbol": "INFY.NS", "name": "Infosys Ltd.", "sector": "IT", "universe": "NIFTY500",
         "metrics": {"close": 1500.0, "ret_1m": -0.05, "pe": 24.0}}


ROW = ("INFY.NS", "Infosys Ltd.", "IT", "NIFTY500", "INR")

def fake_q(sql, args=()):
    if "upper(symbol) = ANY" in sql:
        return [ROW]
    if "stock_metrics_daily" in sql:
        return [("close", 1500.0), ("ret_1m", -0.05), ("pe", 24.0), ("pos_52w", 0.3549)]
    if "stock_prices" in sql:
        return [(1850.0, 1050.0)]
    if "stock_scores" in sql:
        return [("composite", 12)]
    if "raw_headlines" in sql:
        return [("Infosys wins a 500M dollar deal", "2026-10-16")]
    return []


class Gate(unittest.TestCase):
    def setUp(self):
        resolver._universe.update(rows=None, at=0.0)
        os.environ.pop("BOT_AI_RESOLVE", None)

    def test_clean_answer_passes(self):
        out = ask.answer(fake_q, "TCS", asker=lambda m: "Infosys trades at 24 P/E, down 5% over the month.")
        self.assertIn("Infosys trades at 24 P/E", out)

    def test_invented_number_fails_then_retries(self):
        answers = iter(["Infosys trades at 26 P/E.", "Infosys trades at 24 P/E."])
        out = ask.answer(fake_q, "TCS", asker=lambda m: next(answers))
        self.assertIn("24 P/E", out)

    def test_two_failures_show_the_facts_not_the_prose(self):
        out = ask.answer(fake_q, "TCS", asker=lambda m: "Infosys trades at 26 P/E.")
        self.assertIn("the facts themselves", out)
        self.assertIn("24.0", out)
        self.assertNotIn("26 P/E", out)

    def test_gateway_down_degrades_to_facts(self):
        def down(m):
            raise ConnectionError("gateway down")
        out = ask.answer(fake_q, "TCS", asker=down)
        self.assertIn("the facts themselves", out)

    def test_question_about_a_stock_uses_the_symbol_not_the_whole_phrase(self):
        facts, subj = ask.build_facts(fake_q, "TCS outlook")
        self.assertEqual((facts["symbol"], subj), ("INFY.NS", "INFY.NS"))

    def test_question_with_no_facts_is_refused_not_guessed(self):
        out = ask.answer(lambda sql, args=(): [], "what is the weather", asker=lambda m: self.fail("called"))
        self.assertIn("can only answer", out)

    def test_stock_facts_carry_the_52w_range_not_just_the_position(self):
        facts, _ = ask.build_facts(fake_q, "INFY")
        self.assertEqual(facts["metrics"]["high_52w"], 1850.0)
        self.assertEqual(facts["metrics"]["low_52w"], 1050.0)
        self.assertNotIn("pos_52w", facts["metrics"])
        self.assertEqual(facts["metrics"]["position_in_52w_range"], 0.3549)

    def test_news_headlines_pass_numbers_only(self):
        # headline text has no sign context: a number quoted from a headline must not need a sign match
        facts = {"headlines": ["Infosys wins a 500M dollar deal"]}
        bad = ask.factcheck.bad_numbers("Infosys won a 500M dollar deal.", facts)
        self.assertEqual(bad, [])
        def news_only(sql, args=()):
            return [("Infosys wins a 500M dollar deal", "2026-10-16")] if "raw_headlines" in sql else []
        out = ask.answer(news_only, "news infosys", asker=lambda m: "Infosys won a 500M dollar deal.")
        self.assertIn("500M", out)

    def test_expanded_topics_screener_sectors_regime_scorecard_earnings(self):
        def topic_q(sql, args=()):
            if "FROM stock_scores" in sql and "ORDER BY growth_score" in sql:
                return [("TCS.NS", 1, 88.5, 0.7, 0.85, 0.8, 0.7, 0.7)]
            if "FROM sector_metrics" in sql:
                return [("Financials", 14.2, 2.1, 0.16, 12.5)]
            if "FROM prediction_outcomes" in sql:
                return [(91, 50, 62.0, 3.4)]
            if "FROM earnings_calendar" in sql:
                return [("INFY.NS", "2026-11-12")]
            if "FROM index_prices" in sql:
                return [("^VIX", 15.2), ("^INDIAVIX", 13.8)]
            return []
        f_top, s_top = ask.build_facts(topic_q, "what are the top growth stocks in nifty")
        self.assertEqual(s_top, "screener")
        self.assertEqual(f_top["top_picks"][0]["symbol"], "TCS.NS")
        self.assertEqual(f_top["top_picks"][0]["archetype"], "Quality Compounder")

        f_sec, s_sec = ask.build_facts(topic_q, "which sectors are cheapest in sp500")
        self.assertEqual(s_sec, "sectors")
        self.assertEqual(f_sec["sectors"][0]["sector"], "Financials")

        f_sc, s_sc = ask.build_facts(topic_q, "how is the scorecard hit rate")
        self.assertEqual(s_sc, "scorecard")
        self.assertEqual(f_sc["horizons"][0]["hit_rate_pct"], 62.0)

        f_ec, s_ec = ask.build_facts(topic_q, "upcoming earnings calendar")
        self.assertEqual(s_ec, "earnings")
        self.assertEqual(f_ec["upcoming_earnings"][0]["symbol"], "INFY.NS")

        f_reg, s_reg = ask.build_facts(topic_q, "how is market regime and vix")
        self.assertEqual(s_reg, "regime")
        self.assertEqual(f_reg["indices"]["^VIX"], 15.2)

    def test_summarize_card_enforces_factcheck(self):
        card = "<b>Volatility</b>\n<pre>VIX 15.20 +2.1%\nIndia VIX 13.80 -1.4%</pre>"
        good = ask.summarize_card(card, command="vix",
                                  asker=lambda m: "VIX rose 2.1% to 15.20 while India VIX fell 1.4% to 13.80.")
        self.assertIn("AI Takeaway", good)
        self.assertIn("15.20", good)
        # Invented number 19.50 must be rejected after retry
        bad = ask.summarize_card(card, command="vix",
                                 asker=lambda m: "VIX rose to 19.50.")
        self.assertEqual(bad, "")


if __name__ == "__main__":
    unittest.main(verbosity=1)
