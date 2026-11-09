#!/usr/bin/env python3
"""Digest v9: the optional AI summary section (ML-7a). No network, no DB.

    python3 tests/test_digest_narrative.py
"""
import os
import sys
import types
import unittest
from pathlib import Path

pg = types.ModuleType("psycopg")
pg.Error = type("Error", (Exception,), {})
sys.modules["psycopg"] = pg
sys.modules.setdefault("requests", types.ModuleType("requests"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "manifests" / "telegram"))
import digest  # noqa: E402


class Cur:
    def __init__(self, row=None, exc=None):
        self.row, self.exc, self.sql = row, exc, []

    def execute(self, sql, args=()):
        self.sql.append((sql, args))
        if self.exc:
            raise self.exc

    def fetchone(self):
        return self.row


class Narrative(unittest.TestCase):
    def setUp(self):
        os.environ["DIGEST_NARRATIVE"] = "1"

    def tearDown(self):
        os.environ.pop("DIGEST_NARRATIVE", None)

    def test_ok_note_is_returned_and_only_status_ok_is_queried(self):
        cur = Cur(("Nifty 50 fell 1.21%.",))
        self.assertEqual(digest.narrative(cur), "Nifty 50 fell 1.21%.")
        self.assertIn("status = 'ok'", cur.sql[0][0])

    def test_switched_off_means_no_query_at_all(self):
        os.environ["DIGEST_NARRATIVE"] = "0"
        cur = Cur(("x",))
        self.assertIsNone(digest.narrative(cur))
        self.assertEqual(cur.sql, [])

    def test_missing_note_or_table_is_silent(self):
        self.assertIsNone(digest.narrative(Cur(None)))
        UndefinedTable = type("UndefinedTable", (pg.Error,), {})   # psycopg.errors.UndefinedTable
        self.assertIsNone(digest.narrative(Cur(exc=UndefinedTable('relation "llm_narratives" does not exist'))))

    def test_other_db_errors_still_raise(self):
        other = pg.Error("connection lost")
        with self.assertRaises(pg.Error):
            digest.narrative(Cur(exc=other))


PHONE_WIDTH = 38   # widest <pre> line a phone shows without wrapping


class FactsBlock(unittest.TestCase):
    """The FX/sentiment/flows table must stay phone-width: kv() pads every
    line to the longest key, so one long label wraps the whole block."""

    ROWS = ([("USD/INR", None, 96.7812), ("EUR/USD", None, 1.0845)],
            -0.123, [("DII", 112345.0), ("FII", -98765.0)])

    def test_fits_a_phone_and_explains_the_sentiment_scale(self):
        import html
        import re
        block = digest.facts_block(*self.ROWS)
        self.assertIn("FinBERT", block[1])          # derivation shown to the user
        for part in re.findall(r"<pre>(.*?)</pre>", block[0], re.S):
            for line in html.unescape(part).split("\n"):
                self.assertLessEqual(len(line), PHONE_WIDTH, line)

    def test_no_facts_no_block_and_no_legend_without_a_gauge(self):
        self.assertEqual(digest.facts_block([], None, []), [])
        self.assertEqual(len(digest.facts_block(self.ROWS[0], None, [])), 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
