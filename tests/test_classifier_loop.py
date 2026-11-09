"""Hermetic unit tests for ops-tools/classifier_loop.py.

    python3 tests/test_classifier_loop.py

Covers:
1. Cross-sectional Spearman Rank IC, Q5-Q1 spread, and sector distortion alerts.
2. Stock archetype cohort aggregation and thesis soundness checks
   (Quality Compounder > Lagging / Weak, Deep Value > Value Trap,
   Defensive Quality tail protection > High-Risk / Stressed).
3. Multibagger Gate Marginal Value (GMV) attribution and over-restrictive gate
   detection when single-gate near-misses outperform passing picks.
4. Shrinkage-regularized adaptive factor weights under ADR 0018 caps (+/-5% max
   tilt, min sample gate, weights summing to 1.0000).
5. Deterministic Markdown calibration report rendering.
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ops-tools"))
import classifier_loop as cl  # noqa: E402


def _fy(year):
    return datetime(year, 3, 31, tzinfo=timezone.utc)


class ClassifierLoopTest(unittest.TestCase):
    def test_spearman_ic_and_quintile_spread(self):
        x = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        y = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10]
        self.assertAlmostEqual(cl.spearman_ic(x, y), 1.0, places=5)
        self.assertAlmostEqual(cl.spearman_ic(x, list(reversed(y))), -1.0, places=5)
        # Q5 is (0.09 + 0.10)/2 = 0.095, Q1 is (0.01 + 0.02)/2 = 0.015 -> spread = 0.08
        self.assertAlmostEqual(cl.quintile_spread(x, y), 0.08, places=5)

    def test_evaluate_factors_detects_healthy_and_decaying_signals(self):
        records = []
        # 8 cross-sections x 10 symbols: quality positively predicts excess return;
        # momentum negatively predicts excess return (decaying); value inverted in Financials.
        for t_idx in range(8):
            ts = f"2026-0{t_idx + 1}-01"
            for i in range(10):
                q_score = (i + 1) / 10.0
                m_score = 1.0 - q_score
                sec = "Financials" if i < 8 else "Technology"
                v_score = 1.0 - q_score if sec == "Financials" else q_score
                exc = (q_score - 0.5) * 0.20
                records.append({
                    "ts": ts,
                    "symbol": f"S{i}",
                    "universe": "SP500",
                    "sector": sec,
                    "value": v_score,
                    "quality": q_score,
                    "growth": 0.5 + 0.01 * i,
                    "momentum": m_score,
                    "risk": 0.5 + 0.01 * i,
                    "composite": q_score * 100,
                    "realized_ret": exc + 0.02,
                    "bench_ret": 0.02,
                })
        res = cl.evaluate_factors(records)
        self.assertEqual(res["overall"]["quality"]["status"], "healthy")
        self.assertGreater(res["overall"]["quality"]["mean_ic"], 0.9)
        self.assertEqual(res["overall"]["momentum"]["status"], "decaying")
        self.assertLess(res["overall"]["momentum"]["mean_ic"], -0.9)
        self.assertTrue(any("Factor 'momentum' has negative mean Rank IC" in w for w in res["warnings"]))
        self.assertTrue(any("Sector distortion: 'value' in SP500/Financials" in w for w in res["warnings"]))

    def test_evaluate_archetypes_validates_soundness_checks(self):
        records = []
        # 6 Quality Compounders (+12% excess), 6 Lagging / Weak (-8% excess),
        # 6 Deep Value (+8% excess), 6 Value Trap (-10% excess),
        # 6 Defensive Quality (p10 = +1%), 6 High-Risk / Stressed (p10 = -20%)
        profiles = [
            ((0.60, 0.85, 0.75, 0.60, 0.65), 0.14, 0.02),   # Quality Compounder
            ((0.25, 0.30, 0.25, 0.25, 0.35), -0.06, 0.02),  # Lagging / Weak
            ((0.85, 0.60, 0.40, 0.40, 0.50), 0.10, 0.02),   # Deep Value
            ((0.80, 0.25, 0.30, 0.20, 0.45), -0.08, 0.02),  # Value Trap
            ((0.50, 0.75, 0.45, 0.50, 0.80), 0.04, 0.02),   # Defensive Quality
            ((0.40, 0.25, 0.30, 0.40, 0.20), -0.18, 0.02),  # High-Risk / Stressed
        ]
        for idx, (factors, r_ret, b_ret) in enumerate(profiles):
            for k in range(6):
                records.append({
                    "ts": f"2026-01-0{k + 1}",
                    "symbol": f"P{idx}_{k}",
                    "universe": "SP500",
                    "sector": "Industrials",
                    "value": factors[0],
                    "quality": factors[1],
                    "growth": factors[2],
                    "momentum": factors[3],
                    "risk": factors[4],
                    "realized_ret": r_ret,
                    "bench_ret": b_ret,
                })
        res = cl.evaluate_archetypes(records)
        self.assertEqual(len(res["soundness_checks"]), 3)
        self.assertTrue(all(chk["passed"] for chk in res["soundness_checks"]))
        self.assertEqual(res["warnings"], [])

    def test_multibagger_gate_attribution_flags_over_restrictive_gate(self):
        def make_annual(rev_end=180.0, eps_end=2.0):
            return {
                "total_revenue": [(_fy(2026), rev_end), (_fy(2023), 100.0)],
                "eps": [(_fy(2026), eps_end), (_fy(2023), 1.0)],
                "net_income": [(_fy(2026), 20.0), (_fy(2023), 10.0)],
                "total_debt": [(_fy(2026), 10.0)],
                "shareholder_equity": [(_fy(2026), 50.0)],
                "fcf": [(_fy(2026), 8.0)],
            }

        candidates = []
        # 6 candidates passing all gates -> +8% excess return
        for i in range(6):
            candidates.append({
                "symbol": f"PASS{i}",
                "sector": "Industrials",
                "annual": make_annual(),
                "snap": {"pe": 25.0, "roe": 0.22, "market_cap": 1e9},
                "metrics": {"sma200_ratio": 1.1},
                "size_pct": 0.30,
                "realized_ret": 0.10,
                "bench_ret": 0.02,
            })
        # 6 near-misses failing ONLY valuation (PEG > 2.0, e.g. pe=60) -> +18% excess return
        for i in range(6):
            candidates.append({
                "symbol": f"VAL_MISS{i}",
                "sector": "Industrials",
                "annual": make_annual(),
                "snap": {"pe": 60.0, "roe": 0.22, "market_cap": 1e9},
                "metrics": {"sma200_ratio": 1.1},
                "size_pct": 0.30,
                "realized_ret": 0.20,
                "bench_ret": 0.02,
            })
        res = cl.evaluate_multibagger_gates(candidates)
        self.assertEqual(res["passed_count"], 6)
        self.assertAlmostEqual(res["passed_mean_excess"], 0.08, places=4)
        val_gate = res["gates"]["valuation"]
        self.assertEqual(val_gate["near_miss_count"], 6)
        self.assertAlmostEqual(val_gate["gmv"], -0.10, places=4)
        self.assertTrue(any("Gate 'valuation' has negative marginal value" in r
                            for r in res["recommendations"]))

    def test_propose_factor_weights_respects_adr0018_caps_and_min_samples(self):
        # Under min sample threshold -> returns exact prior weights
        thin = {"overall": {f: {"n_periods": 3, "ic_ir": 2.0, "mean_ic": 0.10} for f in cl.FACTORS}}
        cal_thin = cl.propose_factor_weights(thin)
        self.assertEqual(cal_thin["weights"], cl.PRIOR_WEIGHTS)
        self.assertEqual(cal_thin["shrinkage_lambda"], 0.0)

        # Strong quality/growth IC-IR, negative momentum IC-IR over 12 cross-sections
        rich = {
            "overall": {
                "value": {"n_periods": 12, "ic_ir": 0.5, "mean_ic": 0.03},
                "quality": {"n_periods": 12, "ic_ir": 3.0, "mean_ic": 0.12},
                "growth": {"n_periods": 12, "ic_ir": 2.5, "mean_ic": 0.10},
                "momentum": {"n_periods": 12, "ic_ir": -1.0, "mean_ic": -0.05},
                "risk": {"n_periods": 12, "ic_ir": 0.8, "mean_ic": 0.04},
            }
        }
        cal = cl.propose_factor_weights(rich)
        self.assertAlmostEqual(sum(cal["weights"].values()), 1.0, places=4)
        for f in cl.FACTORS:
            self.assertLessEqual(abs(cal["tilts"][f]), cl.MAX_TILT + 1e-4, f)
            self.assertGreaterEqual(cal["weights"][f], cl.MIN_WEIGHT, f)
        self.assertGreater(cal["weights"]["quality"], cl.PRIOR_WEIGHTS["quality"])
        self.assertLess(cal["weights"]["momentum"], cl.PRIOR_WEIGHTS["momentum"])

    def test_render_calibration_report_contains_all_sections(self):
        f_eval = {"overall": {f: {"n_periods": 8, "mean_ic": 0.05, "ic_ir": 1.2,
                                  "mean_q5_q1_spread": 0.04, "status": "healthy"}
                              for f in (*cl.FACTORS, "composite")},
                  "warnings": []}
        a_eval = {"cohorts": {"Quality Compounder": {"count": 10, "mean_excess_ret": 0.08,
                                                     "median_excess_ret": 0.07, "hit_rate": 0.8,
                                                     "tail_p10_ret": -0.02}},
                  "soundness_checks": [{"check": "Quality Compounder > Lagging / Weak",
                                        "passed": True, "detail": "+8.00% vs -4.00%"}],
                  "warnings": []}
        mb_eval = {"passed_count": 5, "passed_mean_excess": 0.09,
                   "gates": {g: {"near_miss_count": 2, "total_failed_count": 10,
                                 "rejected_mean_excess": 0.01, "gmv": 0.08}
                             for g in ("growth", "return on equity", "debt", "size", "valuation", "trend")},
                   "recommendations": []}
        cal = cl.propose_factor_weights(f_eval)
        md = cl.render_calibration_report(f_eval, a_eval, mb_eval, cal, horizon=91, as_of="2026-11-08")
        self.assertIn("# Continuous Classification & Factor Validation (2026-11-08)", md)
        self.assertIn("## 1. Factor Efficacy & Adaptive Weights", md)
        self.assertIn("## 2. Stock Archetype Cohort Validation (Practitioner vs Academic)", md)
        self.assertIn("## 3. Multibagger Gate Attribution (Marginal Value)", md)
        self.assertIn("Quality Compounder", md)

        rows = cl.build_calibration_metric_rows(f_eval, a_eval, mb_eval, cal, horizon=91, as_of="2026-11-08")
        kinds = {r[2] for r in rows}
        self.assertIn("factor_ic", kinds)
        self.assertIn("archetype_ret", kinds)
        self.assertIn("mb_gmv", kinds)
        self.assertIn("shrunk_weight", kinds)


if __name__ == "__main__":
    unittest.main()
