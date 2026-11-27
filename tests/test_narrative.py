#!/usr/bin/env python3
"""ML-7a narrative: the number check and the retry/degrade logic. No network, no DB.

    python3 tests/test_narrative.py
"""
import sys
import types
import unittest
from pathlib import Path

sys.modules.setdefault("psycopg", types.ModuleType("psycopg"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "manifests" / "llm"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "manifests" / "telegram"))   # factcheck.py
import narrative as n  # noqa: E402

FACTS = {"indices": [{"name": "Nifty 50", "close": 22363.35, "change_pct": -1.21},
                     {"name": "India VIX", "close": 18.4, "change_pct": 6.5}],
         "fx": {"usd_inr": 88.12, "change_pct": 0.15}, "flows_rs_cr": {"FII": -5012, "DII": 3890},
         "sentiment_7d": -0.08}


class NumberCheck(unittest.TestCase):
    def test_faithful_text_passes(self):
        t = "Nifty 50 fell 1.21% to 22,363.35 as FIIs sold Rs 5,012 crore while DIIs bought 3,890; VIX rose 6.5% to 18.4."
        self.assertEqual(n.bad_numbers(t, FACTS), [])

    def test_invented_or_recomputed_numbers_are_caught(self):
        self.assertEqual(n.bad_numbers("Nifty fell 1.5% to 22,363.35.", FACTS), ["1.5"])
        self.assertEqual(n.bad_numbers("Net flows were -1,122 crore.", FACTS), ["-1,122"])   # sum not in facts

    def test_rounding_and_small_counts_allowed(self):
        self.assertEqual(n.bad_numbers("Nifty dropped 1.2% to about 22,363; two of 3 indices fell.", FACTS), [])

    def test_tolerance_is_the_written_precision_not_a_flat_window(self):
        # Regression (2026-11-15 review): a flat ±0.1 window passed "1.30%" for a 1.21% fact.
        facts = {"chg": 1.21, "ratio": 0.0259, "r2": 0.05, "x": 1.219}
        for wrong in ("1.30%", "2.68%", "0.14", "1.3"):
            self.assertEqual(n.bad_numbers(wrong, facts), [wrong.rstrip("%")], wrong)
        for right in ("1.2%", "1.21%", "2.59%", "2.6%", "1.22", "1.21"):   # rounded / truncated / percent form
            self.assertEqual(n.bad_numbers(right, facts), [], right)

    def test_unicode_minus_and_plus(self):
        self.assertEqual(n.bad_numbers("Sentiment −0.08, rupee +0.15%.", FACTS), [])


class Directions(unittest.TestCase):
    def test_faithful_text_has_no_direction_errors(self):
        t = ("Nifty 50 fell 1.21% to 22,363.35 as FIIs sold Rs 5,012 crore while DIIs bought 3,890; "
             "VIX rose 6.5% to 18.4. India VIX fell to 18.4 earlier.")      # "fell to 18.4" is a level: skipped
        self.assertEqual(n.wrong_directions(t, FACTS), [])

    def test_swapped_directions_are_caught(self):
        self.assertEqual(n.wrong_directions("Nifty 50 rose 1.21%.", FACTS), ["1.21"])
        self.assertEqual(n.wrong_directions("FIIs recorded net inflows of 5,012 crore, DIIs outflows of 3,890.", FACTS),
                         ["5,012", "3,890"])

    def test_live_note_2026_10_19_passes(self):
        facts = {"flows_rs_cr": {"DII": -3661, "FII": 1166}, "fx": {"change_pct": 0.05},
                 "indices": [{"name": "Nifty 50", "change_pct": 0.65}, {"name": "Sensex", "change_pct": 0.56},
                             {"name": "India VIX", "close": 14.06, "change_pct": 0.25}]}
        t = ("The Nifty 50 and Sensex rose 0.65% and 0.56% respectively, while the India VIX increased 0.25% to 14.06. "
             "DIIs recorded net outflows of 3,661 crores, contrasting with FII inflows of 1,166 crores, and the rupee "
             "depreciated 0.05% against the dollar.")
        self.assertEqual(n.wrong_directions(t, facts), [])

    def test_wrong_direction_triggers_the_retry(self):
        answers = iter([("Nifty 50 rose 1.21%.", "m"), ("Nifty 50 fell 1.21%.", "m")])
        status, summary, *_ = n.narrate(FACTS, lambda msgs: next(answers))
        self.assertEqual((status, summary), ("ok", "Nifty 50 fell 1.21%."))


class Fences(unittest.TestCase):
    def test_code_block_is_removed(self):
        self.assertEqual(n.strip_fences('```json\n{"summary": "x"}\n```'), '{"summary": "x"}')
        self.assertEqual(n.strip_fences('{"summary": "x"}'), '{"summary": "x"}')


class Narrate(unittest.TestCase):
    def test_ok_first_try(self):
        st, s, model, attempts, _ = n.narrate(FACTS, lambda m: ("Nifty 50 fell 1.21%.", "m1"))
        self.assertEqual((st, attempts, model), ("ok", 1, "m1"))

    def test_retry_then_ok_and_retry_message_names_bad_numbers(self):
        seen = []

        def asker(msgs):
            seen.append(msgs)
            return ("Nifty fell 9.99%." if len(seen) == 1 else "Nifty 50 fell 1.21%."), "m1"
        st, s, _, attempts, _ = n.narrate(FACTS, asker)
        self.assertEqual((st, attempts), ("ok", 2))
        self.assertIn("9.99", seen[1][-1]["content"])

    def test_invalid_after_two_bad_answers_never_stores_text(self):
        st, s, _, attempts, detail = n.narrate(FACTS, lambda m: ("Nifty fell 9.99%.", "m1"))
        self.assertEqual((st, s, attempts), ("invalid", None, 2))
        self.assertIn("9.99", detail)

    def test_gateway_down_is_unavailable_not_a_crash(self):
        def asker(msgs):
            raise RuntimeError("unavailable: connection refused")
        st, s, *_ = n.narrate(FACTS, asker)
        self.assertEqual((st, s), ("unavailable", None))


class Bakeoff(unittest.TestCase):
    def setUp(self):
        import bakeoff
        self.b = bakeoff

    def test_run_one_records_first_try_and_final(self):
        calls = []

        def asker(msgs, model):
            calls.append(model)
            return ("Nifty fell 9.99%." if len(calls) == 1 else "Nifty 50 fell 1.21%."), model
        r = self.b.run_one(FACTS, "fast-json.2", asker)
        self.assertEqual((r["first_ok"], r["final_ok"], r["error"]), (False, True, ""))
        self.assertEqual(calls, ["fast-json.2", "fast-json.2"])   # the candidate is pinned per call

    def test_run_one_empty_reply_is_not_a_first_try_pass(self):
        r = self.b.run_one(FACTS, "general", lambda msgs, model: ("", model))
        self.assertFalse(r["first_ok"])

    def test_score_and_errors(self):
        res = [{"first_ok": True, "final_ok": True, "latency": 2.0, "error": ""},
               {"first_ok": False, "final_ok": True, "latency": 4.0, "error": ""},
               {"first_ok": False, "final_ok": False, "latency": None, "error": "unavailable"}]
        self.assertEqual(self.b.score(res), {"runs": 3, "first_try_pass": 0.33, "final_pass": 0.67,
                                             "errors": 1, "median_latency_s": 3.0})


if __name__ == "__main__":
    unittest.main(verbosity=1)
