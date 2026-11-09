#!/usr/bin/env python3
"""Tests for the DQ-2 financial_facts writer (stock_fundamentals.edgar_fact_rows) and the
screener's duration-aware readers (stock_screener.ttm_value). No DB, no network.

    python3 tests/test_financial_facts.py
"""
import datetime as dt
import sys
import types
import unittest
from pathlib import Path

for name in ("psycopg", "yfinance", "requests"):
    sys.modules.setdefault(name, types.ModuleType(name))
try:
    import pandas  # noqa: F401
except ImportError:
    pd_stub = types.ModuleType("pandas")
    pd_stub.Series = object
    sys.modules["pandas"] = pd_stub
SCRIPTS = Path(__file__).resolve().parent.parent / "manifests" / "stock-screener" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import stock_fundamentals as sf  # noqa: E402
import stock_screener as ss  # noqa: E402


def e(end, val, filed, start=None, form="10-K", accn="a"):
    d = {"end": end, "val": val, "filed": filed, "form": form, "accn": accn}
    if start:
        d["start"] = start
    return d


class EdgarFactRows(unittest.TestCase):
    def test_ytd_periods_do_not_collapse(self):
        facts = {"NetIncomeLoss": {"units": {"USD": [
            e("2024-03-31", 98.0, "2024-04-25", "2023-07-01", "10-Q"),   # 9-month YTD
            e("2024-03-31", 22.0, "2024-04-25", "2024-01-01", "10-Q"),   # quarter
            e("2024-06-30", 88.0, "2024-07-30", "2023-07-01"),           # full year
        ]}}}
        rows = sf.edgar_fact_rows(facts)
        self.assertEqual(sorted((r[1], r[3], r[4]) for r in rows),
                         [("2024-03-31", 90, 22.0), ("2024-03-31", 274, 98.0),
                          ("2024-06-30", 365, 88.0)])

    def test_first_release_kept_comparatives_dropped_restatement_kept(self):
        facts = {"NetIncomeLoss": {"units": {"USD": [
            e("2024-06-30", 88.0, "2024-07-30", "2023-07-01"),
            e("2024-06-30", 88.0, "2025-07-30", "2023-07-01"),  # comparative repeat
            e("2024-06-30", 87.5, "2026-07-29", "2023-07-01"),  # restatement
        ]}}}
        rows = sf.edgar_fact_rows(facts)
        self.assertEqual([(r[4], r[5]) for r in rows], [(88.0, "2024-07-30"), (87.5, "2026-07-29")])

    def test_instants_get_duration_zero_and_10_forms_only(self):
        facts = {"StockholdersEquity": {"units": {"USD": [
            e("2024-06-30", 268.0, "2024-07-30"),
            e("2024-06-30", 1.0, "2024-07-30", form="8-K"),
        ]}}}
        rows = sf.edgar_fact_rows(facts)
        self.assertEqual([(r[0], r[3], r[4]) for r in rows], [("shareholder_equity", 0, 268.0)])

    def test_first_tag_wins_per_key(self):
        facts = {
            "Revenues": {"units": {"USD": [e("2024-06-30", 245.0, "2024-07-30", "2023-07-01")]}},
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
                e("2024-06-30", 1.0, "2024-07-30", "2023-07-01"),     # same key: ignored
                e("2023-06-30", 211.0, "2023-07-27", "2022-07-01"),   # new key: kept
            ]}},
        }
        rows = sorted((r[1], r[4]) for r in sf.edgar_fact_rows(facts) if r[0] == "total_revenue")
        self.assertEqual(rows, [("2023-06-30", 211.0), ("2024-06-30", 245.0)])

    def test_ev9_alternate_tags_fill_gaps_without_stealing_keys(self):
        # EV-9: a company that moved from NetIncomeLoss to ProfitLoss keeps its old series
        # under the first tag and gains the new periods from the alternate.
        facts = {
            "NetIncomeLoss": {"units": {"USD": [e("2024-12-31", 100.0, "2025-02-10", "2024-01-01")]}},
            "ProfitLoss": {"units": {"USD": [
                e("2024-12-31", 1.0, "2025-02-10", "2024-01-01"),      # same key: first tag owns it
                e("2025-12-31", 130.0, "2026-02-09", "2025-01-01"),    # new period: filled
            ]}},
            "RegulatedAndUnregulatedOperatingRevenue": {"units": {"USD": [
                e("2025-12-31", 900.0, "2026-02-09", "2025-01-01")]}},  # utility revenue tag
        }
        ni = sorted((r[1], r[4]) for r in sf.edgar_fact_rows(facts) if r[0] == "net_income")
        self.assertEqual(ni, [("2024-12-31", 100.0), ("2025-12-31", 130.0)])
        rev = [(r[1], r[4]) for r in sf.edgar_fact_rows(facts) if r[0] == "total_revenue"]
        self.assertEqual(rev, [("2025-12-31", 900.0)])

    def test_deterministic(self):
        facts = {"NetIncomeLoss": {"units": {"USD": [e("2024-06-30", 88.0, "2024-07-30", "2023-07-01")]}}}
        self.assertEqual(sf.edgar_fact_rows(facts), sf.edgar_fact_rows(facts))


D = dt.date


class TtmValue(unittest.TestCase):
    def test_annual_plus_ytd_minus_prior_ytd(self):
        rows = [(D(2024, 6, 30), 365, 100.0),
                (D(2024, 3, 31), 274, 70.0),    # prior-year 9-month YTD
                (D(2025, 3, 31), 274, 80.0)]    # current 9-month YTD
        self.assertEqual(ss.ttm_value(rows), 110.0)

    def test_falls_back_to_latest_annual(self):
        self.assertEqual(ss.ttm_value([(D(2024, 6, 30), 365, 100.0), (D(2023, 6, 30), 364, 90.0)]), 100.0)
        # YTD after the annual but no matching prior-year period
        self.assertEqual(ss.ttm_value([(D(2024, 6, 30), 365, 100.0), (D(2025, 3, 31), 274, 80.0)]), 100.0)

    def test_single_quarters_are_summed_nse_style(self):
        rows = [(D(2024, 3, 31), 365, 1000.0),
                (D(2023, 6, 30), 91, 240.0), (D(2023, 9, 30), 92, 250.0),     # prior-year Q1, Q2
                (D(2024, 6, 30), 91, 260.0), (D(2024, 9, 30), 92, 270.0)]     # current Q1, Q2 (no YTD)
        self.assertEqual(ss.ttm_value(rows), 1000 + 260 + 270 - 240 - 250)

    def test_quarter_gap_falls_back_to_annual(self):
        rows = [(D(2024, 3, 31), 365, 1000.0), (D(2023, 9, 30), 92, 250.0),
                (D(2024, 9, 30), 92, 270.0)]                                  # Q1 missing
        self.assertEqual(ss.ttm_value(rows), 1000.0)

    def test_stale_annual_is_none_when_as_of_given(self):
        rows = [(D(2017, 12, 31), 364, 2.2)]          # CBOE-style: tag stopped in 2017
        self.assertIsNone(ss.ttm_value(rows, as_of=D(2026, 10, 18)))
        self.assertEqual(ss.ttm_value(rows, as_of=D(2018, 6, 30)), 2.2)

    def test_no_annual_is_none(self):
        self.assertIsNone(ss.ttm_value([(D(2025, 3, 31), 90, 20.0)]))


if __name__ == "__main__":
    unittest.main(verbosity=1)
