"""Unit tests for the intrinsic-valuation engine (ADR 0026) — no network, no DB.

    python3 tests/test_valuation.py

mdjob is stubbed BEFORE import; value_one() and the aggregators are exercised
directly with crafted inputs. Proves each famous method's math and gating:
Graham, sector peer multiple (P/B swap for financials), FCF perpetuity,
two-stage DCF, Gordon DDM, Greenwald EPV (debt netted, no cash credit),
residual income (ROE gate + multiple cap), Lynch PEG — plus the median
composite, growth clamps, and the sector_metrics aggregation.
"""
import importlib.util
import sys
import types
import unittest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "manifests/stock-screener/scripts/valuation.py"

MDJOB = types.ModuleType("mdjob")
MDJOB.connect = lambda: None
MDJOB.job_run = lambda name: None
sys.modules["mdjob"] = MDJOB

spec = importlib.util.spec_from_file_location("valuation", SCRIPT)
va = importlib.util.module_from_spec(spec)
spec.loader.exec_module(va)

RATES = {"US": 0.095, "IN": 0.12}       # 10y + ERP, decimal, per MARKET (ADR 0029)
STYLES = {"Financials": "financial", "Information Technology": "tech_growth"}
WEIGHTS = {("financial", "pe"): 2, ("financial", "pb"): 3, ("financial", "fcf"): 0,
           ("tech_growth", "pe"): 2, ("tech_growth", "pb"): 2,
           ("tech_growth", "fcf"): 3,
           ("default", "pe"): 2, ("default", "pb"): 2, ("default", "fcf"): 2}
WEIGHTS[("financial", "pe")] = 1                  # P/E misleading -> P/B peer path


def yrs(metric_values, start=2020):
    return [(date(start + i, 12, 31), v) for i, v in enumerate(metric_values)]


def tech(close=100.0):
    """A profitable tech name: every method should have usable inputs."""
    d = {"universe": "SP500", "sector": "Information Technology", "close": close,
         "market_cap": 1000.0 * close,            # 1,000 implied shares
         "eps": 5.0, "shareholder_equity": 40000.0, "roe": 0.20,
         "total_debt": 10000.0, "pe": 20.0, "pb": 2.5}
    hist = {"total_revenue": yrs([100e3, 110e3, 121e3, 133.1e3, 146.41e3]),  # 10%
            "eps": yrs([3.0, 3.5, 4.0, 4.5, 5.0]),
            "dividends_paid": yrs([900.0, 950.0, 1000.0, 1050.0, 1100.0]),
            "ocf": yrs([9000.0, 10000.0, 11000.0, 12000.0, 13000.0]),
            "capex": yrs([2000.0, 2500.0, 3000.0, 3000.0, 3000.0]),
            "operating_income": yrs([8000.0, 9000.0, 10000.0, 11000.0, 12000.0])}
    return d, hist


MEDIANS = {("SP500", "Information Technology"): {"pe": 24.0, "pb": 5.0},
           ("SP500", "Financials"): {"pe": 11.0, "pb": 1.4}}


def run_one(d, hist):
    return va.value_one(d, hist, MEDIANS, STYLES, WEIGHTS, RATES)


class Methods(unittest.TestCase):
    def test_graham(self):
        out = run_one(*tech())
        self.assertAlmostEqual(out["graham"], (22.5 * 5.0 * 40.0) ** 0.5, places=6)

    def test_peer_multiple_uses_sector_pe_for_tech(self):
        out = run_one(*tech())
        self.assertAlmostEqual(out["peer_multiple"], 24.0 * 5.0, places=6)

    def test_financials_swap_to_pb_times_bvps_and_skip_fcf(self):
        d, hist = tech()
        d["sector"] = "Financials"
        out = run_one(d, hist)
        self.assertAlmostEqual(out["peer_multiple"], 1.4 * 40.0, places=6)  # P/B x BVPS
        self.assertIsNone(out["fcf_yield"])   # weight 0: bank FCF is meaningless
        self.assertIsNone(out["dcf"])         # DCF rides the same FCF gate

    def test_fcf_perpetuity(self):
        out = run_one(*tech())
        self.assertAlmostEqual(out["fcf_yield"], 10000.0 / 1000.0 / 0.095, places=6)

    def test_dcf_two_stage_matches_hand_rolled_sum(self):
        out = run_one(*tech())
        r, g, f, pv = 0.095, 0.10, 10000.0, 0.0
        for t in range(1, 6):
            f *= 1 + g
            pv += f / (1 + r) ** t
        pv += f * 1.025 / (r - 0.025) / (1 + r) ** 5
        self.assertAlmostEqual(out["dcf"], pv / 1000.0, places=6)
        self.assertAlmostEqual(out["growth_used"], 0.10, places=3)

    def test_growth_clamped_at_15_percent(self):
        d, hist = tech()
        hist["total_revenue"] = yrs([100.0, 150.0, 225.0, 337.5, 506.25])  # 50%/yr
        self.assertAlmostEqual(run_one(d, hist)["growth_used"], 0.15, places=6)

    def test_ddm_gordon_growth(self):
        out = run_one(*tech())
        gd = (1100.0 / 900.0) ** (1 / 4.0) - 1          # ~5.15%, under the 8% clamp
        dps = 1100.0 / 1000.0
        self.assertAlmostEqual(out["ddm"], dps * (1 + gd) / (0.095 - gd), places=4)

    def test_epv_nets_debt_and_floors_at_zero(self):
        out = run_one(*tech())
        expect = (10000.0 * 0.75 / 0.095 - 10000.0) / 1000.0
        self.assertAlmostEqual(out["epv"], expect, places=6)
        d, hist = tech()
        d["total_debt"] = 1e9                            # debt swamps the EPV
        self.assertIsNone(run_one(d, hist)["epv"])

    def test_residual_income_gate_and_cap(self):
        out = run_one(*tech())
        jpb = (0.20 - 0.025) / (0.095 - 0.025)
        self.assertAlmostEqual(out["residual_income"], jpb * 40.0, places=6)
        d, hist = tech()
        d["roe"] = 0.05                                  # below r: no value created
        self.assertIsNone(run_one(d, hist)["residual_income"])
        d["roe"] = 3.0                                   # absurd ROE: multiple capped
        self.assertAlmostEqual(run_one(d, hist)["residual_income"],
                               va.JPB_CAP * 40.0, places=6)

    def test_peg_lynch(self):
        out = run_one(*tech())
        ge = (5.0 / 3.0) ** (1 / 4.0) - 1                # ~13.6% EPS CAGR
        self.assertAlmostEqual(out["peg_lynch"], 5.0 * ge * 100, places=4)

    def test_fair_value_is_median_and_upside_vs_close(self):
        out = run_one(*tech())
        vals = [out[c] for c in va.METHOD_COLS if out[c]]
        self.assertEqual(out["methods"], len(vals))
        import statistics
        self.assertAlmostEqual(out["fair_value"], statistics.median(vals), places=9)
        self.assertAlmostEqual(out["upside_pct"],
                               (out["fair_value"] / 100.0 - 1) * 100, places=9)

    def test_loss_maker_gets_no_earnings_methods_but_no_crash(self):
        d, hist = tech()
        d["eps"] = -2.0
        hist["eps"] = yrs([-1.0, -1.5, -2.0])
        out = run_one(d, hist)
        for m in ("graham", "peer_multiple", "peg_lynch"):
            self.assertIsNone(out[m])
        self.assertIsNotNone(out["fair_value"])  # cash/book methods still speak

    def test_no_close_returns_none(self):
        d, hist = tech()
        del d["close"]
        self.assertIsNone(run_one(d, hist))


class Sectors(unittest.TestCase):
    def test_sector_rows_aggregate_members_and_median_upside(self):
        data = {s: {"universe": "SP500", "sector": "S", "ret_6m": 0.10, "ret_12m": 0.2}
                for s in "ABCDE"}
        data["B"]["ret_6m"] = 0.30
        data["Z"] = {"universe": "SP500", "sector": "T"}
        today = datetime(2026, 10, 26)
        ups = {"A": 10.0, "B": 30.0, "C": 20.0, "D": 20.0, "E": 20.0}
        rows = va.sector_rows(data, {("SP500", "S"): {"pe": 15.0}}, ups, today)
        s = next(r for r in rows if r[1] == "S")
        self.assertEqual((s[3], s[4]), (5, 15.0))        # members, median_pe
        self.assertAlmostEqual(s[9], 0.14, places=9)     # avg ret_6m
        self.assertAlmostEqual(s[11], 20.0, places=9)    # median upside (n >= 5)
        t = next(r for r in rows if r[1] == "T")
        self.assertEqual(t[3], 1)
        self.assertIsNone(t[11])

    def test_median_needs_min_sample(self):
        # 2026-10-27: one thin SP400 name printed as a whole sector's view.
        data = {"A": {"universe": "SP400", "sector": "Materials"}}
        rows = va.sector_rows(data, {}, {"A": 209.0}, datetime(2026, 10, 27))
        self.assertIsNone(rows[0][11])
        thin = {"A": {"universe": "SP400", "sector": "Materials", "pe": 7.2}}
        self.assertEqual(va.sector_medians(thin), {("SP400", "Materials"): {}})


class NewMethodsADR0030(unittest.TestCase):
    def test_damodaran_fcff_hand_rolled(self):
        out = run_one(*tech())
        # Hand-roll: EBIT 12000, NOPAT 9000, capital 50000 -> ROC 0.18;
        # g = 10% revenue CAGR -> RR = 0.5556; rf = 0.05, kd = (0.07)(0.75);
        # E = 100000, D = 10000 -> WACC = (100000*0.095 + 10000*0.0525)/110000.
        nopat, roc, g = 12000.0 * 0.75, (12000.0 * 0.75) / 50000.0, 0.10
        rr = g / roc
        wacc = (100000.0 * 0.095 + 10000.0 * (0.05 + 0.02) * 0.75) / 110000.0
        pv, n = 0.0, nopat
        for t in range(1, 6):
            n *= 1 + g
            pv += n * (1 - rr) / (1 + wacc) ** t
        rr_t = 0.025 / roc
        pv += (n * 1.025 * (1 - rr_t) / (wacc - 0.025)) / (1 + wacc) ** 5
        self.assertAlmostEqual(out["damodaran_fcff"], (pv - 10000.0) / 1000.0,
                               places=6)

    def test_graham_growth_hand_rolled(self):
        out = run_one(*tech())
        # EPS CAGR 3->5 over 4y = 13.62%, clamp [0,15] keeps it; Y = 5% + 1%.
        g = (5.0 / 3.0) ** 0.25 - 1
        self.assertAlmostEqual(out["graham_growth"],
                               5.0 * (8.5 + 2 * g * 100) * 4.4 / 6.0, places=6)

    def test_damodaran_null_without_operating_income(self):
        d, hist = tech()
        hist.pop("operating_income")
        out = run_one(d, hist)
        self.assertIsNone(out["damodaran_fcff"])

    def test_methods_count_includes_new_columns(self):
        out = run_one(*tech())
        self.assertEqual(out["methods"], 10)


class ExtendedMethodsTest(unittest.TestCase):
    def test_ev_ebitda_peer_and_owner_earnings(self):
        d, hist = tech()
        d["ev_ebitda"] = 11.0
        medians = dict(MEDIANS)
        medians[("SP500", "Information Technology")] = {"pe": 24.0, "pb": 5.0, "ev_ebitda": 15.0}
        out = va.value_one(d, hist, medians, STYLES, WEIGHTS, RATES)
        # EV = 100000 + 10000 = 110000; implied EBITDA = 10000; target EV = 15 * 10000 = 150000;
        # eq = 140000 -> 140.0 per share
        self.assertAlmostEqual(out["ev_ebitda_peer"], 140.0, places=6)
        # Owner earnings: 5y FCFs are [7000, 7500, 8000, 9000, 10000] -> mean 8300;
        # (8300 * 1.025 / (0.095 - 0.025) - 10000) / 1000
        expect_oe = (8300.0 * 1.025 / (0.095 - 0.025) - 10000.0) / 1000.0
        self.assertAlmostEqual(out["owner_earnings"], expect_oe, places=6)

    def test_easton_mpeg_intangible_ri_and_total_payout_ddm(self):
        d, hist = tech()
        d["gross_profit"] = 32000.0
        hist["shares_outstanding"] = yrs([1050.0, 1030.0, 1000.0])  # ~2.91% buyback yield
        out = run_one(d, hist)
        ge = (5.0 / 3.0) ** 0.25 - 1
        dps0 = 1100.0 / 1000.0
        expect_mpeg = (5.0 * ge + 0.095 * dps0 * ge) / (0.095 ** 2)
        self.assertAlmostEqual(out["easton_mpeg"], expect_mpeg, places=6)
        self.assertIsNotNone(out["intangible_ri"])
        self.assertGreater(out["intangible_ri"], out["residual_income"])
        self.assertIsNotNone(out["total_payout_ddm"])
        self.assertGreater(out["total_payout_ddm"], out["ddm"])
        self.assertIsNotNone(out["practitioner_fair_value"])
        self.assertIsNotNone(out["academic_fair_value"])
        self.assertGreaterEqual(out["all_methods"], 13)

    def test_valuation_catalog_and_scientific_row(self):
        self.assertEqual(len(va.VALUATION_CATALOG), 34)
        keys = {row[0] for row in va.VALUATION_CATALOG}
        for col in va.PRACTITIONER_COLS + va.ACADEMIC_COLS:
            self.assertIn(col, keys)
        for row in va.VALUATION_CATALOG:
            self.assertIn(row[3], ("Practitioner", "Research-Backed"))
            self.assertTrue(row[4] and row[5] and row[6])
        d, hist = tech()
        d["scores"] = (73.5, 0.65, 0.85, 0.78, 0.65, 0.70)
        d["ret_12m"] = 0.25
        hist["total_assets"] = yrs([85000.0, 92000.0, 100000.0])
        hist["gross_profit"] = yrs([28000.0, 31000.0, 35000.0])
        hist["net_income"] = yrs([8000.0, 9500.0, 11000.0])
        hist["total_debt"] = yrs([12000.0, 11000.0, 10000.0])
        hist["current_assets"] = yrs([30000.0, 35000.0, 40000.0])
        hist["current_liabilities"] = yrs([15000.0, 15000.0, 15000.0])
        hist["shares_outstanding"] = yrs([1020.0, 1010.0, 1000.0])
        srow = va.scientific_row("TECH", datetime(2026, 11, 8), d, hist, size_pct=0.25)
        self.assertEqual(srow[0], "TECH")
        self.assertAlmostEqual(srow[4], 0.35, places=4)       # GP/A = 35000 / 100000
        self.assertAlmostEqual(srow[5], -0.02, places=4)      # Accruals = (11000 - 13000) / 100000
        self.assertEqual(srow[8], 9)                          # Piotroski F-score 9/9
        self.assertEqual(srow[11], "safe")                    # Altman Z'' zone
        self.assertEqual(srow[12], "Quality Compounder")      # Practitioner archetype
        self.assertEqual(srow[13], "QMJ + BAB Compounder (Asness 2019 / Frazzini 2014)")  # Academic archetype
        self.assertAlmostEqual(srow[14], 73.5, places=2)      # Practitioner composite
        self.assertAlmostEqual(srow[15], 72.6, places=2)      # Academic 1/N composite
        self.assertIsNotNone(srow[17])                        # Academic SQM multibagger score


if __name__ == "__main__":
    unittest.main()
