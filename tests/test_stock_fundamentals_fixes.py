#!/usr/bin/env python3
"""Regression tests for the 2026-10-10 data fixes (DQ-2, DQ-3):
- stock_fundamentals.py: `failed` is defined (NameError at end of a full pass),
  upsert() keeps the earliest filing_published_at.
- stock_screener.py: flow metrics / annual series read full-year (yfinance) rows only.

No database, no network: psycopg/requests/yfinance are stubbed, cursors are fakes.

    python3 tests/test_stock_fundamentals_fixes.py
"""
import ast
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

for name in ("psycopg", "requests", "yfinance"):
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


class FakeCursor:
    def __init__(self, rows=None, first=None):
        self.executed = []
        self._rows = rows or []
        self._first = first

    def execute(self, sql, params=()):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._first

    def fetchall(self):
        return self._rows


class FailedCounter(unittest.TestCase):
    def test_failed_is_assigned_before_use(self):
        tree = ast.parse((SCRIPTS / "stock_fundamentals.py").read_text())
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        stores = [n for n in ast.walk(main) if isinstance(n, ast.Name)
                  and n.id == "failed" and isinstance(n.ctx, ast.Store)]
        self.assertTrue(stores, "`failed` is read in main() but never assigned")


class UpsertFirstSeen(unittest.TestCase):
    def test_existing_earlier_date_is_passed_to_insert(self):
        earlier = datetime(2026, 10, 4, tzinfo=timezone.utc)
        cur = FakeCursor(first=(earlier,))
        sf.upsert(cur, "TCS.NS", "2025-03-31T00:00:00Z", "total_revenue", 1.0,
                  "yfinance", "2026-10-17T00:00:00Z")
        insert = next(e for e in cur.executed if e[0].startswith("INSERT"))
        self.assertIn("LEAST", insert[0])
        self.assertEqual(insert[1][-1], earlier)
        self.assertEqual(insert[1][-2], "2026-10-17T00:00:00Z")

    def test_new_row_has_no_prior_date(self):
        cur = FakeCursor(first=None)
        sf.upsert(cur, "TCS.NS", "2025-03-31T00:00:00Z", "total_revenue", 1.0,
                  "yfinance", "2026-10-17T00:00:00Z")
        insert = next(e for e in cur.executed if e[0].startswith("INSERT"))
        self.assertIsNone(insert[1][-1])

    def test_none_value_writes_nothing(self):
        cur = FakeCursor()
        self.assertEqual(sf.upsert(cur, "X", "t", "m", None, "yfinance", "p"), 0)
        self.assertEqual(cur.executed, [])


class FullYearOnly(unittest.TestCase):
    def test_annual_series_reads_yfinance_only(self):
        cur = FakeCursor(rows=[])
        ss.annual_series(cur, ["MSFT"], "total_revenue", datetime.now(timezone.utc), 3)
        self.assertIn("source = 'yfinance'", cur.executed[0][0])

    def test_latest_fundamentals_restricts_flow_metrics(self):
        cur = FakeCursor(rows=[])
        ss.latest_fundamentals(cur, ["MSFT"], datetime.now(timezone.utc))
        sql, params = cur.executed[0]
        self.assertIn("metric <> ALL(%s) OR source = 'yfinance'", sql)
        self.assertEqual(set(params[-1]), set(ss.FLOW_METRICS))
        self.assertTrue({"total_revenue", "net_income", "fcf", "eps"} <= set(ss.FLOW_METRICS))
        self.assertNotIn("total_debt", ss.FLOW_METRICS)   # balance sheet: instants are fine

    def test_annual_series_keeps_one_entry_per_year_newest_first(self):
        d = lambda y, m: datetime(y, m, 28, tzinfo=timezone.utc)  # noqa: E731
        cur = FakeCursor(rows=[("MSFT", d(2026, 6), 3.0), ("MSFT", d(2025, 6), 2.0),
                               ("MSFT", d(2024, 6), 1.0)])
        out = ss.annual_series(cur, ["MSFT"], "total_revenue", d(2026, 10), 3)
        self.assertEqual([v for _ts, v in out["MSFT"]], [3.0, 2.0, 1.0])


class Dg20CashCapture(unittest.TestCase):
    """DG-20 (2026-11-12): cash is captured on both legs as a balance-sheet instant."""

    def test_cash_mapped_on_both_legs_as_instant(self):
        self.assertIn("cash_and_equivalents", sf.EDGAR_MAP)
        self.assertIn("cash_and_equivalents", sf.YF_MAP)
        self.assertNotIn("cash_and_equivalents", sf.FLOW)
        self.assertNotIn("cash_and_equivalents", sf.EXCLUDE_LATEST)

    def test_existing_tags_untouched(self):
        # append-only map: adding cash must not reorder the debt tags
        self.assertEqual(sf.EDGAR_MAP["total_debt"], ["LongTermDebtNoncurrent", "LongTermDebt"])


class Ev9Extension(unittest.TestCase):
    """EV-9 full extension (2026-10-26): new statement lines flow end to end."""

    NEW = {"total_assets", "gross_profit", "current_assets", "current_liabilities",
           "shares_outstanding", "dividends_paid"}

    def test_new_metrics_mapped_on_both_legs(self):
        self.assertTrue(self.NEW <= set(sf.EDGAR_MAP))
        self.assertTrue(self.NEW <= set(sf.YF_MAP))
        self.assertIn("ocf", sf.YF_MAP)   # accruals input for NSE names (EV-12)

    def test_flow_vs_instant_classification(self):
        self.assertTrue({"gross_profit", "dividends_paid"} <= sf.FLOW)
        for instant in ("total_assets", "current_assets", "current_liabilities",
                        "shares_outstanding"):
            self.assertNotIn(instant, sf.FLOW)

    def test_dividends_paid_stored_positive_from_either_sign(self):
        self.assertEqual(sf._norm("dividends_paid", -1250.0), 1250.0)
        self.assertEqual(sf._norm("dividends_paid", 1250.0), 1250.0)
        self.assertEqual(sf._norm("net_income", -7.0), -7.0)  # losses keep their sign

    def test_edgar_rows_accept_shares_unit_and_instant_assets(self):
        facts = {
            "Assets": {"units": {"USD": [
                {"form": "10-K", "end": "2025-12-31", "filed": "2026-02-10", "val": 5e9}]}},
            "CommonStockSharesOutstanding": {"units": {"shares": [
                {"form": "10-Q", "end": "2025-09-30", "filed": "2025-11-01", "val": 1.1e8}]}},
            "PaymentsOfDividends": {"units": {"USD": [
                {"form": "10-K", "start": "2025-01-01", "end": "2025-12-31",
                 "filed": "2026-02-10", "val": 4.2e8}]}},
        }
        rows = sf.edgar_fact_rows(facts)
        by_metric = {r[0]: r for r in rows}
        self.assertEqual(by_metric["total_assets"][3], 0)            # instant: duration 0
        self.assertEqual(by_metric["shares_outstanding"][4], 1.1e8)  # shares unit accepted
        self.assertEqual(by_metric["dividends_paid"][3], 364)        # dated flow
        self.assertEqual(by_metric["dividends_paid"][4], 4.2e8)

    def test_ocf_stays_out_of_the_latest_view(self):
        self.assertEqual(sf.EXCLUDE_LATEST, {"ocf", "capex"})

    def test_gross_profit_derived_from_revenue_minus_cogs(self):
        facts = {
            "Revenues": {"units": {"USD": [
                {"form": "10-K", "start": "2025-01-01", "end": "2025-12-31",
                 "filed": "2026-02-10", "val": 1000.0}]}},
            "CostOfRevenue": {"units": {"USD": [
                {"form": "10-K", "start": "2025-01-01", "end": "2025-12-31",
                 "filed": "2026-02-10", "val": 600.0}]}},
        }
        rows = sf.edgar_fact_rows(facts)
        by_metric = {r[0]: r for r in rows}
        self.assertIn("gross_profit", by_metric)
        self.assertEqual(by_metric["gross_profit"][4], 400.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
