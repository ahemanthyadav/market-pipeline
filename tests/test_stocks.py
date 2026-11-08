#!/usr/bin/env python3
"""Tests for manifests/telegram/stocks.py — the pure logic and message layout
behind the bot's /stock /index /world /fx /watch /top /multibagger /movers
/track /macro commands, the digest tables and the daily change alerts.

No database, no network: every query goes to a scripted fake.

    python3 tests/test_stocks.py
"""
import html
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "manifests" / "telegram"))
import stocks  # noqa: E402

PHONE_WIDTH = 38   # widest <pre> line a phone shows without wrapping


def day(d):
    return datetime(2026, 10, d, tzinfo=timezone.utc)


def pre_lines(text):
    """Every line inside every <pre> block, unescaped."""
    return [line for block in re.findall(r"<pre>(.*?)</pre>", text, re.S)
            for line in html.unescape(block).split("\n")]


def pairs(text):
    """Label → value from the label/value blocks of a message."""
    return dict(re.split(r"\s{2,}", line.strip(), maxsplit=1)
                for line in pre_lines(text) if re.search(r"\s{2,}", line.strip()))


class FakeDB:
    """Answers by the first scripted substring found in the SQL; records calls."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, sql, args=()):
        self.calls.append((sql, args))
        for needle, rows in self.answers:
            if needle in sql:
                return rows(args) if callable(rows) else rows
        raise AssertionError(f"unscripted query: {sql[:80]}")


class RsiTest(unittest.TestCase):
    def test_too_short_is_none(self):
        self.assertIsNone(stocks.rsi([1.0] * 14))

    def test_only_gains_is_100(self):
        self.assertEqual(stocks.rsi([float(i) for i in range(1, 30)]), 100.0)

    def test_only_losses_is_0(self):
        self.assertEqual(stocks.rsi([float(i) for i in range(30, 1, -1)]), 0.0)

    def test_wilder_reference_series(self):
        # Wilder's own worked example (New Concepts, 1978): first RSI = 70.46.
        closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
                  46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
        self.assertAlmostEqual(stocks.rsi(closes), 70.46, places=1)


class FormatTest(unittest.TestCase):
    def test_pct_share_and_missing(self):
        self.assertEqual(stocks.pct(0.0377, 2), "+3.77%")
        self.assertEqual(stocks.pct(-0.5, 0), "-50%")
        self.assertEqual(stocks.pct(None), "n/a")
        self.assertEqual(stocks.share(0.48), "48%")

    def test_px_scales_decimals_to_magnitude(self):
        self.assertEqual(stocks.px(206220.0), "206,220")
        self.assertEqual(stocks.px(7765.36), "7,765.4")
        self.assertEqual(stocks.px(60.58), "60.58")

    def test_big(self):
        self.assertEqual(stocks.big(7507531530240), "7.51T")
        self.assertEqual(stocks.big(936.53e9), "936.53B")
        self.assertEqual(stocks.big(None), "n/a")

    def test_clip_and_bare(self):
        self.assertEqual(stocks.clip("Nifty Smallcap 250", 13), "Nifty Smallc…")
        self.assertEqual(stocks.bare("TCS.NS"), "TCS")
        self.assertEqual(stocks.bare("AAPL"), "AAPL")

    def test_table_aligns_columns_and_escapes(self):
        text = stocks.table(("", "Close", "Day"),
                            ["INDIA", ("S&P 500", "7,765.4", "-0.5%"), ("Nifty", "22,534", "+1.4%")],
                            "lrr")
        self.assertTrue(text.startswith("<pre>") and text.endswith("</pre>"))
        self.assertIn("S&amp;P 500", text)
        lines = pre_lines(text)
        self.assertEqual(lines[1:3], ["", "INDIA"])                 # section row
        self.assertEqual(lines[3], "S&P 500 7,765.4 -0.5%")
        self.assertEqual(lines[4], "Nifty    22,534 +1.4%")         # right-aligned numbers

    def test_kv_drops_missing_values_and_aligns(self):
        text = stocks.kv([("P/E", "15.1"), ("P/B", None), ("Market cap", "7.51T")])
        self.assertEqual(pre_lines(text), ["P/E          15.1", "Market cap  7.51T"])
        self.assertEqual(stocks.kv([("x", None)]), "")

    def test_gold_parity(self):
        # 4215.80 USD/oz at 96.78 INR/USD → ~131,176 INR per 10 g
        self.assertAlmostEqual(stocks.gold_inr_10g(4215.80, 96.78), 131176, delta=5)

    def test_spread_bp(self):
        y = {"^TNX": 5.231, "^IRX": 4.043}
        self.assertAlmostEqual(stocks.spread_bp(y, "^TNX", "^IRX"), 118.8, places=1)
        self.assertIsNone(stocks.spread_bp(y, "^TNX", "IN_GS_10Y"))


def tag_balanced(text):
    """True when every <pre>/<b>/<i>/<code> opened is closed in order."""
    stack = []
    for m in re.finditer(r"<(/?)(pre|b|i|code)>", text):
        if m.group(1):
            if not stack or stack.pop() != m.group(2):
                return False
        else:
            stack.append(m.group(2))
    return not stack


def entities_whole(text):
    """True when every & starts a complete entity — nothing was cut mid-entity."""
    return all(re.match(r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);", text[i:])
               for i, ch in enumerate(text) if ch == "&")


class TrimHtmlTest(unittest.TestCase):
    def test_under_and_at_limit_unchanged(self):
        self.assertEqual(stocks.trim_html("short", 100), "short")
        self.assertEqual(stocks.trim_html("exact", 5), "exact")
        self.assertEqual(stocks.trim_html("<pre>x</pre>", 12), "<pre>x</pre>")

    def test_plain_text_over_limit(self):
        out = stocks.trim_html("abcdefghij", 5)
        self.assertEqual(out, "abcd…")
        self.assertLessEqual(len(out), 5)

    def test_cut_inside_pre_closes_it(self):
        text = "<pre>" + "a" * 100 + "</pre>"
        out = stocks.trim_html(text, 20)
        self.assertLessEqual(len(out), 20)
        self.assertTrue(out.endswith("…</pre>"))          # ellipsis inside the scope
        self.assertTrue(tag_balanced(out), out)
        self.assertEqual(out, "<pre>" + "a" * 8 + "…</pre>")

    def test_cut_inside_nested_b_within_pre(self):
        text = "<pre>head <b>" + "x" * 100 + "</b></pre>"
        out = stocks.trim_html(text, 30)
        self.assertLessEqual(len(out), 30)
        self.assertTrue(out.endswith("…</b></pre>"))
        self.assertTrue(tag_balanced(out), out)

    def test_never_cuts_mid_tag(self):
        text = "aaaa<pre>bb</pre>"
        for limit in range(1, len(text)):
            out = stocks.trim_html(text, limit)
            self.assertNotRegex(out, r"<[^>]*$")          # no half-open tag at the end
            self.assertTrue(tag_balanced(out), f"limit={limit}: {out}")

    def test_never_cuts_mid_entity(self):
        out = stocks.trim_html("aa&amp;bb", 5)            # naive slice would give 'aa&a…'
        self.assertEqual(out, "aa…")
        text = "P&amp;L is &lt;fine&gt; &#8212; yes, really quite fine"
        for limit in range(1, len(text)):
            out = stocks.trim_html(text, limit)
            self.assertLessEqual(len(out), limit)
            self.assertTrue(entities_whole(out.rstrip("…")), f"limit={limit}: {out}")

    def test_every_limit_fits_and_balances_on_a_realistic_message(self):
        text = ("<b>Market digest</b>  <i>09 Oct</i>\n"
                "<pre>S&amp;P 500 7,765.4 -0.5%\nNifty 22,534 +1.4%</pre>\n"
                "Try <code>/watch add TCS</code> — P&amp;L shown per chat")
        for limit in range(1, len(text) + 2):
            out = stocks.trim_html(text, limit)
            self.assertLessEqual(len(out), limit, f"limit={limit}")
            self.assertTrue(tag_balanced(out), f"limit={limit}: {out}")
            self.assertTrue(entities_whole(out), f"limit={limit}: {out}")

    def test_tiny_limits(self):
        self.assertEqual(stocks.trim_html("abc", 0), "")
        self.assertEqual(stocks.trim_html("abc", 1), "…")
        self.assertEqual(stocks.trim_html("<pre>abc</pre>", 2), "…")   # no room for </pre>


class WorldFxTest(unittest.TestCase):
    def test_change(self):
        self.assertAlmostEqual(stocks.change(110.0, 100.0), 0.10)
        self.assertIsNone(stocks.change(110.0, None))
        self.assertIsNone(stocks.change(None, 100.0))

    def test_parse_world(self):
        self.assertEqual(stocks.parse_world("/world"), (None, None))
        self.assertEqual(stocks.parse_world("/world ASIA"), ("Asia-Pacific", None))
        self.assertIsNotNone(stocks.parse_world("/world mars")[1])

    def db(self):
        return FakeDB([
            ("FROM index_prices", [
                ("^NSEI", day(9), 110.0, 100.0, 105.0, 100.0),
                ("^GSPC", day(8), 90.0, 91.0, 95.0, 100.0),
                ("^VIX", day(8), 30.0, 20.0, 20.0, 10.0),      # +200% YTD, never "best"
                ("NIFTYSMLCAP250.NS", day(9), 17580.9, 17488.0, 18400.0, 16680.0),
                ("^NEWIDX", day(9), 5.0, 5.0, None, None),     # unplaced → Other
            ]),
            ("FROM ticker_names", [("^NSEI", "Nifty 50"), ("^GSPC", "S&P 500"),
                                   ("^VIX", "VIX (US)"),
                                   ("NIFTYSMLCAP250.NS", "Nifty Smallcap 250")]),
        ])

    def test_world_is_one_table_grouped_by_region(self):
        text = stocks.world_text(self.db())
        lines = pre_lines(text)
        self.assertLess(lines.index("INDIA"), lines.index("US"))
        self.assertIn("OTHER", lines)
        nifty = next(line for line in lines if line.startswith("Nifty 50"))
        self.assertEqual(nifty.split()[-3:], ["110.00", "+10.0%", "+10.0%"])
        self.assertTrue(any(line.startswith("Nifty Sml 250") for line in lines))  # short name
        self.assertIn("Best this year: <b>Nifty 50</b> +10.0%", text)
        self.assertIn("Worst this year: <b>S&amp;P 500</b> -10.0%", text)
        self.assertIn("to 09 Oct", text)

    def test_world_fits_a_phone(self):
        for line in pre_lines(stocks.world_text(self.db())):
            self.assertLessEqual(len(line), PHONE_WIDTH, line)

    def test_world_region_filter(self):
        text = stocks.world_text(self.db(), "US")
        self.assertIn("S&amp;P 500", text)
        self.assertNotIn("Nifty 50", text)
        self.assertNotIn("Best this year", text)

    def test_quote_table_keeps_the_requested_order_and_skips_missing(self):
        lines = pre_lines(stocks.quote_table(self.db(), ["^GSPC", "^MISSING", "^NSEI"]))
        self.assertEqual([line.split()[0] for line in lines[1:]], ["S&P", "Nifty"])
        self.assertEqual(lines[0].split(), ["Close", "Day", "YTD"])

    def test_fx_orders_inr_first_and_explains_missing_ytd(self):
        db = FakeDB([("FROM fx_rates", [
            ("EURUSD=X", day(9), 1.1, 1.09, None, None),
            ("USDINR=X", day(9), 96.78, 96.5, None, None)])])
        text = stocks.fx_text(db)
        lines = pre_lines(text)
        self.assertEqual(lines[0].split(), ["Pair", "Rate", "Day"])       # no YTD column
        self.assertEqual(lines[1].split(), ["USD/INR", "96.7800", "+0.29%"])
        self.assertTrue(lines[2].startswith("EUR/USD"))
        self.assertIn("Year-to-date appears once", text)

    def test_fx_shows_ytd_column_when_history_allows(self):
        db = FakeDB([("FROM fx_rates", [("USDINR=X", day(9), 96.0, 95.0, None, 80.0)])])
        lines = pre_lines(stocks.fx_text(db))
        self.assertEqual(lines[0].split()[-1], "YTD")
        self.assertEqual(lines[1].split()[-1], "+20.0%")

    def test_performance_sql_filters_by_time(self):
        sql = stocks.performance_sql("index_prices", "ticker", "close")
        self.assertIn("ts >= now() - interval '15 days'", sql)
        self.assertIn("date_trunc('year', l.ts)", sql)


class IndexCardTest(unittest.TestCase):
    def series(self, n, start=100.0, step=1.0):
        first = datetime(2026, 10, 9, tzinfo=timezone.utc) - timedelta(days=n - 1)
        return [(first + timedelta(days=i), start + step * i) for i in range(n)]

    def card(self, n):
        return stocks.index_card(FakeDB([("FROM index_prices", self.series(n))]),
                                 "^X", "Test & Co")

    def test_full_year_card(self):
        text = self.card(300)                         # closes 100 … 399, one per day
        self.assertIn("<b>Test &amp; Co</b>", text)
        self.assertIn("<b>399.00</b>  +0.25%  <i>09 Oct close</i>", text)
        got = pairs(text)
        self.assertEqual(got["1 week"], "+1.3%")              # 399 vs 394
        self.assertEqual(got["Year to date"], "+241.0%")      # 399 vs 117 (31 Dec close)
        self.assertEqual(got["1 year"], "+171.4%")            # 399 vs 147
        self.assertEqual(got["52-week high"], "399.00")
        self.assertEqual(got["52-week low"], "148.00")
        self.assertEqual(got["Below the high"], "+0.0%")
        self.assertEqual(got["RSI (14)"], "100")
        self.assertIn("vs 200-day average", got)
        self.assertNotIn("\n\n", text)

    def test_short_history_drops_what_it_cannot_compute(self):
        labels = " ".join(pre_lines(self.card(30)))
        self.assertIn("1 week", labels)
        for absent in ("3 months", "1 year", "high", "200-day"):
            self.assertNotIn(absent, labels)

    def test_no_rows(self):
        self.assertEqual(stocks.index_card(FakeDB([("FROM index_prices", [])]), "^X", "Gone"),
                         "Gone: no data")

    def test_index_text_lookup(self):
        many = FakeDB([("FROM ticker_names", [("^NSEI", "Nifty 50"), ("^NSEBANK", "Nifty Bank")])])
        self.assertIn("/index ^NSEBANK", stocks.index_text(many, "nifty"))
        self.assertIn("/index nikkei", stocks.index_text(many, ""))
        self.assertIn("No index matching",
                      stocks.index_text(FakeDB([("FROM ticker_names", [])]), "zz"))
        self.assertEqual(stocks.find_index(FakeDB([]), "x'; DROP"), [])
        exact = FakeDB([("FROM ticker_names", [("^NSEI", "Nifty 50"), ("^NSEBANK", "Nifty Bank")]),
                        ("FROM index_prices WHERE ticker", self.series(10)),
                        ("universe_constituents", [])])
        self.assertIn("<b>Nifty 50</b>", stocks.index_text(exact, "^nsei"))


class LongTermTest(unittest.TestCase):
    END = datetime(2026, 10, 9, tzinfo=timezone.utc)

    def weekly(self, years, level=lambda i, n: 100.0):
        """One row a week for `years`; level(i, n) gives the close of row i of n."""
        n = int(years * 52) + 1
        return [(self.END - timedelta(weeks=n - 1 - i), level(i, n)) for i in range(n)]

    def test_trailing_average_needs_the_full_window(self):
        rows = self.weekly(4, lambda i, n: 100.0 if i < n - 52 else 200.0)
        # 52 weeks at 200 plus the row exactly 364 days back, still at 100
        self.assertAlmostEqual(stocks.trailing_average(rows, 1), (52 * 200 + 100) / 53)
        self.assertAlmostEqual(stocks.trailing_average(rows, 3), (104 * 100 + 52 * 200) / 156,
                               delta=1.0)
        self.assertIsNone(stocks.trailing_average(rows, 5))

    def test_gap_history_starts_once_a_window_is_full(self):
        rows = self.weekly(6)                                   # flat at 100
        gaps = stocks.gap_history(rows, 5)
        self.assertTrue(40 <= len(gaps) <= 60)                  # ~1 year of full windows
        self.assertTrue(all(abs(g) < 1e-9 for g in gaps))

    def test_gap_is_close_over_its_own_trailing_mean(self):
        rows = self.weekly(4, lambda i, n: 100.0 if i < n - 1 else 150.0)   # last day jumps
        gap = stocks.gap_history(rows, 3)[-1]
        self.assertAlmostEqual(gap, 150.0 / ((155 * 100 + 150) / 156) - 1, places=3)

    def test_percentile_and_reading_bands(self):
        self.assertEqual(stocks.percentile_of(3, [1, 2, 3, 4]), 62.5)     # 2 below + half a tie
        self.assertEqual(stocks.percentile_of(0.1 + 0.2, [0.3, 0.3]), 50.0)  # float noise = tie
        self.assertEqual([stocks.reading(p) for p in (0, 9.9, 10, 24, 25, 74, 75, 89, 90, 100)],
                         ["very low", "very low", "low", "low", "normal", "normal",
                          "high", "high", "very high", "very high"])

    def test_block_for_a_steadily_rising_index_reads_normal(self):
        # +10% a year for 10 years: always far above its averages, yet never unusual
        rows = self.weekly(10, lambda i, n: 100.0 * 1.1 ** (i / 52))
        lines = stocks.long_term_block(rows, "^NSEI")
        self.assertIn("10 years of history", lines[0])
        table = pre_lines(lines[1])
        self.assertEqual(table[0].split(), ["Level", "Now", "vs"])
        self.assertEqual([t.split()[0] for t in table[1:5]], ["1-year", "3-year", "5-year",
                                                               "10-year"])
        self.assertTrue(table[5].startswith("High since 2016"))
        self.assertTrue(table[4].split()[-1].startswith("+"))       # above its 10-year average
        self.assertIn("Reading: normal", lines[2])
        self.assertIn("5-year average", lines[2])
        self.assertIn("not where it goes next", lines[3])
        for line in table:
            self.assertLessEqual(len(line), PHONE_WIDTH, line)

    def test_block_flags_a_collapse_and_a_spike(self):
        fall = self.weekly(10, lambda i, n: 100.0 * 1.1 ** (i / 52) * (0.6 if i == n - 1 else 1))
        self.assertIn("Reading: very low", stocks.long_term_block(fall, "^NSEI")[2])
        jump = self.weekly(10, lambda i, n: 100.0 * 1.1 ** (i / 52) * (1.5 if i == n - 1 else 1))
        self.assertIn("Reading: very high", stocks.long_term_block(jump, "^NSEI")[2])

    def test_shorter_history_uses_the_3_year_average(self):
        rows = self.weekly(5, lambda i, n: 100.0 * 1.1 ** (i / 52))
        lines = stocks.long_term_block(rows, "^NSEI")
        self.assertIn("3-year average", lines[2])
        self.assertNotIn("10-year", lines[1])

    def test_volatility_gauge_ranks_its_level(self):
        rows = self.weekly(10, lambda i, n: 12.0 if i == n - 1 else 15.0 + (i % 10))
        text = "\n".join(stocks.long_term_block(rows, "^VIX"))
        self.assertIn("Reading: very low", text)
        self.assertIn("higher than on 0% of days since 2016", text)
        self.assertNotIn("stretched", text)

    def test_too_little_history_gives_no_block_or_no_reading(self):
        self.assertEqual(stocks.long_term_block(self.weekly(0.8), "^NSEI"), [])
        lines = stocks.long_term_block(self.weekly(3), "^NSEI")      # averages, no reading
        self.assertEqual(len(lines), 2)

    def test_card_includes_the_block(self):
        rows = [(self.END - timedelta(days=3650 - i), 100.0 * 1.1 ** (i / 365)) for i in range(3651)]
        text = stocks.index_card(FakeDB([("FROM index_prices", rows),
                                         ("universe_constituents", [])]), "^NSEI", "Nifty 50")
        self.assertIn("<b>Long-term reference</b>", text)
        self.assertIn("interval '40 years'", "interval '40 years'")
        self.assertIn("<b>Reading: normal</b>", text)


class IndexQuantTest(unittest.TestCase):
    def quant_db(self, pe=None, pb=None, cap=None):
        """Build a FakeDB answering the universe_constituents query and both
        stock_fundamentals queries (pe/pb query passes 2 args, cap query 1)."""
        members = [("AAPL",), ("MSFT",), ("GOOG",)]
        fund_all = []
        for i, sym in enumerate(("AAPL", "MSFT", "GOOG")):
            if pe is not None and i < len(pe):
                fund_all.append((sym, "pe", pe[i]))
            if pb is not None and i < len(pb):
                fund_all.append((sym, "pb", pb[i]))
            if cap is not None and i < len(cap):
                fund_all.append((sym, "market_cap", cap[i]))

        def fund(args):
            # pe/pb query: (symbols, ["pe", "pb"]) → 2 args → 3-tuples
            # cap query:   (symbols,)            → 1 arg  → 2-tuples (symbol, value)
            if len(args) > 1:
                return [r for r in fund_all if r[1] in ("pe", "pb")]
            return [(r[0], r[2]) for r in fund_all if r[1] == "market_cap"]

        return FakeDB([("universe_constituents", lambda _: members),
                       ("stock_fundamentals", fund)])

    def test_median_pe_and_pb_and_total_cap(self):
        db = self.quant_db(pe=[20.0, 30.0, 40.0], pb=[5.0, 10.0, 15.0],
                           cap=[1e12, 2e12, 3e12])
        pe, pb, cap, n_pe, n_pb = stocks.index_quant(db, "SP500")
        self.assertEqual(pe, 30.0)              # median of [20, 30, 40]
        self.assertEqual(pb, 10.0)              # median of [5, 10, 15]
        self.assertEqual(cap, 6e12)
        self.assertEqual(n_pe, 3)
        self.assertEqual(n_pb, 3)

    def test_even_count_median_averages_middle_two(self):
        db = self.quant_db(pe=[10.0, 20.0])
        pe, _, _, _, _ = stocks.index_quant(db, "NIFTY500")
        self.assertEqual(pe, 15.0)              # (10 + 20) / 2

    def test_missing_metrics_return_none(self):
        db = self.quant_db(pe=[20.0])
        pe, pb, cap, n_pe, n_pb = stocks.index_quant(db, "SP400")
        self.assertEqual(pe, 20.0)
        self.assertIsNone(pb)
        self.assertIsNone(cap)
        self.assertEqual(n_pe, 1)
        self.assertEqual(n_pb, 0)

    def test_no_members_returns_none(self):
        db = FakeDB([("universe_constituents", [])])
        self.assertIsNone(stocks.index_quant(db, "SP600"))

    def test_no_fundamentals_returns_none(self):
        db = FakeDB([("universe_constituents", [("AAPL",), ("MSFT",)]),
                     ("stock_fundamentals", [])])
        self.assertIsNone(stocks.index_quant(db, "SP500"))

    def test_index_card_shows_quant_block_for_sp500(self):
        db = FakeDB([
            ("FROM index_prices", lambda _: [(day(9), 6000.0)]),
            ("universe_constituents", lambda _: [("AAPL",), ("MSFT",)]),
        ])
        # stock_fundamentals: pe/pb query gets 2 args, cap query gets 1
        fund_all = [("AAPL", "pe", 25.0), ("AAPL", "pb", 6.0), ("AAPL", "market_cap", 3e12),
                    ("MSFT", "pe", 35.0), ("MSFT", "pb", 12.0), ("MSFT", "market_cap", 2.5e12)]

        def fund(args):
            if len(args) > 1:
                return [r for r in fund_all if r[1] in ("pe", "pb")]
            return [(r[0], r[2]) for r in fund_all if r[1] == "market_cap"]

        db.answers.append(("stock_fundamentals", fund))
        text = stocks.index_card(db, "^GSPC", "S&P 500")
        self.assertIn("P/E (median)", text)
        self.assertIn("P/B (median)", text)
        self.assertIn("Market cap", text)
        self.assertIn("30.0", text)              # median of [25, 35]
        self.assertIn("5.50T", text)             # 5.5e12 → "5.50T"

    def test_index_card_skips_quant_for_unmapped_ticker(self):
        db = FakeDB([("FROM index_prices", lambda _: [(day(9), 7000.0)]),
                     ("universe_constituents", [])])
        text = stocks.index_card(db, "^VIX", "India VIX")
        self.assertNotIn("P/E (median)", text)
        self.assertNotIn("Market cap", text)




class YtdTest(unittest.TestCase):
    NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)

    def test_jan1_is_utc_year_start(self):
        self.assertEqual(stocks.jan1(self.NOW), datetime(2026, 1, 1, tzinfo=timezone.utc))

    def test_ytd_map_uses_parameter_bounds_and_skips_new_listings(self):
        db = FakeDB([("ts < %s::timestamptz", [("A", 100.0)]),
                     ("interval '15 days'", [("A", 125.0), ("NEW", 50.0)])])
        self.assertEqual(stocks.ytd_map(db, ["NEW", "A", "A"], self.NOW), {"A": 0.25})
        sql, args = db.calls[0]
        self.assertNotIn("date_trunc", sql)
        self.assertEqual(args, (["A", "NEW"], datetime(2026, 1, 1, tzinfo=timezone.utc),
                                datetime(2025, 12, 12, tzinfo=timezone.utc)))
        self.assertEqual(stocks.ytd_map(FakeDB([]), []), {})

    def test_tag(self):
        self.assertEqual(stocks.ytd_tag(0.252), " · YTD +25.2%")
        self.assertEqual(stocks.ytd_tag(None), "")


class ResolveTest(unittest.TestCase):
    ROW = ("TCS.NS", "Tata Consultancy Services Ltd.", "IT", "NIFTY500", "INR")

    def test_bare_symbol_tries_ns_suffix(self):
        db = FakeDB([("upper(symbol) = ANY", [self.ROW])])
        self.assertEqual(stocks.resolve(db, " tcs "), [self.ROW])
        self.assertEqual(db.calls[0][1], (["TCS", "TCS.NS"], "TCS"))

    def test_falls_back_to_name_search(self):
        db = FakeDB([("upper(symbol) = ANY", []), ("name ILIKE", [self.ROW])])
        self.assertEqual(stocks.resolve(db, "tata consult"), [self.ROW])
        self.assertEqual(db.calls[1][1], ("%tata consult%",))

    def test_rejects_wildcards_and_sql_without_querying(self):
        db = FakeDB([])
        for bad in ("%", "a_b", "x'; DROP TABLE watchlist;--", "", "a" * 41):
            self.assertEqual(stocks.resolve(db, bad), [])
        self.assertEqual(db.calls, [])

    def test_pick_one_messages(self):
        db = FakeDB([("upper(symbol) = ANY", []),
                     ("name ILIKE", [self.ROW, ("TATAMOTORS.NS", "Tata Motors", "Auto",
                                                "NIFTY500", "INR")])])
        row, problem = stocks.pick_one(db, "tata")
        self.assertIsNone(row)
        self.assertIn("TATAMOTORS.NS", problem)
        self.assertIn("/stock", stocks.pick_one(db, "")[1])
        none = FakeDB([("upper(symbol) = ANY", []), ("name ILIKE", []),
                       ("WHERE active ORDER BY symbol", [])])   # resolver's universe (fuzzy) lookup
        self.assertIn("No stock matching", stocks.pick_one(none, "zzz")[1])


def fy(year):
    return datetime(year, 3, 31, tzinfo=timezone.utc)


class FundamentalsTest(unittest.TestCase):
    def test_series_and_latest_two_never_mix_sources(self):
        rows = [("eps", "yfinance", fy(2026), 136.0), ("eps", "yfinance", fy(2025), 134.0),
                ("eps", "edgar", fy(2025), 999.0), ("eps", "edgar", fy(2024), 1.0),
                ("fcf", "edgar", fy(2026), 5.0), ("fcf", "yfinance", fy(2026), 7.0),
                ("pe", "yfinance", fy(2026), None)]
        self.assertEqual(stocks.series(rows)["eps"], [(fy(2026), 136.0), (fy(2025), 134.0)])
        out = stocks.latest_two(rows)
        self.assertEqual(out["eps"], (136.0, 134.0, fy(2026)))
        self.assertEqual(out["fcf"][0], 7.0)           # same filing date → yfinance wins
        self.assertNotIn("pe", out)

    def test_flow_metrics_ignore_edgar_year_to_date_rows(self):
        # MSFT-like: EDGAR 10-Q rows (3/6/9-month YTD) are newer than the last full
        # fiscal year, so the newest-filing rule would pick a half-year (DQ-2).
        rows = [("total_revenue", "edgar", day(9), 133.0),      # newest, year-to-date
                ("total_revenue", "edgar", fy(2025), 66.0),
                ("total_revenue", "yfinance", fy(2026), 300.0),
                ("total_revenue", "yfinance", fy(2025), 270.0),
                ("shareholder_equity", "edgar", day(9), 500.0)]  # balance sheet: unchanged
        self.assertEqual(stocks.series(rows)["total_revenue"],
                         [(fy(2026), 300.0), (fy(2025), 270.0)])
        self.assertEqual(stocks.latest_two(rows)["total_revenue"][:2], (300.0, 270.0))
        self.assertEqual(stocks.series(rows)["shareholder_equity"], [(day(9), 500.0)])
        # no yfinance rows at all: falls back to the old rule rather than showing nothing
        self.assertEqual(stocks.series([("fcf", "edgar", fy(2026), 5.0)])["fcf"],
                         [(fy(2026), 5.0)])

    def test_yoy_refuses_a_negative_base(self):
        annual = {"total_revenue": (110.0, 100.0, fy(2026)),
                  "eps": (5.0, -2.0, fy(2026)), "fcf": (3.0, None, fy(2026))}
        self.assertEqual(stocks.yoy(annual, "total_revenue"), "+10.0%")
        self.assertIsNone(stocks.yoy(annual, "eps"))
        self.assertIsNone(stocks.yoy(annual, "fcf"))
        self.assertIsNone(stocks.yoy(annual, "net_income"))

    def test_reprice_scales_price_ratios_only(self):
        snap = {"pe": 20.0, "pb": 4.0, "market_cap": 1000.0, "ev_ebitda": 12.0, "roe": 0.2}
        out, repriced = stocks.reprice(snap, close=110.0, snapshot_close=100.0)
        self.assertTrue(repriced)
        self.assertAlmostEqual(out["pe"], 22.0)
        self.assertAlmostEqual(out["pb"], 4.4)
        self.assertAlmostEqual(out["market_cap"], 1100.0)
        self.assertEqual((out["ev_ebitda"], out["roe"]), (12.0, 0.2))
        self.assertEqual(snap["pe"], 20.0)                       # input untouched

    def test_reprice_without_a_snapshot_price_leaves_values(self):
        out, repriced = stocks.reprice({"pe": 20.0}, close=110.0, snapshot_close=None)
        self.assertEqual((out, repriced), ({"pe": 20.0}, False))


class StockCardTest(unittest.TestCase):
    ROW = ("TCS.NS", "Tata Consultancy & Co", "IT", "NIFTY500", "INR")
    SNAP = datetime(2026, 10, 4, tzinfo=timezone.utc)

    def db(self, snapshot_close=2000.0):
        prices = [(day(9) - timedelta(days=i), 2200.0 - 10 * i) for i in range(40)]
        return FakeDB([
            ("ORDER BY ts DESC LIMIT 60", prices),
            ("ts <= %s::timestamptz", [(snapshot_close,)] if snapshot_close else []),
            ("ts < %s::timestamptz", [(3300.0,)]),                     # last close of 2025
            ("max(close), min(close)", [(2400.0, 1900.0)]),            # 52-week close range
            ("FROM stock_metrics_daily", [("ret_1m", -0.016), ("ret_6m", -0.151),
                                          ("ret_12m", -0.275), ("sma50_ratio", 0.973),
                                          ("sma200_ratio", 0.883), ("pos_52w", 0.13),
                                          ("vol_180d", 0.32), ("max_dd_252d", -0.40),
                                          ("dol_vol_20d", 5.2e9)]),
            ("SELECT count(*) FROM stock_scores", [(436,)]),
            ("SELECT symbol, rank FROM stock_scores", [("TCS.NS", 107), ("TCS.NS", 129)]),
            ("FROM stock_scores", [(day(9), 56.0, 107, 0.73, 0.77, 0.30, 0.27, 0.73, True,
                                    0.93)]),
            ("DISTINCT ON (metric)", [("pe", 20.0, self.SNAP), ("pb", 4.0, self.SNAP),
                                      ("market_cap", 1000e9, self.SNAP),
                                      ("ev_ebitda", 10.0, self.SNAP), ("roe", 0.48, self.SNAP),
                                      ("operating_margin", 0.24, self.SNAP)]),
            ("metric, source, ts, value", [
                ("total_revenue", "yfinance", fy(2026), 110.0),
                ("total_revenue", "yfinance", fy(2025), 100.0),
                ("fcf", "yfinance", fy(2026), 55e9),
                ("total_debt", "yfinance", fy(2026), 50.0),
                ("shareholder_equity", "yfinance", fy(2026), 200.0)]),
            ("FROM v_latest_valuations", [(1800.0, -18.2, 6)]),
            ("FROM headline_entities", [(day(8), "TCS Q2 <live>", "ET", -0.82, "results")]),
        ])

    def test_sections_and_headline(self):
        text = stocks.stock_card(self.db(), self.ROW)
        self.assertTrue(text.startswith(
            "<b>Tata Consultancy &amp; Co</b>\nTCS.NS · IT · NIFTY500\n\n"))
        self.assertIn("<b>2,200.00 INR</b>  +0.46%  <i>09 Oct close</i>", text)
        for section in ("Performance", "Trend and risk", "Screener", "Valuation",
                        "Latest fiscal year", "News"):
            self.assertIn(f"<b>{section}</b>", text)
        self.assertIn("rank <b>107</b> of 436", text)
        v = pairs(text)
        # coverage .93 → 37.2 · 3 of 5 factors ≥ .5 → 18 · rank moved 22/436 → 22.4
        self.assertEqual(v["Confidence"], "78 high")
        self.assertEqual(v["Year to date"], "-33.3%")              # 2200 vs 3300
        self.assertEqual(v["vs 200-day average"], "-11.7%")
        self.assertEqual(v["Volatility, annualised"], "32%")
        self.assertEqual(v["Traded per day, 20d avg"], "5.20B")
        self.assertEqual(v["Revenue"], "+10.0%")
        self.assertNotIn("EPS", v)                                 # no EPS history: no row
        self.assertEqual(v["Debt / equity"], "0.25")
        self.assertIn("Profile: <b>Defensive Quality</b> · drivers: Quality (77th), Value (73rd), Low-Risk (73rd)",
                      text.replace("73th", "73rd"))
        self.assertEqual(stocks.stock_archetype((0.60, 0.85, 0.75, 0.60, 0.65)), "Quality Compounder")
        self.assertEqual(stocks.stock_archetype((0.65, 0.65, 0.80, 0.50, 0.50)), "GARP")
        self.assertEqual(stocks.stock_archetype((0.85, 0.60, 0.40, 0.40, 0.50)), "Deep Value")
        self.assertEqual(stocks.stock_archetype((0.80, 0.25, 0.30, 0.20, 0.45)), "Value Trap")
        self.assertEqual(stocks.stock_archetype((0.40, 0.25, 0.30, 0.40, 0.25)), "High-Risk / Stressed")
        self.assertEqual(stocks.stock_archetype((0.25, 0.30, 0.25, 0.25, 0.35)), "Lagging / Weak")
        self.assertEqual(stocks.academic_archetype((0.60, 0.85, 0.75, 0.60, 0.65)),
                         "QMJ + BAB Compounder (Asness 2019 / Frazzini 2014)")
        self.assertEqual(stocks.academic_archetype((0.85, 0.65, 0.40, 0.40, 0.50)),
                         "Piotroski-Filtered Value (Piotroski 2000 / Novy-Marx 2013)")
        self.assertEqual(stocks.academic_archetype((0.20, 0.40, 0.80, 0.50, 0.50)),
                         "Unprofitable Glamour Risk (Lakonishok 1994)")
        self.assertAlmostEqual(stocks.academic_composite((0.60, 0.80, 0.70, 0.50, 0.90)), 70.0)
        self.assertIn("Practitioner vs Academic lens:", text)
        sci = stocks.scientific_diagnostics({
            "total_assets": (1000.0, 920.0, fy(2026)),
            "gross_profit": (350.0, 300.0, fy(2026)),
            "net_income": (120.0, 100.0, fy(2026)),
            "ocf": (160.0, 130.0, fy(2026)),
            "total_revenue": (800.0, 700.0, fy(2026)),
            "total_debt": (100.0, 120.0, fy(2026)),
            "shareholder_equity": (600.0, 500.0, fy(2026)),
            "current_assets": (400.0, 350.0, fy(2026)),
            "current_liabilities": (150.0, 150.0, fy(2026)),
            "shares_outstanding": (98.0, 100.0, fy(2026)),
            "operating_income": (180.0, 150.0, fy(2026)),
        })
        self.assertAlmostEqual(sci["gpa"], 0.35)
        self.assertAlmostEqual(sci["accruals"], -0.04)
        self.assertAlmostEqual(sci["share_issuance"], -0.02)
        self.assertEqual(sci["f_score"], (9, 9, "strong"))
        self.assertEqual(sci["altman_z2"][1], "safe")
        self.assertIn("Practitioner vs Research-Backed valuations:", text)
        self.assertIn("[Practitioner —", text)
        self.assertIn("[Research-Backed —", text)
        self.assertIn("• TCS Q2 &lt;live&gt;", text)
        self.assertIn("08 Oct · results · sentiment -0.82", text)

    def test_valuation_is_carried_to_the_latest_close(self):
        text = stocks.stock_card(self.db(snapshot_close=2000.0), self.ROW)
        v = pairs(text)
        # a repriced value must never sit under an "as reported" label
        self.assertNotIn("as reported", text)
        self.assertIn("<i>at the 09 Oct close — P/E, P/B and market cap scaled from "
                      "the 04 Oct snapshot by the price move (earnings, book value "
                      "and shares assumed unchanged)</i>", text)
        self.assertEqual(v["P/E"], "22.0")                         # 20 × 2200/2000
        self.assertEqual(v["P/B"], "4.4")
        self.assertEqual(v["Market cap"], "1.10T")
        self.assertEqual(v["FCF yield"], "5.0%")                   # 55B / 1.1T
        self.assertEqual(v["EV/EBITDA (04 Oct)"], "10.0")          # not repriced, dated

    def test_valuation_falls_back_to_the_snapshot_and_says_so(self):
        text = stocks.stock_card(self.db(snapshot_close=None), self.ROW)
        self.assertIn("<i>as reported 04 Oct — from the weekly fundamentals snapshot</i>", text)
        self.assertEqual(pairs(text)["P/E"], "20.0")

    def test_card_fits_a_phone(self):
        for line in pre_lines(stocks.stock_card(self.db(), self.ROW)):
            self.assertLessEqual(len(line), PHONE_WIDTH, line)

    def test_no_prices(self):
        text = stocks.stock_card(FakeDB([("FROM stock_prices", [])]), self.ROW)
        self.assertIn("No price rows", text)

    def test_symbol_narrative_strips_prefix_when_enabled(self):
        import os
        db = FakeDB([("FROM llm_narratives", [("per_symbol", "[TCS.NS#abc123] TCS trades at 22 P/E.")])])
        orig = os.environ.get("STOCK_NARRATIVE")
        os.environ["STOCK_NARRATIVE"] = "1"
        try:
            self.assertEqual(stocks.symbol_narrative(db, "TCS.NS"), "TCS trades at 22 P/E.")
        finally:
            if orig is None:
                os.environ.pop("STOCK_NARRATIVE", None)
            else:
                os.environ["STOCK_NARRATIVE"] = orig


class WatchParseTest(unittest.TestCase):
    def test_list(self):
        self.assertEqual(stocks.parse_watch("/watch")[0], "list")

    def test_add_watch_only(self):
        self.assertEqual(stocks.parse_watch("/watch add TCS"),
                         ("add", "TCS", None, None, None, None))

    def test_add_position_with_note(self):
        self.assertEqual(stocks.parse_watch("/watch add TCS 10 3500.5 core holding"),
                         ("add", "TCS", 10.0, 3500.5, "core holding", None))

    def test_add_note_only(self):
        self.assertEqual(stocks.parse_watch("/watch add TCS wait for results"),
                         ("add", "TCS", None, None, "wait for results", None))

    def test_rejects_non_positive_and_nan(self):
        for bad in ("/watch add TCS 0 100", "/watch add TCS 10 -5", "/watch add TCS nan 5"):
            self.assertIsNotNone(stocks.parse_watch(bad)[5], bad)

    def test_rm_and_usage(self):
        self.assertEqual(stocks.parse_watch("/watch rm TCS")[:2], ("rm", "TCS"))
        self.assertIsNotNone(stocks.parse_watch("/watch rm")[5])
        self.assertEqual(stocks.parse_watch("/watch frobnicate")[5], stocks.WATCH_USAGE)


class WatchBlockTest(unittest.TestCase):
    def rows(self):
        return [
            # symbol, qty, cost, note, name, currency, ts, close, prev, rank
            ("TCS.NS", 10.0, 2000.0, "core <b>", "TCS Ltd.", "INR", day(9), 2200.0, 2000.0, 212),
            ("INFY.NS", None, None, None, "Infosys", "INR", day(9), 1500.0, 1515.0, None),
            ("AAPL", 2.0, 400.0, None, "Apple", "USD", day(8), 340.0, 336.0, 40),
            ("GONE.NS", None, None, None, None, None, None, None, None, None),
        ]

    def test_table_totals_and_notes(self):
        lines = stocks.watch_block(self.rows(), {"TCS.NS": -0.333, "AAPL": 0.252})
        table = pre_lines(lines[0])
        self.assertEqual(table[0].split(), ["Close", "Day", "YTD", "P&L"])
        self.assertEqual(table[1].split(), ["TCS", "2,200.0", "+10.0%", "-33%", "+10%"])
        self.assertEqual(table[2].split(), ["INFY", "1,500.0", "-1.0%"])       # watch-only
        self.assertEqual(table[3].split(), ["AAPL", "340.00", "+1.2%", "+25%", "-15%"])
        self.assertEqual(table[4].split(), ["GONE", "n/a"])
        inr = next(line for line in lines if line.startswith("Holdings INR"))
        self.assertIn("<b>22,000</b>", inr)
        self.assertIn("<b>+2,000</b> (+10.0%)", inr)
        self.assertIn("today +2,000", inr)
        self.assertIn("<b>-120</b> (-15.0%)",
                      next(line for line in lines if line.startswith("Holdings USD")))
        self.assertIn("<i>TCS: core &lt;b&gt;</i>", lines)

    def test_no_pnl_column_without_holdings_and_no_notes_for_the_digest(self):
        lines = stocks.watch_block(self.rows()[1:2], notes=False)
        self.assertEqual(pre_lines(lines[0])[0].split(), ["Close", "Day", "YTD"])
        self.assertEqual(len(lines), 1)
        with_note = stocks.watch_block(self.rows()[:1], notes=False)
        self.assertFalse(any("core" in line for line in with_note))

    def test_fits_a_phone(self):
        for line in pre_lines(stocks.watch_block(self.rows(), {"TCS.NS": -0.333})[0]):
            self.assertLessEqual(len(line), PHONE_WIDTH, line)

    def test_watch_text_empty(self):
        self.assertIn("Watchlist is empty", stocks.watch_text(FakeDB([("FROM watchlist", [])])))


class TopTest(unittest.TestCase):
    def test_parse_defaults_and_words_in_any_order(self):
        self.assertEqual(stocks.parse_top("/top"), ("composite", "NIFTY500", None))
        self.assertEqual(stocks.parse_top("/top sp500 growth"), ("growth", "SP500", None))
        self.assertEqual(stocks.parse_top("/top VALUE us"), ("value", "SP500", None))
        self.assertIsNotNone(stocks.parse_top("/top bogus")[2])

    def db(self):
        return FakeDB([("GROUP BY ts", [(day(9),)]),
                       ("FROM stock_scores s", [
                           ("A.NS", "Alpha & Sons", 0.99, 61.0, 12, 1.0, .9, .8, .99, .7, .6),
                           ("B.NS", "Beta", 0.95, 70.2, 1, 0.5, .9, .2, .95, .1, None)]),
                       ("SELECT count(*) FROM stock_scores", [(400,)]),
                       ("SELECT symbol, rank FROM stock_scores", [("A.NS", 12), ("A.NS", 14)]),
                       ("ts < %s::timestamptz", [("A.NS", 100.0)]),
                       ("interval '15 days'", [("A.NS", 140.0)])])

    def test_factor_table(self):
        db = self.db()
        text = stocks.top_text(db, "growth", "NIFTY500")
        self.assertIn("ORDER BY s.growth_score DESC", db.calls[1][0])
        lines = pre_lines(text)
        self.assertEqual(lines[0].split(), ["Growth", "Rank", "YTD", "Conf"])
        self.assertEqual(lines[1].split(), ["A", "0.99", "12", "+40%", "99"])
        self.assertEqual(lines[2].split(), ["B", "0.95", "1", "47"])   # no YTD, thin evidence
        self.assertIn("not the chance the price rises", text)
        for line in lines:
            self.assertLessEqual(len(line), PHONE_WIDTH, line)
        self.assertIn("A — Alpha &amp; Sons", text)
        self.assertIn("run of 09 Oct", text)
        self.assertIn("not advice", text)

    def test_composite_table_shows_rank_first(self):
        lines = pre_lines(stocks.top_text(self.db(), "composite", "NIFTY500"))
        self.assertEqual(lines[0].split(), ["#", "Composite", "YTD", "Conf"])
        self.assertEqual(lines[1].split(), ["12", "A", "61.0", "+40%", "99"])

    def test_factor_column_comes_from_the_whitelist(self):
        with self.assertRaises(KeyError):
            stocks.top_text(self.db(), "growth_score; DROP TABLE x", "NIFTY500")

    def test_no_full_run(self):
        self.assertIn("No full screener run",
                      stocks.top_text(FakeDB([("GROUP BY ts", [])]), "value", "SP500"))


class ConfidenceTest(unittest.TestCase):
    def test_labels(self):
        self.assertEqual([stocks.conf_label(x) for x in (70, 69, 45, 44)],
                         ["high", "medium", "medium", "low"])

    def test_full_evidence_scores_100(self):
        self.assertEqual(stocks.screener_confidence(1.0, [.9] * 5, [10, 10, 10], 400), 100)

    def test_each_component(self):
        full = stocks.screener_confidence(1.0, [.9] * 5, [10, 10], 400)
        self.assertEqual(full - stocks.screener_confidence(0.5, [.9] * 5, [10, 10], 400), 20)
        self.assertEqual(full - stocks.screener_confidence(1.0, [.9, .9, .1, .1, None],
                                                           [10, 10], 400), 18)
        self.assertEqual(full - stocks.screener_confidence(1.0, [.9] * 5, [10, 90], 400), 30)
        self.assertEqual(full - stocks.screener_confidence(1.0, [.9] * 5, [10], 400), 15)

    def test_missing_coverage_counts_as_none(self):
        self.assertEqual(stocks.screener_confidence(None, [.9] * 5, [10, 10], 400), 60)

    def test_rank_history_groups_by_symbol(self):
        db = FakeDB([("SELECT symbol, rank", [("A", 1), ("B", 7), ("A", 3)])])
        self.assertEqual(stocks.rank_history(db, ["B", "A", "A"]), {"A": [1, 3], "B": [7]})
        self.assertEqual(db.calls[0][1], (["A", "B"],))


class MultibaggerTest(unittest.TestCase):
    def annual(self, rev=(100.0, 172.8), eps=(10.0, 19.5), debt=20.0, equity=100.0, fcf=5.0):
        """Three fiscal years apart: 172.8/100 → 20%/yr, 19.5/10 → ~25%/yr."""
        return {"total_revenue": [(fy(2026), rev[1]), (fy(2023), rev[0])],
                "eps": [(fy(2026), eps[1]), (fy(2023), eps[0])],
                "net_income": [(fy(2026), 30.0), (fy(2023), 15.0)],
                "total_debt": [(fy(2026), debt)], "shareholder_equity": [(fy(2026), equity)],
                "fcf": [(fy(2026), fcf)]}

    def candidate(self, sector="Industrials", snap=None, metrics=None, size=0.3, **annual):
        return stocks.multibagger_candidate(
            "X.NS", sector, self.annual(**annual),
            {"pe": 25.0, "roe": 0.22, "market_cap": 5e10, **(snap or {})},
            {"sma200_ratio": 1.1, "ret_12m": 0.4, "pos_52w": 0.8, **(metrics or {})}, size)

    def test_cagr(self):
        self.assertAlmostEqual(stocks.cagr([(fy(2026), 172.8), (fy(2023), 100.0)]), 0.20,
                               places=3)
        self.assertIsNone(stocks.cagr([(fy(2026), 110.0), (fy(2025), 100.0)]))   # one year only
        self.assertIsNone(stocks.cagr([(fy(2026), 50.0), (fy(2023), -10.0)]))    # from a loss
        self.assertIsNone(stocks.cagr([(fy(2026), 50.0)]))
        # terminal-year collapse (>15% drop vs prior year) rejected despite positive 3y endpoint CAGR
        self.assertIsNone(stocks.cagr([(fy(2026), 160.0), (fy(2025), 320.0), (fy(2023), 100.0)]))

    def test_a_clean_company_passes_and_is_scored(self):
        got, reason = self.candidate()
        self.assertIsNone(reason)
        self.assertAlmostEqual(got["rev"], 0.20, places=3)
        self.assertAlmostEqual(got["peg"], 25.0 / (got["earn"] * 100))
        self.assertTrue(0 < got["score"] <= 100)
        # 3 fiscal years 30 · EPS+income both compound 20 · slower rate 20% clears the
        # 15% gate by a third 5 · FCF>0 15 · reported ROE 10 · upper half of range 10
        self.assertEqual(got["conf"], 90)

    def test_confidence_drops_with_thinner_evidence(self):
        full = self.candidate()[0]["conf"]
        self.assertEqual(full - self.candidate(fcf=-5.0)[0]["conf"], 15)
        self.assertEqual(full - self.candidate(snap={"roe": None})[0]["conf"], 10)
        self.assertEqual(full - self.candidate(metrics={"pos_52w": 0.2})[0]["conf"], 10)

    def test_each_gate_rejects_with_its_reason(self):
        cases = {
            "growth history": dict(rev=(-5.0, 172.8)),
            "growth": dict(rev=(100.0, 120.0)),
            "return on equity": dict(snap={"roe": 0.08}),
            "debt": dict(debt=250.0),
            "size": dict(size=0.9),
            "valuation": dict(snap={"pe": 90.0}),
            "trend": dict(metrics={"sma200_ratio": 0.93}),
        }
        for reason, kwargs in cases.items():
            self.assertEqual(self.candidate(**kwargs), (None, reason), reason)

    def test_financials_are_exempt_from_the_debt_gate(self):
        self.assertIsNone(self.candidate(sector="Financial Services", debt=900.0)[1])
        self.assertIsNone(self.candidate(sector="Financials", debt=900.0)[1])

    def test_roe_falls_back_to_income_over_equity(self):
        got, reason = self.candidate(snap={"roe": None})          # 30 / 100 = 30%
        self.assertIsNone(reason)
        self.assertAlmostEqual(got["roe"], 0.30)

    def test_faster_growth_and_smaller_size_score_higher(self):
        base = self.candidate()[0]["score"]
        self.assertGreater(self.candidate(rev=(100.0, 250.0))[0]["score"], base)
        self.assertGreater(self.candidate(size=0.05)[0]["score"], base)
        self.assertLess(self.candidate(fcf=-5.0)[0]["score"], base)

    def rank_inputs(self):
        names = [("GOOD.NS", "Good & Co", "Industrials"), ("BIG.NS", "Big", "Industrials"),
                 ("SLOW.NS", "Slow", "Industrials"), ("NODATA.NS", "No Data", "Industrials"),
                 ("TINY1.NS", "Tiny 1", "IT"), ("TINY2.NS", "Tiny 2", "IT")]
        # two more small caps (cap only) so BIG lands in the largest 30%
        fundamentals = [(s, "market_cap", "yfinance", day(4), 5e9)
                        for s in ("TINY1.NS", "TINY2.NS")]
        metric_rows = []
        for sym, cap, rev_end in (("GOOD.NS", 1e10, 172.8), ("BIG.NS", 9e12, 172.8),
                                  ("SLOW.NS", 2e10, 105.0)):
            for metric, vals in self.annual(rev=(100.0, rev_end)).items():
                fundamentals += [(sym, metric, "yfinance", ts, v) for ts, v in vals]
            fundamentals += [(sym, "pe", "yfinance", day(4), 25.0),
                             (sym, "pe", "yfinance", day(1), 999.0),      # older snapshot
                             (sym, "roe", "yfinance", day(4), 0.22),
                             (sym, "market_cap", "yfinance", day(4), cap)]
            metric_rows += [(sym, "sma200_ratio", 1.1), (sym, "ret_12m", 0.4),
                            (sym, "pos_52w", 0.8)]
        return names, fundamentals, metric_rows

    def test_rank_uses_the_newest_snapshot_and_counts_the_funnel(self):
        ranked, failed = stocks.multibagger_rank(*self.rank_inputs())
        self.assertEqual([c["symbol"] for c in ranked], ["GOOD.NS"])
        self.assertEqual(failed, {"size": 1, "growth": 1, "growth history": 3})
        acad_ranked, acad_failed = stocks.multibagger_academic_rank(*self.rank_inputs())
        self.assertEqual([c["symbol"] for c in acad_ranked], ["GOOD.NS", "SLOW.NS"])
        self.assertIn("size (SMB)", acad_failed)

    def test_message(self):
        names, fundamentals, metric_rows = self.rank_inputs()
        db = FakeDB([("FROM universe_constituents WHERE universe", names),
                     ("FROM stock_fundamentals f", fundamentals),
                     ("FROM stock_metrics_daily m", metric_rows)])
        text = stocks.multibagger_text(db, "NIFTY500")
        lines = pre_lines(text)
        self.assertEqual(lines[0].split(), ["Score", "Rev", "EPS", "ROE", "Conf"])
        self.assertEqual(lines[1].split()[0], "GOOD")
        self.assertEqual(lines[1].split()[2:], ["+20%", "+25%", "22%", "90"])
        self.assertIn("1 of 6 companies pass every gate", text)
        self.assertIn("GOOD — Good &amp; Co · cap 10.00B · PEG 1.0 · confidence high", text)
        self.assertIn("Dropped: 3 on growth history, 1 on growth, 1 on size.", text)
        self.assertIn("not the chance the price rises", text)
        self.assertIn("Not advice", text)
        for line in lines:
            self.assertLessEqual(len(line), PHONE_WIDTH, line)

    def test_message_when_nothing_passes(self):
        db = FakeDB([("FROM universe_constituents WHERE universe", [("A", "A", "IT")]),
                     ("FROM stock_fundamentals f", []), ("FROM stock_metrics_daily m", [])])
        self.assertIn("No company passes every gate", stocks.multibagger_text(db, "SP500"))


class MoversTest(unittest.TestCase):
    def test_diff(self):
        latest = {"A": ("Alpha", 1), "C": ("Gamma", 3)}
        previous = {"A": ("Alpha", 2), "B": ("Beta", 5)}
        self.assertEqual(stocks.diff_shortlist(latest, previous),
                         ([("C", "Gamma", 3)], [("B", "Beta", 5)]))

    def test_needs_two_full_runs(self):
        self.assertIsNone(stocks.movers(FakeDB([("GROUP BY ts", [(day(9),)])]), "SP500"))

    def test_lines(self):
        self.assertEqual(stocks.movers_lines("SP500", (day(9), day(8), [], [])), [])
        lines = stocks.movers_lines(
            "NIFTY500", (day(9), day(8), [("NEW.NS", "Newco", 3)], [("OLD.NS", "Oldco", 4)]),
            {"NEW.NS": 0.5})
        self.assertEqual(lines[0], "<b>NIFTY500 top 20</b>  <i>08 Oct → 09 Oct</i>")
        self.assertEqual(lines[1], "▲ In at #3: <b>Newco</b> (NEW) · YTD +50.0%")
        self.assertEqual(lines[2], "▼ Out, was #4: Oldco (OLD)")


class TrackTest(unittest.TestCase):
    def test_summary_averages_runs_and_counts_wins(self):
        rows = [
            # n, run_ts, picks_ret, universe_ret, picks, winners
            (1, day(5), 0.02, 0.01, 20, 14),
            (1, day(6), -0.01, 0.00, 20, 6),
            (1, day(7), None, 0.01, 0, 0),      # run with no priced picks: ignored
            (5, day(5), 0.03, 0.04, 20, 11),
        ]
        s = stocks.summarise_track(rows)
        self.assertEqual(s[1]["runs"], 2)
        self.assertAlmostEqual(s[1]["picks"], 0.005)
        self.assertAlmostEqual(s[1]["univ"], 0.005)
        self.assertEqual(s[1]["beat"], 1)
        self.assertEqual((s[1]["winners"], s[1]["n_picks"]), (20, 40))
        self.assertEqual(s[5]["beat"], 0)

    def test_bounds_are_parameters_so_chunks_prune(self):
        db = FakeDB([("GROUP BY ts", [(day(5),), (day(6),)]), ("WITH px", [])])
        stocks.track_rows(db, "SP500")
        sql, args = db.calls[-1]
        self.assertEqual((args["start"], args["runs"]), (day(5), [day(5), day(6)]))
        self.assertIn("ts >= %(start)s::timestamptz", sql)
        self.assertNotIn("SELECT min(ts)", sql)

    def test_table_marks_unfinished_horizons(self):
        db = FakeDB([("GROUP BY ts", [(day(5),)]),
                     ("WITH px", [(1, day(5), 0.0073, 0.0066, 20, 12)])])
        text = stocks.track_text(db)
        lines = pre_lines(text)
        self.assertEqual(lines[0].split(), ["After", "Picks", "Univ", "Edge", "Won"])
        self.assertEqual(lines[1].split(), ["1", "day", "+0.73%", "+0.66%", "+0.07%", "1/1"])
        self.assertEqual(lines[2].split(), ["1", "week", "–", "–", "–", "–"])
        self.assertIn("anecdote", text)
        self.assertIn("Edge = Picks − Univ", text)
        for line in lines:
            self.assertLessEqual(len(line), PHONE_WIDTH, line)

    def test_table_fits_a_phone_at_triple_digit_run_counts(self):
        # daily runs pass 100 within months; 'Won' = '100/120' must still fit
        rows = [(1, day(i % 9 + 1), 0.0073, 0.0066, 20, 12) for i in range(120)]
        db = FakeDB([("GROUP BY ts", [(day(5),)]), ("WITH px", rows)])
        lines = pre_lines(stocks.track_text(db))
        self.assertIn("120/120", lines[1])
        for line in lines:
            self.assertLessEqual(len(line), PHONE_WIDTH, line)


class MacroTest(unittest.TestCase):
    def test_sections(self):
        db = FakeDB([
            ("ticker = 'GC=F'", [(day(9), 4215.80), (day(8), 4155.50)]),
            ("FROM fx_rates", [(96.78,)]),
            ("FROM bond_yields", [("^TNX", day(8), 5.231), ("^IRX", day(8), 4.043),
                                  ("IN_GS_10Y", day(8), 7.2898)]),
            ("FROM capital_flows", [(7, 0.857, -56338.0, -0.024)]),
        ])
        text = stocks.macro_text(db)
        self.assertIn("<b>131,177</b> per 10 g  +1.45%", text)
        v = pairs(text)
        self.assertEqual(v["US 10Y - 3M"], "+119 bp")
        self.assertEqual(v["India 10Y - US 10Y"], "+206 bp")
        self.assertNotIn("US 30Y - 5Y", v)                         # inputs missing
        self.assertEqual(v["FII net, Rs cr"], "-56,338")
        self.assertEqual(v["Daily correlation"], "+0.86")

    def test_nothing_available(self):
        db = FakeDB([("ticker = 'GC=F'", []), ("FROM bond_yields", []),
                     ("FROM capital_flows", [(0, None, None, None)])])
        self.assertEqual(stocks.macro_text(db), "No macro inputs in the last 15 days.")


class AlertTest(unittest.TestCase):
    NOW = datetime(2026, 10, 10, 9, 45, tzinfo=timezone.utc)

    def test_fresh_means_today_or_yesterday(self):
        self.assertTrue(stocks.is_fresh(day(10), self.NOW))
        self.assertTrue(stocks.is_fresh(day(9), self.NOW))
        self.assertFalse(stocks.is_fresh(day(8), self.NOW))
        self.assertFalse(stocks.is_fresh(None, self.NOW))

    def test_cross_detected_only_on_fresh_data(self):
        rows = [
            ("UP.NS", "sma200_ratio", day(9), 1.02), ("UP.NS", "sma200_ratio", day(8), 0.99),
            ("UP.NS", "sma200_ratio", day(7), 1.05),   # older rows never matter
            ("DN.NS", "sma50_ratio", day(9), 0.97), ("DN.NS", "sma50_ratio", day(8), 1.01),
            ("FLAT.NS", "sma200_ratio", day(9), 1.03), ("FLAT.NS", "sma200_ratio", day(8), 1.01),
            ("OLD.NS", "sma200_ratio", day(6), 1.02), ("OLD.NS", "sma200_ratio", day(5), 0.99),
            ("ONE.NS", "sma200_ratio", day(9), 1.02),
        ]
        self.assertEqual(stocks.crosses(rows, self.NOW), [
            ("DN.NS", "sma50_ratio", "below", day(9)),
            ("UP.NS", "sma200_ratio", "above", day(9)),
        ])

    def test_sections_silent_without_watchlist_or_changes(self):
        db = FakeDB([("GROUP BY ts", [(day(9),), (day(8),)]),
                     ("s.rank <=", [(day(9), "A", "Alpha", 1), (day(8), "A", "Alpha", 1)]),
                     ("FROM v_valuations_recent", []),
                     ("FROM watchlist", []), ("FROM stock_prices", [])])
        self.assertEqual(stocks.alert_sections(db, self.NOW), [])

    def test_sections_report_mover_big_move_cross_and_event(self):
        from datetime import date as _date
        db = FakeDB([
            ("GROUP BY ts", [(day(9),), (day(8),)]),
            ("s.rank <=", [(day(9), "NEW", "Newco", 3), (day(8), "OLD", "Oldco", 4)]),
            ("FROM watchlist", [("TCS.NS", None, None, None, "TCS", "INR",
                                 day(9), 106.0, 100.0, 5)]),
            ("FROM stock_metrics_daily", [("TCS.NS", "sma200_ratio", day(9), 1.01),
                                          ("TCS.NS", "sma200_ratio", day(8), 0.98)]),
            ("FROM headline_events", [("TCS.NS", "results", "TCS Q2 profit up <8%>", day(9))]),
            ("FROM earnings_calendar", [("TCS.NS", _date(2026, 10, 12))]),
            ("FROM v_valuations_recent", []),
            ("FROM stock_prices", []),                                   # ytd lookups
        ])
        text = "\n".join(stocks.alert_sections(db, self.NOW))
        self.assertIn("▲ In at #3: <b>Newco</b>", text)
        self.assertIn("▼ Out, was #4: Oldco", text)
        self.assertIn("<b>Your watchlist</b>", text)
        self.assertIn("<b>TCS</b> moved +6.0%", text)
        self.assertIn("<b>TCS</b> crossed above its 200-day average", text)
        self.assertIn("<b>TCS</b> · results\n  TCS Q2 profit up &lt;8%&gt;", text)
        self.assertIn("<b>TCS</b> reports earnings Mon 12 Oct", text)

    def test_stale_screener_run_is_not_re_announced(self):
        db = FakeDB([("GROUP BY ts", [(day(7),), (day(6),)]),
                     ("s.rank <=", [(day(7), "NEW", "Newco", 3), (day(6), "OLD", "Oldco", 4)]),
                     ("FROM v_valuations_recent", []), ("FROM watchlist", [])])
        self.assertEqual(stocks.alert_sections(db, self.NOW), [])

    def test_missing_watchlist_table_degrades(self):
        class UndefinedTable(Exception):
            pass

        def boom(_args):
            raise UndefinedTable("relation watchlist does not exist")

        db = FakeDB([("GROUP BY ts", []), ("FROM v_valuations_recent", []),
                     ("FROM watchlist", boom)])
        self.assertEqual(stocks.alert_sections(db, self.NOW), [])


class ValuationWatchTest(unittest.TestCase):
    """ADR 0026 amendment (owner-approved 2026-10-26): buy-watch alert lines."""
    NOW = datetime(2026, 10, 10, 9, 45, tzinfo=timezone.utc)

    @staticmethod
    def cand(ts, sym, close=100.0, fair=140.0, up=40.0, methods=6, above=5,
             universe="SP500"):
        return (ts, sym, universe, close, fair, up, methods, above)

    def db(self, cands, ctx, earnings=()):
        return FakeDB([("FROM v_valuations_recent", cands),
                       ("sector_styles st", ctx),
                       ("FROM earnings_calendar", list(earnings))])

    def test_entry_fires_with_reasoning_and_lens(self):
        db = self.db([self.cand(day(9), "CHEAP"), self.cand(day(8), "OTHER")],
                     [("CHEAP", 12, 71.0, "Cheap & Co <L>", "Financials",
                       "Banks: leverage is the business model")])
        lines = stocks.alert_valuation(db, self.NOW)
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertIn("Cheap &amp; Co &lt;L&gt;", line)          # escaped
        self.assertIn("fair <b>140.00</b> vs close 100.00 (+40%", line)
        self.assertIn("5/6 methods above close", line)
        self.assertIn("rank <b>#12</b>", line)
        self.assertIn("Financials", line)
        self.assertIn("not advice", line)

    def test_already_in_the_set_yesterday_stays_silent(self):
        db = self.db([self.cand(day(9), "CHEAP"), self.cand(day(8), "CHEAP")],
                     [("CHEAP", 12, 71.0, "Cheap", "Financials", None)])
        self.assertEqual(stocks.alert_valuation(db, self.NOW), [])

    def test_stale_valuation_run_never_fires(self):
        db = self.db([self.cand(day(6), "CHEAP")], [])
        self.assertEqual(stocks.alert_valuation(db, self.NOW), [])

    def test_rank_gate_names_outside_top50_dropped(self):
        db = self.db([self.cand(day(9), "CHEAP")], [])   # ctx query returns nothing
        self.assertEqual(stocks.alert_valuation(db, self.NOW), [])

    def test_agreement_gate_needs_three_methods_above_close(self):
        db = self.db([self.cand(day(9), "CHEAP", above=2)], [])
        self.assertEqual(stocks.alert_valuation(db, self.NOW), [])

    def test_capped_at_five_lines_best_upside_first(self):
        cands = [self.cand(day(9), f"S{i}", up=20.0 + i) for i in range(1, 8)]
        ctx = [(f"S{i}", i, 60.0, f"Name{i}", "Energy", None) for i in range(1, 8)]
        lines = stocks.alert_valuation(self.db(cands, ctx), self.NOW)
        self.assertEqual(len(lines), stocks.VAL_MAX_LINES)
        self.assertIn("Name7", lines[0])                 # highest upside leads

    def test_missing_table_degrades_silently(self):
        class UndefinedTable(Exception):
            pass

        def boom(_args):
            raise UndefinedTable("relation v_valuations_recent does not exist")

        db = FakeDB([("FROM v_valuations_recent", boom)])
        self.assertEqual(stocks.alert_valuation(db, self.NOW), [])

    def test_earnings_within_three_days_suppresses_the_line(self):
        from datetime import date as _date
        db = self.db([self.cand(day(9), "CHEAP")],
                     [("CHEAP", 12, 71.0, "Cheap", "Financials", None)],
                     earnings=[("CHEAP", _date(2026, 10, 12))])
        self.assertEqual(stocks.alert_valuation(db, self.NOW), [])

    def test_missing_earnings_table_does_not_block_the_line(self):
        class UndefinedTable(Exception):
            pass

        def boom(_args):
            raise UndefinedTable("relation earnings_calendar does not exist")

        db = FakeDB([("FROM v_valuations_recent", [self.cand(day(9), "CHEAP")]),
                     ("sector_styles st",
                      [("CHEAP", 12, 71.0, "Cheap", "Financials", None)]),
                     ("FROM earnings_calendar", boom)])
        self.assertEqual(len(stocks.alert_valuation(db, self.NOW)), 1)


class RegimeTest(unittest.TestCase):
    def db(self, breadth, trend, mom):
        return FakeDB([("sma200_ratio", [(breadth,)]),
                       ("FROM index_prices", [(trend,)]),
                       ("FROM factor_returns", [(mom,)])])

    def test_all_green(self):
        rows = stocks.regime(self.db(0.72, True, True))
        self.assertEqual(len(rows), 2)
        label, light, parts = rows[0]
        self.assertEqual((label, light), ("US", "🟢"))
        self.assertIn("breadth 72% above 200d", parts[0])

    def test_all_red(self):
        _, light, _ = stocks.regime(self.db(0.31, False, False))[0]
        self.assertEqual(light, "🔴")

    def test_mixed_is_yellow(self):
        _, light, _ = stocks.regime(self.db(0.72, False, True))[0]
        self.assertEqual(light, "🟡")

    def test_stale_momentum_leg_drops_out(self):
        rows = stocks.regime(self.db(0.72, True, None))
        _, light, parts = rows[0]
        self.assertEqual(light, "🟢")          # 2 of 2 available legs green
        self.assertEqual(len(parts), 2)

    def test_regime_line_and_text(self):
        db = self.db(0.72, True, True)
        self.assertIn("🟢 US", stocks.regime_line(db))
        db = self.db(0.72, True, True)
        self.assertIn("<b>US</b>", stocks.regime_text(db))


class EarningsHelpersTest(unittest.TestCase):
    def test_lines_sorted_soonest_first(self):
        from datetime import date as _date
        db = FakeDB([("FROM earnings_calendar",
                      [("MSFT", _date(2026, 10, 29)),
                       ("AAPL.NS", _date(2026, 10, 27))])])
        lines = stocks.earnings_lines(db, ["MSFT", "AAPL.NS"])
        self.assertEqual(len(lines), 2)
        self.assertIn("AAPL", lines[0])
        self.assertIn("Tue 27 Oct", lines[0])

    def test_no_symbols_short_circuits(self):
        db = FakeDB([])
        self.assertEqual(stocks.earnings_soon(db, []), {})
        self.assertEqual(db.calls, [])

    def test_missing_table_returns_empty(self):
        class UndefinedTable(Exception):
            pass

        def boom(_args):
            raise UndefinedTable("relation earnings_calendar does not exist")

        db = FakeDB([("FROM earnings_calendar", boom)])
        self.assertEqual(stocks.earnings_soon(db, ["AAPL"]), {})


class SectorsCardTest(unittest.TestCase):
    ROWS = [("Energy", 21, 18.7, -42.0, "Earnings follow the commodity price cycle"),
            ("Financials", 76, 14.2, -24.0, None)]

    def test_card_lists_sectors_with_upside(self):
        db = FakeDB([("FROM sector_metrics", list(reversed(self.ROWS)))])
        text = stocks.sectors_card(db, "/sectors sp500")
        self.assertIn("<b>Sectors — SP500</b>", text)
        self.assertIn("<pre>", text)
        self.assertIn("Financials", text)
        self.assertIn("-24%", text)
        self.assertIn("not advice", text.lower())

    def test_default_universe_is_nifty(self):
        db = FakeDB([("FROM sector_metrics", self.ROWS)])
        self.assertIn("NIFTY500", stocks.sectors_card(db, "/sectors"))

    def test_empty_table_says_so(self):
        db = FakeDB([("FROM sector_metrics", [])])
        self.assertIn("No sector data", stocks.sectors_card(db, "/sectors sp500"))


class CardFairValueTest(StockCardTest):
    def test_card_carries_the_fair_value_line(self):
        text = stocks.stock_card(self.db(), self.ROW)
        self.assertIn("Fair value <b>1,800.00</b> (-18% vs close, "
                      "median of 6 methods", text)
        self.assertIn("ADR 0026, not advice", text)


class SectorsUniverseParseTest(unittest.TestCase):
    ROWS = [("Energy", 21, 18.7, -42.0, None)]

    def test_sp600_goes_to_sp600_not_sp500(self):
        db = FakeDB([("FROM sector_metrics", lambda args: self.ROWS
                      if args[0] == "SP600" else [])])
        text = stocks.sectors_card(db, "/sectors sp600")
        self.assertIn("SP600", text)
        self.assertIn("Energy", text)

    def test_unknown_universe_is_an_error_not_sp500(self):
        db = FakeDB([])
        text = stocks.sectors_card(db, "/sectors spx")
        self.assertIn("Unknown universe", text)
        self.assertEqual(db.calls, [])

    def test_bare_command_defaults_to_nifty(self):
        db = FakeDB([("FROM sector_metrics", lambda args: self.ROWS
                      if args[0] == "NIFTY500" else [])])
        self.assertIn("NIFTY500", stocks.sectors_card(db, "/sectors"))


class EarningsCommandTest(unittest.TestCase):
    def test_watchlist_names_starred_and_sorted(self):
        from datetime import date as _date
        db = FakeDB([("FROM watchlist", [("AAPL",)]),
                     ("WHERE rank <= 25", [("MSFT",)]),
                     ("FROM earnings_calendar",
                      [("AAPL", _date(2026, 10, 29)), ("MSFT", _date(2026, 10, 28))])])
        text = stocks.earnings_text(db, "123")
        lines = text.splitlines()
        self.assertIn("MSFT", lines[1])          # soonest first
        self.assertIn("AAPL</b> ★", lines[2])    # watchlist starred
    def test_empty_calendar_message(self):
        db = FakeDB([("FROM watchlist", []), ("WHERE rank <= 25", []),
                     ("FROM earnings_calendar", [])])
        self.assertIn("No earnings dates", stocks.earnings_text(db, "123"))


class ScorecardCommandTest(unittest.TestCase):
    def test_counts_without_outcomes(self):
        from datetime import date as _date
        db = FakeDB([("FROM prediction_snapshots GROUP BY kind",
                      [("screener_rank", 1419, _date(2026, 10, 27), _date(2026, 10, 27))]),
                     ("prediction_outcomes", [])])
        text = stocks.scorecard_text(db)
        self.assertIn("1,419 snapshots", text)
        self.assertIn("No resolved outcomes yet", text)

    def test_decile_spread_renders(self):
        from datetime import date as _date
        db = FakeDB([("FROM prediction_snapshots GROUP BY kind",
                      [("screener_rank", 100, _date(2026, 10, 1), _date(2026, 10, 27))]),
                     ("prediction_outcomes", [("screener_rank", 30, 0.042, -0.013, 900)])])
        text = stocks.scorecard_text(db)
        self.assertIn("d1 +4.2% vs d10 -1.3%", text)
        line = stocks.scorecard_line(FakeDB([
            ("prediction_outcomes", [("screener_rank", 30, 0.042, -0.013, 900)])
        ]))
        self.assertIn("top decile <b>+4.2%</b> vs bottom <b>-1.3%</b>", line)

    def test_missing_table_degrades(self):
        class UndefinedTable(Exception):
            pass

        def boom(_args):
            raise UndefinedTable("relation prediction_snapshots does not exist")
        db = FakeDB([("FROM prediction_snapshots GROUP BY kind", boom)])
        self.assertIn("not created yet", stocks.scorecard_text(db))


if __name__ == "__main__":
    unittest.main()
