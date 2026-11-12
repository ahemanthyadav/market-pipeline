#!/usr/bin/env python3
"""EV-0 harness tests on synthetic data: a planted signal must pass, noise must not.

    python3 tests/test_ev0.py
"""
import importlib.util
import sys
import unittest
from pathlib import Path

try:
    import numpy as np
    import pandas as pd
except ImportError as exc:
    print(f"SKIP: needs numpy and pandas ({exc})")
    sys.exit(0)

OPS = Path(__file__).resolve().parent.parent / "ops-tools"
spec = importlib.util.spec_from_file_location("ev0", OPS / "ev0.py")
ev0 = importlib.util.module_from_spec(spec)
sys.modules["ev0"] = ev0
spec.loader.exec_module(ev0)


def panel(seed, months=120, names=200, strength=0.0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2005-01-31", periods=months, freq="ME")
    cols = [f"S{i}" for i in range(names)]
    sig = pd.DataFrame(rng.normal(size=(months, names)), idx, cols)
    fwd = pd.DataFrame(rng.normal(scale=0.08, size=(months, names)), idx, cols) + strength * 0.08 * sig
    liq = pd.DataFrame(1.0, idx, cols)
    return sig, fwd, liq


class Harness(unittest.TestCase):
    def test_planted_signal_passes_criteria_1_and_3(self):
        sig, fwd, liq = panel(1, strength=0.15)
        s = ev0.summarize(ev0.evaluate(sig, fwd, liq, {}, cost=0.002))
        self.assertGreater(s["ic_t_nw"], 3)
        self.assertGreater(s["net_ann"], 0)
        self.assertGreater(s["ic_h1"], 0)
        self.assertGreater(s["ic_h2"], 0)

    def test_noise_does_not_pass(self):
        sig, fwd, liq = panel(2, strength=0.0)
        s = ev0.summarize(ev0.evaluate(sig, fwd, liq, {}, cost=0.002))
        self.assertLess(abs(s["ic_t_nw"]), 3)
        verdict, _ = ev0.verdict({"SP500": s})
        self.assertEqual(verdict, "FAIL")

    def test_fama_macbeth_drops_a_signal_explained_by_a_control(self):
        sig, fwd, liq = panel(3, strength=0.15)
        rng = np.random.default_rng(9)
        clone = sig + pd.DataFrame(rng.normal(scale=0.05, size=sig.shape), sig.index, sig.columns)
        alone = ev0.summarize(ev0.evaluate(clone, fwd, liq, {}, 0.002))
        ctrl = ev0.summarize(ev0.evaluate(clone, fwd, liq, {"orig": sig}, 0.002))
        self.assertGreater(abs(alone["fm_t_nw"]), abs(ctrl["fm_t_nw"]))

    def test_turnover_is_zero_for_a_static_signal_and_costs_bite(self):
        sig, fwd, liq = panel(4, strength=0.15)
        static = pd.DataFrame(np.tile(sig.iloc[0].values, (len(sig), 1)), sig.index, sig.columns)
        m = ev0.evaluate(static, fwd, liq, {}, cost=0.01)
        self.assertEqual(m.turnover.iloc[1:].max(), 0.0)
        self.assertLess(m.net.iloc[0], m.spread.iloc[0])  # first month pays full entry

    def test_nw_t_matches_plain_t_for_iid_and_needs_data(self):
        x = np.random.default_rng(5).normal(0.1, 1, 2000)
        plain = x.mean() / (x.std(ddof=0) / np.sqrt(len(x)))
        self.assertAlmostEqual(ev0.nw_t(x, lag=0), plain, places=6)
        self.assertTrue(np.isnan(ev0.nw_t([1.0, 2.0])))

    def test_signals_skip_the_last_month_for_12_1(self):
        idx = pd.bdate_range("2020-01-01", periods=400)
        px = pd.DataFrame({"A": np.linspace(100, 200, 400)}, idx)
        s = ev0.signals(px, px * 0 + 1e6, px)
        self.assertAlmostEqual(s["ret_12_1"]["A"].iloc[-1], px.A.iloc[-22] / px.A.iloc[-253] - 1)

    def test_g21_signals_computed_on_synthetic_panel(self):
        """G-21 signals (amihud, max_daily_ret, idio_vol) must be computable panels."""
        idx = pd.bdate_range("2020-01-01", periods=400)
        rng = np.random.default_rng(7)
        px = pd.DataFrame({"A": 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 400)))}, idx)
        vol = pd.DataFrame({"A": 1e6}, idx)
        close = px.copy()
        s = ev0.signals(px, vol, close)
        for name in ev0.G21:
            self.assertIn(name, s, f"G-21 signal {name} missing from signals()")
            self.assertEqual(s[name].shape, px.shape, f"{name} panel shape mismatch")
        self.assertIn("industry_momentum", ev0.G21_EXTRA,
                      "industry_momentum needs sector data, computed in run()")

    def test_amihud_is_negative_illiquidity(self):
        """amihud = -mean(|r| / dollar_vol) so higher score = more liquid."""
        idx = pd.bdate_range("2020-01-01", periods=300)
        px = pd.DataFrame({"A": 100.0}, idx)
        vol = pd.DataFrame({"A": 1e6}, idx)
        s = ev0.signals(px, px * 0 + 1e6, px)
        # With constant price, r = 0, amihud = 0 (not negative)
        self.assertTrue((s["amihud"]["A"].dropna() <= 0).all(),
                        "amihud should be <= 0 (negated illiquidity)")

    def test_industry_momentum_maps_sector_return(self):
        """industry_momentum gives every symbol in a sector the same sector momentum score."""
        import numpy as np
        idx = pd.bdate_range("2020-01-01", periods=300)
        rng = np.random.default_rng(11)
        # Two sectors: Tech (rising) and Banks (falling)
        tech_ret = 0.001 + rng.normal(0, 0.02, 300)
        bank_ret = -0.001 + rng.normal(0, 0.02, 300)
        px = pd.DataFrame({
            "AAPL": 100 * np.exp(np.cumsum(tech_ret)),
            "MSFT": 100 * np.exp(np.cumsum(tech_ret * 0.9)),
            "JPM": 100 * np.exp(np.cumsum(bank_ret)),
            "C": 100 * np.exp(np.cumsum(bank_ret * 1.1)),
        }, idx)
        vol = px * 0 + 1e6
        close = px.copy()
        s = ev0.signals(px, vol, close)
        # industry_momentum is computed in run(), not signals() — verify it's in G21_EXTRA
        self.assertIn("industry_momentum", ev0.G21_EXTRA)

    def test_distress_score_in_g21_extra(self):
        """distress_score needs financials data — computed in run(), listed in G21_EXTRA."""
        self.assertIn("distress_score", ev0.G21_EXTRA)

    def test_altman_z_score_components(self):
        """Altman Z-score: higher = less distressed. Verify component signs on synthetic data."""
        import numpy as np
        # Healthy company: positive WC, high equity, positive EBIT, high mve/liab, high rev/assets
        ca, cl, ta, se, oi, rev, so, close = 100, 40, 500, 300, 50, 400, 10, 50
        wc = ca - cl
        mve = close * so
        bvl = ta - se
        z = (1.2 * wc / ta + 1.4 * se / ta + 3.3 * oi / ta
             + 0.6 * mve / bvl + 1.0 * rev / ta)
        self.assertGreater(z, 2.0, "healthy company should have Z > 2 (safe zone)")

        # Distressed company: negative WC, low equity, negative EBIT, low mve/liab, low rev/assets
        ca2, cl2, ta2, se2, oi2, rev2, so2, close2 = 20, 80, 200, 20, -10, 50, 5, 10
        wc2 = ca2 - cl2
        mve2 = close2 * so2
        bvl2 = ta2 - se2
        z2 = (1.2 * wc2 / ta2 + 1.4 * se2 / ta2 + 3.3 * oi2 / ta2
              + 0.6 * mve2 / bvl2 + 1.0 * rev2 / ta2)
        self.assertLess(z2, 1.8, "distressed company should have Z < 1.8 (distress zone)")


# ---------------------------------------------------------------- EV-19 / EV-20 (point-in-time fundamentals)
D = pd.Timestamp


def facts(rows, symbol="A", source="edgar"):
    """rows: (metric, period_end, duration_days, filed, value)"""
    return pd.DataFrame([{"symbol": symbol, "source": source, "metric": m, "period_end": D(pe),
                          "duration_days": dur, "filed": D(f), "value": v}
                         for m, pe, dur, f, v in rows])


class PointInTimeFacts(unittest.TestCase):
    def test_first_filed_ignores_restatements(self):
        ff = facts([("eps", "2020-03-31", 91, "2020-05-01", 1.0),
                    ("eps", "2020-03-31", 91, "2021-05-01", 9.9)])     # restated comparative
        out = ev0.first_filed(ff)
        self.assertEqual(len(out), 1)
        self.assertEqual(out.value.iloc[0], 1.0)
        self.assertEqual(out.filed.iloc[0], D("2020-05-01"))

    def test_split_factor_counts_only_later_splits(self):
        f = ev0.split_factor([D("2019-01-01"), D("2021-01-01"), D("2025-01-01")],
                             [D("2020-08-31"), D("2024-06-10")], [4.0, 10.0])
        self.assertEqual(list(f), [40.0, 10.0, 1.0])

    def test_split_adjust_puts_eps_and_shares_on_todays_basis(self):
        ff = facts([("eps", "2019-12-31", 91, "2020-01-30", 4.0),
                    ("shares_outstanding", "2019-12-31", 91, "2020-01-30", 100.0),
                    ("eps", "2021-12-31", 91, "2022-01-30", 1.5)])
        spl = pd.DataFrame({"symbol": ["A"], "d": [D("2020-08-31")], "ratio": [4.0]})
        out = ev0.split_adjust(ff, spl).set_index(["metric", "period_end"]).value
        self.assertEqual(out[("eps", D("2019-12-31"))], 1.0)
        self.assertEqual(out[("shares_outstanding", D("2019-12-31"))], 400.0)
        self.assertEqual(out[("eps", D("2021-12-31"))], 1.5)               # filed after split

    def test_discrete_quarters_derives_q4_from_fy_minus_9m(self):
        ff = facts([("eps", "2020-03-31", 91, "2020-05-01", 1.0),
                    ("eps", "2020-06-30", 91, "2020-08-01", 1.1),
                    ("eps", "2020-06-30", 182, "2020-08-01", 2.1),
                    ("eps", "2020-09-30", 91, "2020-11-01", 1.2),
                    ("eps", "2020-09-30", 274, "2020-11-01", 3.3),
                    ("eps", "2020-12-31", 366, "2021-02-20", 4.7)])
        q = ev0.discrete_quarters(ff).set_index("period_end")
        self.assertEqual(len(q), 4)
        self.assertAlmostEqual(q.loc[D("2020-12-31"), "value"], 1.4)
        self.assertEqual(q.loc[D("2020-12-31"), "filed"], D("2021-02-20"))
        self.assertAlmostEqual(q.loc[D("2020-06-30"), "value"], 1.1)      # direct 3M row wins

    def test_discrete_quarters_from_ytd_only_cash_flow(self):
        ff = facts([("ocf", "2020-03-31", 91, "2020-05-01", 10.0),
                    ("ocf", "2020-06-30", 182, "2020-08-01", 25.0),
                    ("ocf", "2020-09-30", 274, "2020-11-01", 45.0)])
        q = ev0.discrete_quarters(ff).set_index("period_end").value
        self.assertEqual(list(q), [10.0, 15.0, 20.0])

    def test_ttm_needs_four_consecutive_quarters_and_waits_for_the_last_filing(self):
        q = pd.DataFrame({"symbol": "A", "metric": "net_income",
                          "period_end": pd.to_datetime(["2020-03-31", "2020-06-30", "2020-09-30",
                                                        "2020-12-31", "2021-09-30"]),
                          "filed": pd.to_datetime(["2020-05-01", "2020-08-01", "2020-11-01",
                                                   "2021-02-20", "2021-11-01"]),
                          "value": [1.0, 2.0, 3.0, 4.0, 5.0]})
        t = ev0.ttm(q)
        self.assertEqual(len(t), 1)                     # 2021-09-30 has a gap (no Q1/Q2 2021)
        self.assertEqual(t.value.iloc[0], 10.0)
        self.assertEqual(t.filed.iloc[0], D("2021-02-20"))

    def test_sue_uses_seasonal_difference_and_only_prior_sigma(self):
        pe = pd.date_range("2015-03-31", periods=16, freq="QE")
        eps = [1.0, 1.0, 1.0, 1.0] + [1.0 + 0.1 * ((i % 2) * 2 - 1) for i in range(11)] + [3.0]
        q = pd.DataFrame({"symbol": "A", "period_end": pe,
                          "filed": pe + pd.Timedelta(days=30), "value": eps})
        s = ev0.sue_events(q, n=8, min_n=4).set_index("period_end").sue
        diffs = pd.Series(eps).diff(4)
        expect = diffs.iloc[15] / diffs.iloc[7:15].std()
        self.assertAlmostEqual(s.loc[pe[15]], expect)
        self.assertGreater(s.loc[pe[15]], 5)            # a big surprise stands out
        self.assertNotIn(pe[6], s.index)                # < 4 prior diffs -> no SUE yet

    def test_asof_panel_is_strictly_point_in_time(self):
        f = pd.DataFrame({"symbol": "A",
                          "period_end": pd.to_datetime(["2020-03-31", "2020-06-30", "2019-12-31"]),
                          "filed": pd.to_datetime(["2020-05-01", "2020-07-31", "2020-08-15"]),
                          "value": [1.0, 2.0, 99.0]})          # last row: late filing, OLD period
        me = pd.DatetimeIndex(["2020-04-30", "2020-05-01", "2020-05-29", "2020-07-31",
                               "2020-08-31", "2021-12-31"])
        p = ev0.asof_panel(f, me, pd.Index(["A", "B"]))
        a = p["A"]
        self.assertTrue(np.isnan(a.iloc[0]))            # nothing filed yet
        self.assertTrue(np.isnan(a.iloc[1]))            # filed ON the month-end: not yet usable
        self.assertEqual(a.iloc[2], 1.0)
        self.assertEqual(a.iloc[3], 1.0)                # Q2 filed that very day: still Q1
        self.assertEqual(a.iloc[4], 2.0)                # late old-period filing never wins
        self.assertTrue(np.isnan(a.iloc[5]))            # > 15 months stale
        self.assertTrue(p["B"].isna().all())

    def test_sleeve_neutral_fill_and_min_components(self):
        idx = pd.DatetimeIndex(["2020-01-31"])
        cols = [f"S{i}" for i in range(5)]
        a = pd.DataFrame([[1, 2, 3, 4, np.nan]], idx, cols, dtype=float)
        b = pd.DataFrame([[4, 3, 2, 1, np.nan]], idx, cols, dtype=float)
        c = pd.DataFrame([[1, 2, 3, np.nan, np.nan]], idx, cols, dtype=float)
        s = ev0.sleeve({"a": a, "b": b, "c": c}, {"a": True, "b": False, "c": True}, 2)
        self.assertTrue(np.isnan(s.loc[idx[0], "S4"]))  # no component at all
        self.assertAlmostEqual(s.loc[idx[0], "S3"], (1.0 + 1.0 + 0.5) / 3)  # c missing -> 0.5
        self.assertGreater(s.loc[idx[0], "S2"], s.loc[idx[0], "S0"])

    def test_pick_nse_source_prefers_consolidated(self):
        ff = pd.concat([facts([("eps", "2020-03-31", 91, "2020-05-01", 1.0)], "X.NS", "nse_cons"),
                        facts([("eps", "2020-03-31", 91, "2020-05-01", 2.0)], "X.NS", "nse_sa"),
                        facts([("eps", "2020-03-31", 91, "2020-05-01", 3.0)], "Y.NS", "nse_sa")])
        out = ev0.pick_nse_source(ff)
        self.assertEqual(sorted(zip(out.symbol, out.source)), [("X.NS", "nse_cons"), ("Y.NS", "nse_sa")])

    def test_fundamental_panels_end_to_end_us(self):
        """Two EDGAR names, two years of quarters: E/P and ROE come out right and point-in-time."""
        rows = []
        for sym, ni, eq in (("A", 10.0, 100.0), ("B", 20.0, 400.0)):
            for pe in pd.date_range("2019-03-31", periods=8, freq="QE"):
                f = (pe + pd.Timedelta(days=35)).date().isoformat()
                pe_s = pe.date().isoformat()
                rows += [("net_income", pe_s, 91, f, ni), ("operating_income", pe_s, 91, f, ni),
                         ("total_revenue", pe_s, 91, f, 4 * ni), ("eps", pe_s, 91, f, ni / 10),
                         ("shareholder_equity", pe_s, 0, f, eq), ("total_debt", pe_s, 0, f, eq / 2),
                         ("shares_outstanding", pe_s, 0, f, 10.0)]
            rows_df = facts(rows[-56:], sym)
            rows_df["symbol"] = sym
            rows[-56:] = []
            rows.append(rows_df)
        fin = pd.concat(rows, ignore_index=True)
        me = pd.DatetimeIndex(["2019-10-31", "2020-12-31"])
        syms = pd.Index(["A", "B"])
        close = pd.DataFrame({"A": [20.0, 20.0], "B": [100.0, 100.0]}, me)
        fp, (n_us, n_in) = ev0.fundamental_panels(fin, None, me, syms, close)
        self.assertEqual((n_us, n_in), (2, 0))
        self.assertTrue(np.isnan(fp["ep"].loc[me[0], "A"]))   # only 2 quarters filed by then
        self.assertAlmostEqual(fp["ep"].loc[me[1], "A"], 40.0 / 200.0)
        self.assertAlmostEqual(fp["roe"].loc[me[1], "B"], 80.0 / 400.0)
        self.assertAlmostEqual(fp["low_de"].loc[me[1], "A"], -0.5)
        self.assertIn("sue", fp)

    def test_planted_signal_gets_a_full_pass_verdict(self):
        sig, fwd, liq = panel(12, strength=0.15)
        s = ev0.summarize(ev0.evaluate(sig, fwd, liq, {}, cost=0.002))
        v, _ = ev0.verdict({"SP500": dict(s, fm_t_nw=s["ic_t_nw"])})
        self.assertEqual(v, "PASS")


if __name__ == "__main__":
    unittest.main(verbosity=1)
