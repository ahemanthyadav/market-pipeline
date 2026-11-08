#!/usr/bin/env python3
"""Continuous Classification & Factor Validation Loop (classifier_loop.py).

Validates the logical and empirical soundness of all platform classifications
as new price bars, fundamentals filings, and resolved prediction outcomes arrive:
  1. Factor & Sector IC Audit: cross-sectional Spearman Rank IC, IC-IR, Q5-Q1
     excess return spread, and sector-level distortion flags for Value, Quality,
     Growth, Momentum, and Risk.
  2. Stock Archetype Cohort Validation: realized excess return, win rate, and
     10th-percentile tail drawdown per archetype (Quality Compounder, GARP,
     Deep Value, Momentum Leader, High-Growth Speculative, Defensive Quality,
     Turnaround / Cyclical, Balanced, Value Trap, High-Risk / Stressed,
     Lagging / Weak), plus automated thesis soundness checks.
  3. Multibagger Gate Marginal Value (GMV): compares candidates passing all
     6 multibagger gates against near-misses failing a single gate to verify
     each gate adds positive forward-return selection value.
  4. Shrinkage-Regularized Adaptive Calibration: blends prior factor weights
     W0 = (0.25, 0.25, 0.20, 0.20, 0.10) with realized IC-IR posterior weights,
     bounded by ADR 0018 guardrails (max +/-5% tilt cap, min sample gate).

Shares the workstation PSQL_CMD pattern with ev0.py / ml0.py / ml1.py / ml3.py,
and writes the rendered markdown calibration report to stock_reports
(symbol='__calibration__') and out/classifier_loop/report-<date>.md.

Usage:
  PSQL_CMD='kubectl -n market-data exec -i timescaledb-0 -- env PGPASSWORD=... psql -U postgres -d postgres' \\
    python3 ops-tools/classifier_loop.py run --horizon 91
  PSQL_CMD='...' python3 ops-tools/classifier_loop.py show
"""
import argparse
import datetime as dt
import json
import math
import os
import statistics
import subprocess
import sys
from pathlib import Path

# Reuse canonical classification functions from telegram/stocks.py
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "manifests" / "telegram"))
from stocks import (  # noqa: E402
    FINANCIAL_SECTORS,
    MB_MAX_DEBT_EQUITY,
    MB_MAX_PEG,
    MB_MIN_GROWTH,
    MB_MIN_ROE,
    MB_SIZE_CUT,
    academic_archetype,
    academic_composite,
    cagr,
    stock_archetype,
)

OUT = "out/classifier_loop"
FACTORS = ("value", "quality", "growth", "momentum", "risk")
PRIOR_WEIGHTS = {
    "value": 0.25,
    "quality": 0.25,
    "growth": 0.20,
    "momentum": 0.20,
    "risk": 0.10,
}
MAX_TILT = 0.05          # ADR 0018: max +/-5% shift from prior weight
MIN_WEIGHT = 0.05        # floor per factor
MIN_CROSS_SECTIONS = 6   # min resolved dates before proposing weight tilts
SHRINKAGE_N0 = 12        # Bayesian shrinkage half-saturation (cross-sections)
MIN_COHORT_N = 5         # min observations to judge an archetype or gate


# ---------------------------------------------------------------- pure statistics

def _rank(values):
    """Average-tie fractional ranks (1..N) for a sequence of floats."""
    n = len(values)
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j + 2) / 2.0
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def spearman_ic(x, y):
    """Cross-sectional Spearman rank correlation between x and y, or None."""
    pairs = [( float(a), float(b) ) for a, b in zip(x, y)
             if a is not None and b is not None
             and not math.isnan(float(a)) and not math.isnan(float(b))]
    if len(pairs) < 5:
        return None
    rx = _rank([p[0] for p in pairs])
    ry = _rank([p[1] for p in pairs])
    mx = sum(rx) / len(rx)
    my = sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den_x = sum((a - mx) ** 2 for a in rx)
    den_y = sum((b - my) ** 2 for b in ry)
    if den_x <= 0 or den_y <= 0:
        return 0.0
    return num / math.sqrt(den_x * den_y)


def quantile_cut(values, q):
    """Simple linear-interpolated quantile for q in [0, 1]."""
    clean = sorted(float(v) for v in values if v is not None and not math.isnan(float(v)))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    pos = q * (len(clean) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return clean[lo]
    return clean[lo] + (pos - lo) * (clean[hi] - clean[lo])


def quintile_spread(signal_vals, excess_rets):
    """Top quintile (Q5) minus bottom quintile (Q1) mean excess return."""
    pairs = [(float(s), float(r)) for s, r in zip(signal_vals, excess_rets)
             if s is not None and r is not None
             and not math.isnan(float(s)) and not math.isnan(float(r))]
    if len(pairs) < 5:
        return None
    pairs.sort(key=lambda p: p[0])
    k = max(1, len(pairs) // 5)
    q1 = sum(p[1] for p in pairs[:k]) / k
    q5 = sum(p[1] for p in pairs[-k:]) / k
    return q5 - q1


# ---------------------------------------------------------------- 1. Factor & Sector Audit

def evaluate_factors(records):
    """Evaluate factor Rank IC, IC-IR, Q5-Q1 spread, and sector distortions.

    records: list of dicts with keys:
      ts, symbol, universe, sector, value, quality, growth, momentum, risk,
      composite, realized_ret, bench_ret
    Returns {"overall": {factor: {...}}, "by_sector": {(universe, sector): {factor: ic}}, "warnings": [...]}.
    """
    by_ts = {}
    by_sector = {}
    for r in records:
        if r.get("realized_ret") is None:
            continue
        exc = float(r["realized_ret"]) - float(r.get("bench_ret") or 0.0)
        factors = (r.get("value"), r.get("quality"), r.get("growth"),
                   r.get("momentum"), r.get("risk"))
        row = {**r, "excess_ret": exc, "academic_1n": academic_composite(factors)}
        by_ts.setdefault(r["ts"], []).append(row)
        sec_key = (r.get("universe", "ALL"), r.get("sector") or "_default")
        by_sector.setdefault(sec_key, []).append(row)

    overall = {}
    warnings = []
    for f in (*FACTORS, "composite", "academic_1n"):
        ics, spreads = [], []
        for _ts, rows in sorted(by_ts.items()):
            sig = [x.get(f) for x in rows]
            exc = [x["excess_ret"] for x in rows]
            ic = spearman_ic(sig, exc)
            sp = quintile_spread(sig, exc)
            if ic is not None:
                ics.append(ic)
            if sp is not None:
                spreads.append(sp)
        mean_ic = sum(ics) / len(ics) if ics else None
        std_ic = statistics.stdev(ics) if len(ics) >= 2 else None
        ic_ir = (mean_ic / std_ic) if (mean_ic is not None and std_ic and std_ic > 1e-9) else (
            mean_ic * 10.0 if mean_ic is not None else None
        )
        mean_spread = sum(spreads) / len(spreads) if spreads else None
        hit_rate = (sum(1 for x in ics if x > 0) / len(ics)) if ics else None
        status = "healthy"
        if mean_ic is not None and len(ics) >= 3 and mean_ic < -0.02:
            status = "decaying"
            warnings.append(
                f"Factor '{f}' has negative mean Rank IC ({mean_ic:+.3f}) across "
                f"{len(ics)} cross-sections — check regime tilt or input definitions."
            )
        overall[f] = {
            "n_periods": len(ics),
            "mean_ic": mean_ic,
            "ic_ir": ic_ir,
            "mean_q5_q1_spread": mean_spread,
            "ic_hit_rate": hit_rate,
            "status": status,
        }

    sector_audit = {}
    for (uni, sec), rows in sorted(by_sector.items()):
        if len(rows) < MIN_COHORT_N:
            continue
        sec_metrics = {}
        for f in FACTORS:
            ic = spearman_ic([x.get(f) for x in rows], [x["excess_ret"] for x in rows])
            sec_metrics[f] = ic
            if ic is not None and ic < -0.10 and len(rows) >= 8:
                warnings.append(
                    f"Sector distortion: '{f}' in {uni}/{sec} has inverted IC ({ic:+.2f}, n={len(rows)}) "
                    f"— verify SECTOR_NEUTRAL=1 and metric_relevance weights."
                )
        sector_audit[(uni, sec)] = sec_metrics

    return {"overall": overall, "by_sector": sector_audit, "warnings": warnings}


# ---------------------------------------------------------------- 2. Archetype Cohort Validation

def evaluate_archetypes(records):
    """Evaluate realized returns, hit rates, and tail risk for both Practitioner and Academic archetypes.

    Returns {"cohorts": {archetype: {...}}, "academic_cohorts": {acad_arch: {...}},
             "soundness_checks": [...], "warnings": [...]}.
    """
    cohorts = {}
    acad_cohorts = {}
    for r in records:
        if r.get("realized_ret") is None:
            continue
        factors = (r.get("value"), r.get("quality"), r.get("growth"),
                   r.get("momentum"), r.get("risk"))
        arch = r.get("archetype") or stock_archetype(factors)
        acad = r.get("academic_archetype") or academic_archetype(factors)
        exc = float(r["realized_ret"]) - float(r.get("bench_ret") or 0.0)
        cohorts.setdefault(arch, []).append((exc, float(r["realized_ret"])))
        acad_cohorts.setdefault(acad, []).append((exc, float(r["realized_ret"])))

    def _summarize(group_map):
        out = {}
        for name, pairs in sorted(group_map.items()):
            excs = [p[0] for p in pairs]
            rets = [p[1] for p in pairs]
            out[name] = {
                "count": len(pairs),
                "mean_excess_ret": sum(excs) / len(excs),
                "median_excess_ret": statistics.median(excs),
                "hit_rate": sum(1 for e in excs if e > 0) / len(excs),
                "tail_p10_ret": quantile_cut(rets, 0.10),
            }
        return out

    summary = _summarize(cohorts)
    acad_summary = _summarize(acad_cohorts)

    checks, warnings = [], []

    # Check 1: Quality Compounder / GARP vs Lagging / Weak
    qc = summary.get("Quality Compounder")
    lw = summary.get("Lagging / Weak")
    if qc and lw and qc["count"] >= MIN_COHORT_N and lw["count"] >= MIN_COHORT_N:
        passed = qc["mean_excess_ret"] > lw["mean_excess_ret"]
        checks.append({
            "check": "Quality Compounder > Lagging / Weak",
            "passed": passed,
            "detail": f"{qc['mean_excess_ret']:+.2%} vs {lw['mean_excess_ret']:+.2%}",
        })
        if not passed:
            warnings.append("Archetype inversion: 'Quality Compounder' underperformed 'Lagging / Weak'.")

    # Check 2: Deep Value vs Value Trap separation
    dv = summary.get("Deep Value")
    vt = summary.get("Value Trap")
    if dv and vt and dv["count"] >= MIN_COHORT_N and vt["count"] >= MIN_COHORT_N:
        passed = dv["mean_excess_ret"] > vt["mean_excess_ret"]
        checks.append({
            "check": "Deep Value > Value Trap",
            "passed": passed,
            "detail": f"{dv['mean_excess_ret']:+.2%} vs {vt['mean_excess_ret']:+.2%}",
        })
        if not passed:
            warnings.append("Value Trap separation weak: 'Deep Value' did not beat 'Value Trap'.")

    # Check 3: Defensive Quality downside protection vs High-Risk / Stressed
    dq = summary.get("Defensive Quality")
    hr = summary.get("High-Risk / Stressed")
    if dq and hr and dq["count"] >= MIN_COHORT_N and hr["count"] >= MIN_COHORT_N:
        passed = (dq["tail_p10_ret"] or 0.0) > (hr["tail_p10_ret"] or 0.0)
        checks.append({
            "check": "Defensive Quality p10 tail > High-Risk / Stressed p10 tail",
            "passed": passed,
            "detail": f"{dq['tail_p10_ret']:+.2%} vs {hr['tail_p10_ret']:+.2%}",
        })
        if not passed:
            warnings.append("Defensive Quality failed tail-risk protection vs High-Risk / Stressed.")

    return {
        "cohorts": summary,
        "academic_cohorts": acad_summary,
        "soundness_checks": checks,
        "warnings": warnings,
    }


# ---------------------------------------------------------------- 3. Multibagger Gate Attribution

def multibagger_gate_flags(sector, annual, snap, metrics, size_pct):
    """Return dict of {gate_name: passed_bool} for all 6 multibagger gates.

    Allows diagnosing near-misses (companies that passed 5 of 6 gates and failed
    only one specific gate) so we can measure each gate's Marginal Value (GMV).
    """
    rev = cagr(annual.get("total_revenue", []))
    earn = cagr(annual.get("eps", [])) or cagr(annual.get("net_income", []))
    has_hist = (rev is not None and earn is not None)
    g_growth = bool(has_hist and rev >= MB_MIN_GROWTH and earn >= MB_MIN_GROWTH)

    roe = snap.get("roe")
    equity = annual.get("shareholder_equity", [(None, None)])[0][1]
    income = annual.get("net_income", [(None, None)])[0][1]
    if roe is None and equity and equity > 0 and income is not None:
        roe = income / equity
    g_roe = bool(roe is not None and roe >= MB_MIN_ROE)

    debt = annual.get("total_debt", [(None, None)])[0][1]
    leverage = debt / equity if debt is not None and equity and equity > 0 else None
    g_debt = bool(sector in FINANCIAL_SECTORS or leverage is None or leverage <= MB_MAX_DEBT_EQUITY)

    g_size = bool(size_pct is not None and size_pct <= MB_SIZE_CUT)

    pe = snap.get("pe")
    peg = (pe / (earn * 100)) if (pe and pe > 0 and earn and earn > 0) else None
    g_val = bool(peg is not None and peg <= MB_MAX_PEG)

    trend = metrics.get("sma200_ratio")
    g_trend = bool(trend is not None and trend >= 1.0)

    return {
        "growth": g_growth,
        "return on equity": g_roe,
        "debt": g_debt,
        "size": g_size,
        "valuation": g_val,
        "trend": g_trend,
    }


def evaluate_multibagger_gates(candidates):
    """Compute Gate Marginal Value (GMV) for each of the 6 multibagger gates.

    candidates: list of dicts with keys:
      symbol, sector, annual, snap, metrics, size_pct, realized_ret, bench_ret
    Returns {"passed_count": int, "passed_mean_excess": float, "gates": {gate: {...}}, "recommendations": [...]}.
    """
    passed_exc = []
    near_miss = {g: [] for g in ("growth", "return on equity", "debt", "size", "valuation", "trend")}
    failed_any = {g: [] for g in near_miss}

    for c in candidates:
        if c.get("realized_ret") is None:
            continue
        exc = float(c["realized_ret"]) - float(c.get("bench_ret") or 0.0)
        flags = multibagger_gate_flags(
            c.get("sector", ""),
            c.get("annual", {}),
            c.get("snap", {}),
            c.get("metrics", {}),
            c.get("size_pct"),
        )
        failed = [g for g, ok in flags.items() if not ok]
        if not failed:
            passed_exc.append(exc)
        else:
            for g in failed:
                failed_any[g].append(exc)
            if len(failed) == 1:
                near_miss[failed[0]].append(exc)

    passed_mean = (sum(passed_exc) / len(passed_exc)) if passed_exc else None
    gate_stats = {}
    recommendations = []

    for g in near_miss:
        nm = near_miss[g]
        fa = failed_any[g]
        ref = nm if len(nm) >= MIN_COHORT_N else fa
        ref_mean = (sum(ref) / len(ref)) if ref else None
        gmv = (passed_mean - ref_mean) if (passed_mean is not None and ref_mean is not None) else None
        gate_stats[g] = {
            "near_miss_count": len(nm),
            "total_failed_count": len(fa),
            "rejected_mean_excess": ref_mean,
            "gmv": gmv,
        }
        if gmv is not None and gmv < -0.02 and len(ref) >= MIN_COHORT_N:
            recommendations.append(
                f"Gate '{g}' has negative marginal value (GMV {gmv:+.2%}, n={len(ref)}): "
                f"near-misses rejected by '{g}' outperformed passing candidates. "
                f"Consider relaxing the '{g}' threshold."
            )

    return {
        "passed_count": len(passed_exc),
        "passed_mean_excess": passed_mean,
        "gates": gate_stats,
        "recommendations": recommendations,
    }


# ---------------------------------------------------------------- 4. Adaptive Calibration

def propose_factor_weights(factor_eval, prior=None):
    """Compute shrinkage-regularized adaptive factor weights under ADR 0018 caps.

    Blends prior weights W0 with empirical positive IC-IR weights using sample-size
    shrinkage lambda = min(0.50, N / (N + SHRINKAGE_N0)), then clamps each factor's
    tilt within [-MAX_TILT, +MAX_TILT] and normalizes to sum to 1.00.
    """
    prior = dict(prior or PRIOR_WEIGHTS)
    overall = factor_eval.get("overall", {})
    n_periods = min((overall.get(f, {}).get("n_periods", 0) for f in FACTORS), default=0)

    if n_periods < MIN_CROSS_SECTIONS:
        return {
            "weights": prior,
            "tilts": {f: 0.0 for f in FACTORS},
            "shrinkage_lambda": 0.0,
            "n_periods": n_periods,
            "reason": f"insufficient resolved cross-sections ({n_periods} < {MIN_CROSS_SECTIONS}); holding prior weights",
        }

    # Positive IC-IR signal strength; decaying/negative factors get floor score
    strengths = {}
    for f in FACTORS:
        ir = overall.get(f, {}).get("ic_ir")
        ic = overall.get(f, {}).get("mean_ic")
        if ir is not None and ir > 0:
            strengths[f] = float(ir)
        elif ic is not None and ic > 0:
            strengths[f] = float(ic) * 5.0
        else:
            strengths[f] = 0.05

    total_str = sum(strengths.values())
    emp = {f: strengths[f] / total_str for f in FACTORS} if total_str > 0 else dict(prior)

    lam = min(0.50, n_periods / (n_periods + SHRINKAGE_N0))
    raw = {f: (1.0 - lam) * prior[f] + lam * emp[f] for f in FACTORS}

    # Enforce ADR 0018 per-factor tilt cap (+/- MAX_TILT) and MIN_WEIGHT floor
    clamped = {
        f: max(MIN_WEIGHT, min(prior[f] + MAX_TILT, max(prior[f] - MAX_TILT, raw[f])))
        for f in FACTORS
    }
    # Iterative projection so weights sum to 1.0 while respecting [prior-MAX_TILT, prior+MAX_TILT]
    for _ in range(5):
        diff = 1.0 - sum(clamped.values())
        if abs(diff) < 1e-9:
            break
        free = [
            f for f in FACTORS
            if (diff > 0 and clamped[f] < prior[f] + MAX_TILT - 1e-9)
            or (diff < 0 and clamped[f] > max(MIN_WEIGHT, prior[f] - MAX_TILT) + 1e-9)
        ]
        if not free:
            break
        step = diff / len(free)
        for f in free:
            lo = max(MIN_WEIGHT, prior[f] - MAX_TILT)
            hi = prior[f] + MAX_TILT
            clamped[f] = max(lo, min(hi, clamped[f] + step))

    weights = {f: round(clamped[f], 4) for f in FACTORS}
    # Fix any 1e-4 rounding residual on the largest weight
    top_f = max(FACTORS, key=lambda k: weights[k])
    weights[top_f] = round(weights[top_f] + (1.0 - sum(weights.values())), 4)
    tilts = {f: round(weights[f] - prior[f], 4) for f in FACTORS}

    return {
        "weights": weights,
        "tilts": tilts,
        "shrinkage_lambda": round(lam, 3),
        "n_periods": n_periods,
        "reason": f"shrinkage-calibrated over {n_periods} cross-sections (lambda={lam:.2f}, cap=+/-{MAX_TILT:.0%})",
    }


# ---------------------------------------------------------------- 5. Report Renderer

def render_calibration_report(factor_eval, arch_eval, mb_eval, calibration, horizon=91, as_of=None):
    """Render a deterministic Markdown report for stock_reports ('__calibration__')."""
    as_of = as_of or dt.date.today().isoformat()
    lines = [
        f"# Continuous Classification & Factor Validation ({as_of})",
        "",
        f"Horizon: **{horizon}d** forward excess return vs universe benchmark · "
        f"Guardrails: ADR 0018 (±{MAX_TILT:.0%} max tilt cap, min {MIN_CROSS_SECTIONS} cross-sections).",
        "",
        "## 1. Factor Efficacy & Adaptive Weights",
        "",
        "| Factor | Prior W | Proposed W | Tilt | Periods | Mean IC | IC-IR | Q5−Q1 Spread | Status |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    overall = factor_eval.get("overall", {})
    w_prop = calibration["weights"]
    tilts = calibration["tilts"]
    labels = {
        "composite": "Composite (Practitioner 25/25/20/20/10)",
        "academic_1n": "Academic 1/N (DeMiguel 20/20/20/20/20)",
    }
    for f in (*FACTORS, "composite", "academic_1n"):
        if f not in overall:
            continue
        st = overall.get(f, {})
        pw = f"{PRIOR_WEIGHTS[f]:.0%}" if f in PRIOR_WEIGHTS else ("20%×5" if f == "academic_1n" else "—")
        nw = f"{w_prop[f]:.1%}" if f in w_prop else "—"
        tl = f"{tilts[f]:+.1%}" if f in tilts else "—"
        n_p = st.get("n_periods", 0)
        mic = f"{st['mean_ic']:+.3f}" if st.get("mean_ic") is not None else "n/a"
        ir = f"{st['ic_ir']:+.2f}" if st.get("ic_ir") is not None else "n/a"
        sp = f"{st['mean_q5_q1_spread']:+.2%}" if st.get("mean_q5_q1_spread") is not None else "n/a"
        fname = labels.get(f, f.capitalize())
        lines.append(f"| {fname} | {pw} | {nw} | {tl} | {n_p} | {mic} | {ir} | {sp} | {st.get('status', 'n/a')} |")

    lines += [
        "",
        f"_Calibration verdict_: {calibration['reason']}.",
        "",
        "## 2. Stock Archetype Cohort Validation (Practitioner vs Academic)",
        "",
        "### 2A. Practitioner Archetypes (25/25/20/20/10 Profile)",
        "",
        "| Archetype | N | Mean Excess | Median Excess | Hit Rate | P10 Tail Return |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for arch, c in sorted(arch_eval.get("cohorts", {}).items(),
                          key=lambda kv: -kv[1]["mean_excess_ret"]):
        p10 = f"{c['tail_p10_ret']:+.2%}" if c.get("tail_p10_ret") is not None else "n/a"
        lines.append(
            f"| {arch} | {c['count']} | {c['mean_excess_ret']:+.2%} | "
            f"{c['median_excess_ret']:+.2%} | {c['hit_rate']:.0%} | {p10} |"
        )

    if arch_eval.get("academic_cohorts"):
        lines += [
            "",
            "### 2B. Academic Research Archetypes (FF5 / QMJ / Piotroski / Mohanram / 1/N)",
            "",
            "| Academic Archetype | N | Mean Excess | Median Excess | Hit Rate | P10 Tail Return |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for arch, c in sorted(arch_eval.get("academic_cohorts", {}).items(),
                              key=lambda kv: -kv[1]["mean_excess_ret"]):
            p10 = f"{c['tail_p10_ret']:+.2%}" if c.get("tail_p10_ret") is not None else "n/a"
            lines.append(
                f"| {arch} | {c['count']} | {c['mean_excess_ret']:+.2%} | "
                f"{c['median_excess_ret']:+.2%} | {c['hit_rate']:.0%} | {p10} |"
            )

    if arch_eval.get("soundness_checks"):
        lines += ["", "### Archetype Thesis Checks"]
        for chk in arch_eval["soundness_checks"]:
            mark = "PASS" if chk["passed"] else "WARN"
            lines.append(f"- **[{mark}]** {chk['check']}: {chk['detail']}")

    pm_str = f"{mb_eval['passed_mean_excess']:+.2%}" if mb_eval.get("passed_mean_excess") is not None else "n/a"
    lines += [
        "",
        "## 3. Multibagger Gate Attribution (Marginal Value)",
        "",
        f"Passing candidates: **{mb_eval.get('passed_count', 0)}** (mean excess: {pm_str}).",
        "",
        "| Gate | Near-Misses | Total Rejected | Rejected Mean Excess | Gate Marginal Value (GMV) |",
        "|---|---:|---:|---:|---:|",
    ]
    for g, st in mb_eval.get("gates", {}).items():
        rm = f"{st['rejected_mean_excess']:+.2%}" if st.get("rejected_mean_excess") is not None else "n/a"
        gmv = f"{st['gmv']:+.2%}" if st.get("gmv") is not None else "n/a"
        lines.append(f"| {g} | {st['near_miss_count']} | {st['total_failed_count']} | {rm} | {gmv} |")

    all_warnings = (
        factor_eval.get("warnings", [])
        + arch_eval.get("warnings", [])
        + mb_eval.get("recommendations", [])
    )
    lines += ["", "## 4. Active Alerts & Calibration Recommendations"]
    if all_warnings:
        for w in all_warnings:
            lines.append(f"- {w}")
    else:
        lines.append("- All factor ICs, archetype cohorts, and multibagger gates are within soundness bounds.")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- DB runner (workstation PSQL_CMD)

def _q(s):
    return "'" + str(s).replace("'", "'\"'\"'") + "'"


def psql(sql):
    cmd = os.environ.get("PSQL_CMD")
    if not cmd:
        sys.exit("set PSQL_CMD (see module docstring)")
    out = subprocess.run(
        cmd + " -v ON_ERROR_STOP=1 -At -F, -c " + _q(sql),
        shell=True, check=True, capture_output=True, text=True,
    )
    return out.stdout


def fetch_resolved_scores(horizon=91, days=365):
    """Fetch resolved screener_rank outcomes joined with point-in-time stock_scores."""
    sql = (
        f"SELECT to_char(o.ts, 'YYYY-MM-DD'), o.symbol, s.universe, "
        f"COALESCE(u.sector, '_default'), "
        f"COALESCE(s.value_score, 0.5), COALESCE(s.quality_score, 0.5), "
        f"COALESCE(s.growth_score, 0.5), COALESCE(s.momentum_score, 0.5), "
        f"COALESCE(s.risk_score, 0.5), COALESCE(s.composite, 50.0), "
        f"o.realized_ret, COALESCE(o.bench_ret, 0.0) "
        f"FROM prediction_outcomes o "
        f"JOIN stock_scores s ON s.symbol = o.symbol AND s.ts = o.ts "
        f"LEFT JOIN universe_constituents u ON u.symbol = o.symbol AND u.universe = s.universe "
        f"WHERE o.kind = 'screener_rank' AND o.horizon_days = {int(horizon)} "
        f"AND o.ts >= now() - interval '{int(days)} days' "
        f"ORDER BY o.ts, o.symbol"
    )
    raw = psql(sql)
    records = []
    for line in raw.strip().splitlines():
        if not line:
            continue
        parts = line.split(",")
        if len(parts) < 12:
            continue
        records.append({
            "ts": parts[0],
            "symbol": parts[1],
            "universe": parts[2],
            "sector": parts[3],
            "value": float(parts[4]),
            "quality": float(parts[5]),
            "growth": float(parts[6]),
            "momentum": float(parts[7]),
            "risk": float(parts[8]),
            "composite": float(parts[9]),
            "realized_ret": float(parts[10]),
            "bench_ret": float(parts[11]),
        })
    return records


def build_calibration_metric_rows(factor_eval, arch_eval, mb_eval, calibration, horizon=91, as_of=None):
    """Build structured rows for calibration_metrics table (schema 25).

    Each tuple: (ts, horizon_days, kind, metric_key, universe, sample_n, value, aux_value, status)
    """
    ts = as_of or dt.date.today().isoformat()
    rows = []
    for f, st in factor_eval.get("overall", {}).items():
        rows.append((
            ts, int(horizon), "factor_ic", f, "ALL",
            int(st.get("n_periods") or 0),
            st.get("mean_ic"), st.get("ic_ir"), st.get("status"),
        ))
    for (univ, sec, f), st in factor_eval.get("by_sector", {}).items():
        rows.append((
            ts, int(horizon), "sector_ic", f"{sec}:{f}", univ,
            int(st.get("n_periods") or 0),
            st.get("mean_ic"), None,
            "distorted" if (st.get("mean_ic") or 0.0) < -0.05 else "healthy",
        ))
    for arch, c in arch_eval.get("cohorts", {}).items():
        rows.append((
            ts, int(horizon), "archetype_ret", arch, "ALL",
            int(c.get("count") or 0),
            c.get("mean_excess_ret"), c.get("hit_rate"), "practitioner",
        ))
    for arch, c in arch_eval.get("academic_cohorts", {}).items():
        rows.append((
            ts, int(horizon), "academic_archetype_ret", arch, "ALL",
            int(c.get("count") or 0),
            c.get("mean_excess_ret"), c.get("hit_rate"), "academic",
        ))
    for g, st in mb_eval.get("gates", {}).items():
        rows.append((
            ts, int(horizon), "mb_gmv", g, "ALL",
            int(st.get("near_miss_count") or 0),
            st.get("gmv"), st.get("rejected_mean_excess"),
            "restrictive" if (st.get("gmv") or 0.0) < -0.02 else "ok",
        ))
    w_prop = calibration.get("weights", {})
    tilts = calibration.get("tilts", {})
    n_p = int(calibration.get("n_periods") or 0)
    for f, w in w_prop.items():
        rows.append((
            ts, int(horizon), "shrunk_weight", f, "ALL",
            n_p, w, tilts.get(f), "capped_5pct",
        ))
    return rows


def write_calibration_metrics(rows):
    """Persist structured calibration rows into calibration_metrics (schema 25)."""
    if not rows:
        return
    vals = []
    for ts, h, kind, mkey, univ, n, val, aux, st in rows:
        k_esc = str(kind).replace("'", "''")
        m_esc = str(mkey).replace("'", "''")
        u_esc = str(univ).replace("'", "''")
        s_esc = ("'" + str(st).replace("'", "''") + "'") if st is not None else "NULL"
        v_sql = "NULL" if val is None else f"{float(val):.6f}"
        a_sql = "NULL" if aux is None else f"{float(aux):.6f}"
        vals.append(
            f"('{ts}'::timestamptz, {int(h)}, '{k_esc}', '{m_esc}', '{u_esc}', "
            f"{int(n)}, {v_sql}, {a_sql}, {s_esc})"
        )
    sql = (
        "INSERT INTO calibration_metrics "
        "(ts, horizon_days, kind, metric_key, universe, sample_n, value, aux_value, status) "
        "VALUES " + ", ".join(vals) + " "
        "ON CONFLICT (ts, horizon_days, kind, metric_key, universe) DO UPDATE SET "
        "sample_n = EXCLUDED.sample_n, value = EXCLUDED.value, "
        "aux_value = EXCLUDED.aux_value, status = EXCLUDED.status"
    )
    psql(sql)


def write_report_to_db(report_md):
    """Persist the calibration markdown report into stock_reports ('__calibration__')."""
    esc_md = report_md.replace("'", "''")
    sql = (
        "INSERT INTO stock_reports (symbol, ts, report_md, sentiment_avg) "
        f"VALUES ('__calibration__', date_trunc('day', now()), '{esc_md}', 0.0) "
        "ON CONFLICT (symbol, ts) DO UPDATE SET report_md = EXCLUDED.report_md"
    )
    psql(sql)


def run(horizon=91, days=365, write_db=True):
    records = fetch_resolved_scores(horizon=horizon, days=days)
    f_eval = evaluate_factors(records)
    a_eval = evaluate_archetypes(records)
    mb_eval = evaluate_multibagger_gates([])
    cal = propose_factor_weights(f_eval)
    md = render_calibration_report(f_eval, a_eval, mb_eval, cal, horizon=horizon)
    metric_rows = build_calibration_metric_rows(f_eval, a_eval, mb_eval, cal, horizon=horizon)

    os.makedirs(OUT, exist_ok=True)
    today = dt.date.today().isoformat()
    md_path = f"{OUT}/report-{today}.md"
    json_path = f"{OUT}/calibration-{today}.json"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {"factors": f_eval["overall"], "archetypes": a_eval["cohorts"],
             "academic_archetypes": a_eval.get("academic_cohorts", {}),
             "calibration": cal, "warnings": f_eval["warnings"] + a_eval["warnings"]},
            f, indent=2, default=str,
        )
    if write_db:
        write_report_to_db(md)
        write_calibration_metrics(metric_rows)
    print(f"CLASSIFIER-LOOP-OK records={len(records)} rows={len(metric_rows)} report={md_path}", flush=True)
    return md


def show():
    raw = psql(
        "SELECT to_char(ts, 'YYYY-MM-DD'), report_md FROM stock_reports "
        "WHERE symbol = '__calibration__' ORDER BY ts DESC LIMIT 1"
    )
    print(raw.strip() or "No __calibration__ report in stock_reports yet.")


def main():
    ap = argparse.ArgumentParser(description="Continuous classification & factor calibration loop")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="evaluate resolved outcomes + write calibration report")
    r.add_argument("--horizon", type=int, default=91, choices=(30, 91, 182))
    r.add_argument("--days", type=int, default=365)
    r.add_argument("--no-db", action="store_true", help="write local files only")
    sub.add_parser("show", help="display latest __calibration__ report from stock_reports")
    a = ap.parse_args()
    if a.cmd == "run":
        run(horizon=a.horizon, days=a.days, write_db=not a.no_db)
    elif a.cmd == "show":
        show()


if __name__ == "__main__":
    main()
