"""Deterministic intrinsic-value estimates for EVERY active symbol (ADR 0026).

The famous methods, each stored per stock per day in stock_valuations and NULL
whenever its inputs don't apply — no method is forced:
- graham:          sqrt(22.5 * EPS * BVPS) (Graham's 15 P/E x 1.5 P/B bound)
- peer_multiple:   sector-median P/E x EPS; for styles where metric_relevance
                   (schema 16) marks P/E misleading and P/B primary
                   (financials, real estate, commodities) it is median P/B x BVPS
- fcf_yield:       FCF/share / r — zero-growth perpetuity
- dcf:             two-stage FCF DCF — 5 years at the revenue CAGR clamped to
                   [0%, 15%], terminal growth 2.5%
- ddm:             Gordon growth on dividends/share (payers with >=3 years)
- epv:             Greenwald earnings-power value — normalized EBIT (mean of
                   up to 5 annual operating_income) x (1 - 25% tax) / r, minus
                   total debt, per share. NO cash credit (cash isn't ingested),
                   so it leans conservative — pinned in ADR 0026
- residual_income: justified P/B — (ROE - g)/(r - g) x BVPS when ROE > r,
                   multiple capped at 8
- peg_lynch:       PEG = 1 fair value — EPS x 100 x EPS CAGR clamped [5%, 25%]
- damodaran_fcff:  Damodaran two-stage FCFF (ADR 0030): NOPAT with fundamental
                   growth (reinvestment = g/ROC, ROC on book capital), 5y at
                   the revenue CAGR then terminal 2.5%, discounted at WACC
                   (ke = 10y + ERP; kd = 10y + 2% after 25% tax, market-value
                   weights); debt netted, no cash credit (as EPV)
- graham_growth:   Graham's revised 1974 formula EPS x (8.5 + 2g%) x 4.4/Y,
                   g = EPS CAGR clamped [0, 15%], Y = 10y + 1% (AAA proxy)
fair_value = median of whatever produced a number; methods = how many did.

Sector lens comes from sector_styles + metric_relevance (schema 16); annual
histories from financial_facts (EV-9 lines included); shares are implied
(market_cap / close) until shares_outstanding coverage completes. Also writes
the daily sector_metrics aggregates (schema 17) in the same transaction.

DESCRIPTIVE ONLY: nothing here feeds stock_scores/composite without an EV-0
pass (ADR 0026). r = latest 10y (DGS10 / IN_GS_10Y) + 4.5% ERP.

Env: PGHOST, PGUSER, PGPASSWORD, PGDATABASE. OK-marker: VALUATION-OK.
"""

import statistics
import sys
from datetime import datetime, timezone

from mdjob import connect, job_run

ERP = 4.5                                   # equity risk premium, percent
TERMINAL_G = 0.025                          # terminal growth, decimal
TAX = 0.25                                  # EPV normalized tax rate
DCF_YEARS = 5
JPB_CAP = 8.0                               # residual-income multiple cap
DEBT_SPREAD = 0.02                          # synthetic cost-of-debt spread over rf (ADR 0030)
AAA_SPREAD = 1.0                            # AAA-corporate proxy over the 10y, percent (Graham Y)
RR_CAP = 0.9                                # reinvestment-rate ceiling (Damodaran FCFF)
# Rates are per MARKET, not per universe (ADR 0029: five universes, two markets).
FALLBACK_10Y = {"US": 4.5, "IN": 7.0}
TENOR = {"US": "DGS10", "IN": "IN_GS_10Y"}


def market(universe):
    return "IN" if universe.startswith("NIFTY") else "US"
HIST_METRICS = ("total_revenue", "eps", "dividends_paid", "ocf", "capex",
                "operating_income", "fcf", "gross_profit", "shares_outstanding",
                "total_assets", "net_income", "total_debt", "current_assets",
                "current_liabilities")


def discount_rates(cur):
    """{market: decimal rate} from the latest 10y print (rule-9 bounded)."""
    out = {}
    for universe, instrument in TENOR.items():
        cur.execute("SELECT yield FROM bond_yields WHERE instrument=%s "
                    "AND ts >= now() - interval '45 days' ORDER BY ts DESC LIMIT 1",
                    (instrument,))
        row = cur.fetchone()
        tenor = row[0] if row else FALLBACK_10Y[universe]
        if not row:
            print(f"WARN {universe}: no fresh {instrument} — fallback "
                  f"{FALLBACK_10Y[universe]}%", flush=True)
        out[universe] = (tenor + ERP) / 100.0
    return out


def style_weights(cur):
    """({sector: style}, {(style, metric): weight})."""
    cur.execute("SELECT sector, style FROM sector_styles")
    styles = dict(cur.fetchall())
    cur.execute("SELECT style, metric, weight FROM metric_relevance")
    weights = {(s, m): w for s, m, w in cur.fetchall()}
    return styles, weights


def load_inputs(cur):
    """{symbol: {...latest snapshot inputs...}} in three bulk queries."""
    cur.execute("SELECT symbol, universe, coalesce(sector,'_default') "
                "FROM universe_constituents WHERE active")
    data = {s: {"universe": u, "sector": sec} for s, u, sec in cur.fetchall()}
    cur.execute("SELECT DISTINCT ON (symbol) symbol, close FROM stock_prices "
                "WHERE ts >= now() - interval '14 days' AND close > 0 "
                "ORDER BY symbol, ts DESC")
    for symbol, close in cur.fetchall():
        if symbol in data:
            data[symbol]["close"] = close
    cur.execute("SELECT DISTINCT ON (symbol, metric) symbol, metric, value "
                "FROM stock_fundamentals WHERE ts >= now() - interval '500 days' "
                "AND metric IN ('eps','shareholder_equity','fcf','market_cap','pe','pb',"
                "'ev_ebitda','roe','operating_margin','total_debt','gross_profit',"
                "'shares_outstanding','total_assets','net_income','operating_income',"
                "'current_assets','current_liabilities','ocf') "
                "AND (source='yfinance' OR metric NOT IN ('eps','fcf','gross_profit','net_income','operating_income','ocf')) "
                "ORDER BY symbol, metric, ts DESC")
    for symbol, metric, value in cur.fetchall():
        if symbol in data:
            data[symbol][metric] = value
    cur.execute("SELECT DISTINCT ON (symbol, metric) symbol, metric, value "
                "FROM stock_metrics_daily WHERE ts >= now() - interval '14 days' "
                "AND metric IN ('ret_1m','ret_6m','ret_12m','ret_12_1','pos_52w','sma200_ratio','near_high') "
                "ORDER BY symbol, metric, ts DESC")
    for symbol, metric, value in cur.fetchall():
        if symbol in data:
            data[symbol][metric] = value
    cur.execute("SELECT DISTINCT ON (symbol) symbol, composite, value_score, quality_score, "
                "growth_score, momentum_score, risk_score FROM stock_scores "
                "WHERE ts >= now() - interval '14 days' ORDER BY symbol, ts DESC")
    for sym, comp, vs, qs, gs, ms, rs in cur.fetchall():
        if sym in data:
            data[sym]["scores"] = (comp, vs, qs, gs, ms, rs)
    return data


def load_history(cur):
    """{symbol: {metric: [(period_end, value) ascending]}} — ANNUAL rows only,
    first-filed value per period (financial_facts is point-in-time, DQ-2)."""
    cur.execute("SELECT DISTINCT ON (symbol, metric, period_end) "
                "symbol, metric, period_end, value FROM financial_facts "
                "WHERE metric = ANY(%s) AND duration_days BETWEEN 300 AND 400 "
                "ORDER BY symbol, metric, period_end, filed DESC",
                (list(HIST_METRICS),))
    hist = {}
    for symbol, metric, end, value in cur.fetchall():
        hist.setdefault(symbol, {}).setdefault(metric, []).append((end, value))
    for metrics in hist.values():
        for series in metrics.values():
            series.sort()
    return hist


def cagr(series, min_periods=3):
    """Compound annual growth between the first and last positive values."""
    pts = [(d, v) for d, v in series if isinstance(v, (int, float)) and v > 0]
    if len(pts) < min_periods:
        return None
    (d0, v0), (d1, v1) = pts[0], pts[-1]
    years = (d1 - d0).days / 365.25
    if years < 1:
        return None
    return (v1 / v0) ** (1 / years) - 1


def clamp(v, lo, hi):
    return None if v is None else max(lo, min(hi, v))


def latest_annual(hist_m):
    """Last annual value of a series, or None."""
    return hist_m[-1][1] if hist_m else None


def latest_fcf(hist, snap):
    """Annual FCF: EDGAR ocf-capex for the latest shared period; else the fcf
    series (Yahoo leg); else the stock_fundamentals snapshot value."""
    ocf = dict(hist.get("ocf", []))
    capex = dict(hist.get("capex", []))
    shared = sorted(set(ocf) & set(capex))
    if shared:
        end = shared[-1]
        return ocf[end] - capex[end]
    fcf = latest_annual(hist.get("fcf", []))
    return fcf if fcf is not None else snap.get("fcf")


MEDIAN_METRICS = ("pe", "pb", "ev_ebitda", "roe", "operating_margin")
MEDIAN_CAP = {"pe": 200, "pb": 200, "ev_ebitda": 200, "roe": 5, "operating_margin": 1}


MEDIAN_MIN_N = 5   # a "sector median" over fewer inputs is noise, not a view


def sector_medians(data):
    """{(universe, sector): {metric: median}} over sane positive values;
    a metric with fewer than MEDIAN_MIN_N inputs stays absent (2026-10-27:
    one thin SP400 name printed as 'Materials P/E 7.2, +209%')."""
    buckets = {}
    for d in data.values():
        key = (d["universe"], d["sector"])
        for m in MEDIAN_METRICS:
            v = d.get(m)
            if isinstance(v, (int, float)) and 0 < v < MEDIAN_CAP[m]:
                buckets.setdefault(key, {}).setdefault(m, []).append(v)
    return {key: {m: statistics.median(vs) for m, vs in ms.items()
                  if len(vs) >= MEDIAN_MIN_N}
            for key, ms in buckets.items()}


def value_one(d, hist, medians, styles, weights, rates):
    """Dict of method values + fair/upside/growth, or None without a close."""
    close = d.get("close")
    if not close:
        return None
    style = styles.get(d["sector"], "default")
    w = lambda metric: weights.get((style, metric), weights.get(("default", metric), 2))  # noqa: E731
    r = rates[market(d["universe"])]
    med = medians.get((d["universe"], d["sector"]), {})

    mcap, eps, equity = d.get("market_cap"), d.get("eps"), d.get("shareholder_equity")
    shares = mcap / close if mcap and mcap > 0 else None
    bvps = equity / shares if shares and equity and equity > 0 else None

    out = {"discount_rate": r, "growth_used": None}
    out["graham"] = ((22.5 * eps * bvps) ** 0.5
                     if eps and eps > 0 and bvps and bvps > 0 else None)
    if w("pe") <= 1 and w("pb") >= 3:
        out["peer_multiple"] = med.get("pb") * bvps if med.get("pb") and bvps else None
    else:
        out["peer_multiple"] = (med.get("pe") * eps
                                if med.get("pe") and eps and eps > 0 else None)

    fcf0 = latest_fcf(hist, d)
    fcf_ok = fcf0 and fcf0 > 0 and shares and w("fcf") > 0
    out["fcf_yield"] = fcf0 / shares / r if fcf_ok else None

    g = clamp(cagr(hist.get("total_revenue", [])), 0.0, 0.15)
    if fcf_ok and g is not None:
        out["growth_used"] = g
        pv, f = 0.0, fcf0
        for t in range(1, DCF_YEARS + 1):
            f *= 1 + g
            pv += f / (1 + r) ** t
        pv += (f * (1 + TERMINAL_G) / (r - TERMINAL_G)) / (1 + r) ** DCF_YEARS
        out["dcf"] = pv / shares
    else:
        out["dcf"] = None

    divs = hist.get("dividends_paid", [])
    d0 = latest_annual(divs)
    gd = clamp(cagr(divs), 0.0, min(0.08, r - 0.01))
    out["ddm"] = (d0 / shares * (1 + gd) / (r - gd)
                  if d0 and d0 > 0 and shares and gd is not None else None)

    ebits = [v for _e, v in hist.get("operating_income", [])[-5:]
             if isinstance(v, (int, float))]
    if ebits and shares:
        epv_equity = (statistics.mean(ebits) * (1 - TAX) / r) - (d.get("total_debt") or 0)
        out["epv"] = epv_equity / shares if epv_equity > 0 else None
    else:
        out["epv"] = None

    roe = d.get("roe")
    if roe and bvps and roe > r:
        jpb = min((roe - TERMINAL_G) / (r - TERMINAL_G), JPB_CAP)
        out["residual_income"] = jpb * bvps
    else:
        out["residual_income"] = None

    ge = clamp(cagr(hist.get("eps", [])), 0.05, 0.25)
    out["peg_lynch"] = (eps * ge * 100
                        if eps and eps > 0 and ge is not None else None)

    # Damodaran FCFF (ADR 0030): fundamental growth — reinvestment = g/ROC —
    # NOPAT-based firm value at WACC, debt netted (no cash credit, like EPV).
    out["damodaran_fcff"] = None
    ebit0 = latest_annual(hist.get("operating_income", []))
    debt = d.get("total_debt") or 0
    if (ebit0 and ebit0 > 0 and shares and mcap and equity and equity > 0
            and g is not None):
        nopat = ebit0 * (1 - TAX)
        roc = nopat / (debt + equity)
        rf = r - ERP / 100.0
        kd = (rf + DEBT_SPREAD) * (1 - TAX)
        wacc = (mcap * r + debt * kd) / (mcap + debt)
        if roc > TERMINAL_G and wacc > TERMINAL_G:
            rr = clamp(g / roc, 0.0, RR_CAP)
            pv, n = 0.0, nopat
            for t in range(1, DCF_YEARS + 1):
                n *= 1 + g
                pv += n * (1 - rr) / (1 + wacc) ** t
            rr_t = clamp(TERMINAL_G / roc, 0.0, RR_CAP)
            tv = n * (1 + TERMINAL_G) * (1 - rr_t) / (wacc - TERMINAL_G)
            pv += tv / (1 + wacc) ** DCF_YEARS
            eq_value = pv - debt
            out["damodaran_fcff"] = eq_value / shares if eq_value > 0 else None

    # Graham's revised growth formula (1974): V = EPS x (8.5 + 2g%) x 4.4 / Y,
    # Y = AAA corporate yield, proxied as the 10y + AAA_SPREAD (ADR 0030).
    gg = clamp(cagr(hist.get("eps", [])), 0.0, 0.15)
    y_pct = (r - ERP / 100.0) * 100 + AAA_SPREAD
    out["graham_growth"] = (eps * (8.5 + 2 * gg * 100) * 4.4 / y_pct
                            if eps and eps > 0 and gg is not None and y_pct > 0
                            else None)

    # --- Extended Practitioner & Research-Backed Valuation Methods ---
    # 11. EV/EBITDA sector peer multiple (Practitioner: Koller et al. 2020; Loughran & Wellman 2011)
    out["ev_ebitda_peer"] = None
    ev_ebitda = d.get("ev_ebitda")
    med_ev = med.get("ev_ebitda")
    if w("ev_ebitda") > 0 and ev_ebitda and ev_ebitda > 0 and med_ev and mcap and shares:
        implied_ebitda = (mcap + debt) / ev_ebitda
        eq_ev_peer = med_ev * implied_ebitda - debt
        out["ev_ebitda_peer"] = eq_ev_peer / shares if eq_ev_peer > 0 else None

    # 12. Buffett Owner-Earnings Value (Practitioner: Buffett 1986 letter — normalized multi-year FCF)
    out["owner_earnings"] = None
    ocf_map = dict(hist.get("ocf", []))
    capex_map = dict(hist.get("capex", []))
    shared_fcfs = [ocf_map[dt] - capex_map[dt] for dt in sorted(set(ocf_map) & set(capex_map))[-5:]]
    if not shared_fcfs:
        shared_fcfs = [v for _dt, v in hist.get("fcf", [])[-5:] if isinstance(v, (int, float))]
    if not shared_fcfs and fcf0:
        shared_fcfs = [fcf0]
    if shared_fcfs and shares and w("fcf") > 0 and r > TERMINAL_G:
        norm_fcf = statistics.mean(shared_fcfs)
        if norm_fcf > 0:
            oe_eq = norm_fcf * (1 + TERMINAL_G) / (r - TERMINAL_G) - debt
            out["owner_earnings"] = oe_eq / shares if oe_eq > 0 else None

    # 13. Easton (2004) / Ohlson-Juettner (2005) Abnormal Earnings Growth / Modified PEG (Research-Backed)
    # Replaces Lynch's magic 100 (which hardcodes r=10%, d=0) with 1/r^2 + dividend reinvestment.
    dps0 = (d0 / shares) if (d0 and d0 > 0 and shares) else 0.0
    out["easton_mpeg"] = ((eps * ge + r * dps0 * ge) / (r ** 2)
                          if eps and eps > 0 and ge is not None and r > 0 else None)

    # 14. Peters & Taylor (2017) Intangible-Adjusted Residual Income / Total q (Research-Backed)
    # Capitalizes 30% of overhead (GP - EBIT) over a 5y life (1.5x steady-state stock) into Book Equity.
    out["intangible_ri"] = None
    gp0 = latest_annual(hist.get("gross_profit", [])) or d.get("gross_profit")
    if gp0 and ebit0 is not None and gp0 > ebit0 and equity and equity > 0 and shares and roe:
        overhead = gp0 - ebit0
        k_int = 1.5 * overhead
        adj_eq = equity + k_int
        adj_ni = roe * equity + 0.10 * overhead
        adj_roe = adj_ni / adj_eq
        adj_bvps = adj_eq / shares
        if adj_roe > r and r > TERMINAL_G:
            adj_jpb = min((adj_roe - TERMINAL_G) / (r - TERMINAL_G), JPB_CAP)
            out["intangible_ri"] = adj_jpb * adj_bvps

    # 15. Boudoukh et al. (2007) Total Shareholder Payout (Dividends + Net Buybacks) DDM (Research-Backed)
    out["total_payout_ddm"] = None
    sh_hist = [(dt, v) for dt, v in hist.get("shares_outstanding", [])
               if isinstance(v, (int, float)) and v > 0]
    buyback_cash = 0.0
    if len(sh_hist) >= 2 and sh_hist[-1][1] < sh_hist[-2][1] and mcap:
        bb_yield = min(0.08, (sh_hist[-2][1] - sh_hist[-1][1]) / sh_hist[-2][1])
        buyback_cash = bb_yield * mcap
    tot_payout = (d0 if d0 and d0 > 0 else 0.0) + buyback_cash
    if fcf0 and fcf0 > 0:
        tot_payout = min(tot_payout, fcf0)
    gp_rate = gd if gd is not None else clamp(g, 0.0, min(0.08, r - 0.01))
    if tot_payout > 0 and buyback_cash > 0 and shares and gp_rate is not None and r > gp_rate:
        out["total_payout_ddm"] = (tot_payout / shares) * (1 + gp_rate) / (r - gp_rate)

    methods = [out[k] for k in METHOD_COLS if out.get(k)]
    out["methods"] = len(methods)
    out["fair_value"] = statistics.median(methods) if methods else None
    out["upside_pct"] = ((out["fair_value"] / close - 1) * 100
                         if out["fair_value"] else None)

    prac_vals = [out[k] for k in PRACTITIONER_COLS if out.get(k)]
    acad_vals = [out[k] for k in ACADEMIC_COLS if out.get(k)]
    all_vals = prac_vals + acad_vals
    out["practitioner_methods"] = len(prac_vals)
    out["practitioner_fair_value"] = statistics.median(prac_vals) if prac_vals else None
    out["academic_methods"] = len(acad_vals)
    out["academic_fair_value"] = statistics.median(acad_vals) if acad_vals else None
    out["all_methods"] = len(all_vals)
    out["all_fair_value"] = statistics.median(all_vals) if all_vals else None
    return out


def _mean(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return sum(vals) / len(vals) if vals else None


def sector_rows(data, medians, upsides, today):
    """sector_metrics rows: medians + equal-weighted returns + median upside."""
    groups = {}
    for symbol, d in data.items():
        groups.setdefault((d["universe"], d["sector"]), []).append(symbol)
    rows = []
    for (universe, sector), symbols in sorted(groups.items()):
        med = medians.get((universe, sector), {})
        ups = [upsides[s] for s in symbols if upsides.get(s) is not None]
        rows.append((universe, sector, today, len(symbols),
                     med.get("pe"), med.get("pb"), med.get("ev_ebitda"),
                     med.get("roe"), med.get("operating_margin"),
                     _mean(data[s].get("ret_6m") for s in symbols),
                     _mean(data[s].get("ret_12m") for s in symbols),
                     statistics.median(ups) if len(ups) >= MEDIAN_MIN_N else None))
    return rows


METHOD_COLS = ("graham", "peer_multiple", "fcf_yield", "dcf", "ddm", "epv",
               "residual_income", "peg_lynch", "damodaran_fcff", "graham_growth")
PRACTITIONER_COLS = ("graham", "graham_growth", "peg_lynch", "peer_multiple",
                     "ev_ebitda_peer", "fcf_yield", "dcf", "owner_earnings")
ACADEMIC_COLS = ("residual_income", "intangible_ri", "easton_mpeg",
                 "damodaran_fcff", "epv", "ddm", "total_payout_ddm")
EXTENDED_COLS = ("ev_ebitda_peer", "owner_earnings", "easton_mpeg",
                 "intangible_ri", "total_payout_ddm")

VALUATION_CATALOG = (
    ("graham", "Graham Number", "valuation", "Practitioner", "Graham Conservative",
     "Graham (1949, The Intelligent Investor)", "sqrt(22.5 * EPS * BVPS)", "stock_valuations", 1),
    ("graham_growth", "Graham 1974 Growth", "valuation", "Practitioner", "Graham Conservative",
     "Graham (1974, The Decade 1965-1974)", "EPS * (8.5 + 2 * g_eps%) * 4.4 / Y_AAA%", "stock_valuations", 2),
    ("peg_lynch", "Lynch PEG = 1.0", "valuation", "Practitioner", "GARP & Peer Multiples",
     "Lynch (1989, One Up On Wall Street)", "EPS * 100 * clamp(g_eps, 5%, 25%)", "stock_valuations", 3),
    ("peer_multiple", "Sector P/E or P/B Peer", "valuation", "Practitioner", "GARP & Peer Multiples",
     "Street Comps (ADR 0026)", "median(PE_sector) * EPS (or median(PB_sector) * BVPS for financials)", "stock_valuations", 4),
    ("ev_ebitda_peer", "EV/EBITDA Sector Peer", "valuation", "Practitioner", "GARP & Peer Multiples",
     "Koller, Goedhart & Wessels (2020, McKinsey Valuation)", "(median(EV_EBITDA_sector) * implied_EBITDA - total_debt) / shares", "stock_valuations", 5),
    ("fcf_yield", "FCF Perpetuity", "valuation", "Practitioner", "Cash-Flow & Buffett",
     "Practitioner Cash-Yield Floor (ADR 0026)", "(FCF_0 / shares) / r", "stock_valuations", 6),
    ("dcf", "Two-Stage FCF DCF", "valuation", "Practitioner", "Cash-Flow & Buffett",
     "5y Revenue-CAGR FCF DCF (ADR 0026)", "PV(5y FCF at clamp(g_rev, 0%, 15%) + 2.5% terminal, discounted at r) / shares", "stock_valuations", 7),
    ("owner_earnings", "Buffett Owner Earnings", "valuation", "Practitioner", "Cash-Flow & Buffett",
     "Buffett (1986 Berkshire Shareholder Letter)", "(mean_5y(OCF - Capex) * (1 + g_T) / (r - g_T) - total_debt) / shares", "stock_valuations", 8),
    ("residual_income", "Residual Income (Justified P/B)", "valuation", "Research-Backed", "Clean-Surplus & Intangibles",
     "Ohlson (1995, CAR); Frankel & Lee (1998, JAE); Penman (2012)", "min((ROE - g_T) / (r - g_T), 8.0) * BVPS (when ROE > r)", "stock_valuations", 9),
    ("intangible_ri", "Intangible-Adjusted RI (Total q)", "valuation", "Research-Backed", "Clean-Surplus & Intangibles",
     "Peters & Taylor (2017, JFE); Eisfeldt & Papanikolaou (2013, JF); Arnott et al. (2021, FAJ)", "min((ROE_adj - g_T) / (r - g_T), 8.0) * ((Equity + 1.5 * (GP - EBIT)) / shares)", "stock_valuations", 10),
    ("easton_mpeg", "Easton Modified PEG (MPEG)", "valuation", "Research-Backed", "Abnormal Earnings Growth",
     "Easton (2004, The Accounting Review); Ohlson & Juettner-Nauroth (2005, RAST)", "(EPS_0 * g_eps + r * DPS_0 * g_eps) / (r^2)", "stock_valuations", 11),
    ("damodaran_fcff", "Damodaran FCFF (g/ROC)", "valuation", "Research-Backed", "Corporate Finance",
     "Modigliani & Miller (1958, AER); Damodaran (2012, Investment Valuation)", "(PV(5y NOPAT * (1 - g/ROC) + terminal at WACC) - total_debt) / shares", "stock_valuations", 12),
    ("epv", "Greenwald 5y EPV", "valuation", "Research-Backed", "Corporate Finance",
     "Greenwald, Kahn, Sonkin & van Biema (2001, Value Investing)", "(mean_5y(EBIT) * (1 - 0.25) / r - total_debt) / shares", "stock_valuations", 13),
    ("ddm", "Gordon Dividend Discount", "valuation", "Research-Backed", "Shareholder Payout",
     "Williams (1938); Gordon & Shapiro (1956, Mgmt Sci); Gordon (1959, REStat)", "DPS_0 * (1 + g_div) / (r - g_div)", "stock_valuations", 14),
    ("total_payout_ddm", "Total Payout (Div + Buyback) DDM", "valuation", "Research-Backed", "Shareholder Payout",
     "Boudoukh, Michaely, Richardson & Roberts (2007, JF); Fama & French (2001, JFE)", "(min(FCF_0, Dividends_0 + Net_Buybacks_0) / shares) * (1 + g_p) / (r - g_p)", "stock_valuations", 15),
    ("gpa", "Gross Profitability (GP/A)", "scientific_diagnostic", "Research-Backed", "Profitability (EV-10)",
     "Novy-Marx (2013, JFE)", "gross_profit / total_assets", "stock_scientific_metrics", 16),
    ("operating_accruals", "Operating Accruals", "scientific_diagnostic", "Research-Backed", "Earnings Quality (EV-12)",
     "Sloan (1996, The Accounting Review)", "(net_income - ocf) / total_assets", "stock_scientific_metrics", 17),
    ("asset_growth_cma", "Asset Growth / Investment (CMA)", "scientific_diagnostic", "Research-Backed", "Conservative Investment (EV-11)",
     "Cooper, Gulen & Schill (2008, JF); Fama & French (2015, JFE)", "total_assets_t / total_assets_t1 - 1", "stock_scientific_metrics", 18),
    ("net_share_issuance", "Net Share Issuance", "scientific_diagnostic", "Research-Backed", "Financing Factor (EV-13)",
     "Pontiff & Woodgate (2008, JF)", "shares_outstanding_t / shares_outstanding_t1 - 1", "stock_scientific_metrics", 19),
    ("piotroski_f_score", "Piotroski 9-Signal F-Score", "scientific_diagnostic", "Research-Backed", "Accounting Composite (EV-14)",
     "Piotroski (2000, Journal of Accounting Research)", "9 binary signals across Profitability (4), Leverage/Liquidity (3), Efficiency (2)", "stock_scientific_metrics", 20),
    ("altman_z2", "Altman Z''-Score", "scientific_diagnostic", "Research-Backed", "Solvency & Distress",
     "Altman (1968, JF; 2000 Z'' non-manufacturing)", "6.56*(WC/TA) + 3.26*(RE/TA) + 6.72*(EBIT/TA) + 1.05*(Eq/Debt)", "stock_scientific_metrics", 21),
    ("ret_12_1", "12-1 Month Momentum (ex-1m reversal)", "scientific_diagnostic", "Research-Backed", "Momentum (EV-3)",
     "Jegadeesh & Titman (1993, JF); Jegadeesh (1990, JF)", "adj_close[t-21] / adj_close[t-252] - 1", "stock_metrics_daily", 22),
    ("near_high", "52-Week High Proximity", "scientific_diagnostic", "Research-Backed", "Anchoring Momentum (EV-4)",
     "George & Hwang (2004, JF)", "close[t] / max(close[t-252:t])", "stock_metrics_daily", 23),
    ("value_score", "Value Factor Pillar", "factor_pillar", "Research-Backed", "Cross-Sectional Value",
     "Fama & French (1992, JF); Basu (1977, JF); Loughran & Wellman (2011, JFQA)", "mean(pct_rank(-PE), pct_rank(-PB), pct_rank(-EV/EBITDA), pct_rank(FCF_Yield))", "stock_scores", 24),
    ("quality_score", "Quality Factor Pillar", "factor_pillar", "Research-Backed", "Profitability & Safety (RMW / QMJ)",
     "Fama & French (2015, JFE); Asness, Frazzini & Pedersen (2019, RAST)", "mean(pct_rank(ROE), pct_rank(Operating_Margin), pct_rank(-Debt/Equity))", "stock_scores", 25),
    ("growth_score", "Growth Factor Pillar", "factor_pillar", "Practitioner", "Historical Revenue & EPS Growth",
     "Ball & Brown (1968, JAR); Chan, Karceski & Lakonishok (2003, JF)", "0.45*pct_rank(Rev_CAGR_3y) + 0.35*pct_rank(EPS_YoY) + 0.20*pct_rank(EPS_Accel)", "stock_scores", 26),
    ("momentum_score", "Momentum Factor Pillar", "factor_pillar", "Research-Backed", "Cross-Sectional & Trend Momentum (UMD)",
     "Jegadeesh & Titman (1993, JF); George & Hwang (2004, JF)", "mean(pct_rank(ret_6m), pct_rank(ret_12m), pct_rank(pos_52w), pct_rank(sma200_ratio))", "stock_scores", 27),
    ("risk_score", "Low-Risk / Stability Factor Pillar", "factor_pillar", "Research-Backed", "Low-Volatility & Drawdown (BAB)",
     "Ang, Hodrick, Xing & Zhang (2006, JF); Frazzini & Pedersen (2014, JFE)", "mean(pct_rank(-vol_180d), pct_rank(-max_dd_252d))", "stock_scores", 28),
    ("practitioner_composite", "Practitioner Composite Score (25/25/20/20/10)", "composite", "Practitioner", "Multi-Factor Composite",
     "House v1 Balanced Multi-Factor Composite", "(0.25*Value + 0.25*Quality + 0.20*Growth + 0.20*Momentum + 0.10*Risk) * 100", "stock_scientific_metrics", 29),
    ("academic_composite", "Academic 1/N Equal-Weighted Composite (20/20/20/20/20)", "composite", "Research-Backed", "Multi-Factor Composite (1/N)",
     "DeMiguel, Garlappi & Uppal (2009, Review of Financial Studies)", "(0.20*Value + 0.20*Quality + 0.20*Growth + 0.20*Momentum + 0.20*Risk) * 100", "stock_scientific_metrics", 30),
    ("practitioner_archetype", "Practitioner Stock Archetype Profile", "classification", "Practitioner", "Factor Archetype Taxonomy",
     "Lynch / GARP / Compounder / Value Trap Practitioner Taxonomy (ADR 0033)", "11-bucket decision tree over (Value, Quality, Growth, Momentum, Risk)", "stock_scientific_metrics", 31),
    ("academic_archetype", "Academic Factor-Interaction Profile", "classification", "Research-Backed", "Factor-Interaction Taxonomy",
     "Asness QMJ (2019) / Piotroski (2000) / Mohanram G-Score (2005) / Campbell Distress (2008)", "10-bucket peer-reviewed factor-interaction taxonomy", "stock_scientific_metrics", 32),
    ("multibagger_practitioner", "Practitioner Multibagger Screen (Lynch / PEG-Growth)", "multibagger_screen", "Practitioner", "Multibagger Discovery",
     "Lynch (1989) GARP + Terminal-Collapse-Guarded 3y CAGR (ADR 0033)", "size<=p70, CAGR>=15% (no >15% terminal drop), ROE/OM>=15%, D/E<=1, PEG<=2 or PE<=25, >200d SMA", "stock_scientific_metrics", 33),
    ("multibagger_academic", "Academic SQM Multibagger Screen (Size x Quality x 12-1 Mom)", "multibagger_screen", "Research-Backed", "Multibagger Discovery (SQM)",
     "Asness et al. (2018, JFE); Novy-Marx (2013); Sloan (1996); Jegadeesh & Titman (1993)", "size<=p70, ROE>=15%/OM>=12%, FCF>0 (Sloan accruals), D/E<=1, ret_12_1>0 & pos_52w>=0.50, 1/N 25/25/25/25 rank", "stock_scientific_metrics", 34),
)


def _stock_archetype(factors):
    v, qu, g, mo, r = [(x if x is not None else 0.5) for x in factors]
    if qu >= 0.70 and g >= 0.65 and r >= 0.55:
        return "Quality Compounder"
    if g >= 0.70 and v >= 0.55 and qu >= 0.55:
        return "GARP"
    if v >= 0.75 and qu >= 0.50:
        return "Deep Value"
    if mo >= 0.75 and g >= 0.55:
        return "Momentum Leader"
    if g >= 0.75 and v < 0.35:
        return "High-Growth Speculative"
    if r >= 0.70 and qu >= 0.65:
        return "Defensive Quality"
    if v >= 0.70 and mo >= 0.60 and qu < 0.45:
        return "Turnaround / Cyclical"
    if v >= 0.65 and qu < 0.35 and mo < 0.40:
        return "Value Trap"
    if qu < 0.35 and r < 0.35:
        return "High-Risk / Stressed"
    if (v + qu + g + mo + r) / 5.0 < 0.38 and max(v, qu, g, mo, r) < 0.65:
        return "Lagging / Weak"
    return "Balanced"


def _academic_archetype(factors):
    v, qu, g, mo, r = [(x if x is not None else 0.5) for x in factors]
    if qu >= 0.70 and r >= 0.60 and mo >= 0.50:
        return "QMJ + BAB Compounder (Asness 2019 / Frazzini 2014)"
    if v >= 0.70 and qu >= 0.60:
        return "Piotroski-Filtered Value (Piotroski 2000 / Novy-Marx 2013)"
    if v >= 0.65 and mo >= 0.65:
        return "Value × Momentum Crossover (Asness et al. 2013)"
    if g >= 0.70 and qu >= 0.60:
        return "Mohanram G-Score Growth (Mohanram 2005)"
    if mo >= 0.75 and r >= 0.45:
        return "Jegadeesh-Titman Momentum (Jegadeesh & Titman 1993)"
    if r >= 0.75 and qu >= 0.55 and v >= 0.45:
        return "Conservative Low-Vol (van Vliet 2018 / Ang 2006)"
    if g >= 0.65 and v < 0.35 and qu < 0.50:
        return "Unprofitable Glamour Risk (Lakonishok 1994)"
    if (v >= 0.65 and qu < 0.35 and mo < 0.40) or (qu < 0.35 and r < 0.35):
        return "Distress / Value-Trap Puzzle (Campbell et al. 2008)"
    if (v + qu + g + mo + r) / 5.0 < 0.38 and max(v, qu, g, mo, r) < 0.65:
        return "Bottom-Quintile Factor Lag"
    return "1/N Factor-Neutral (DeMiguel 2009)"


def _cur_prev(hist, d, key):
    series = hist.get(key, [])
    if len(series) >= 2:
        return series[-1][1], series[-2][1]
    if len(series) == 1:
        return series[-1][1], None
    return d.get(key), None


def scientific_row(symbol, today, d, hist, size_pct=0.5):
    """Compute daily per-symbol scientific accounting diagnostics & dual archetypes for `stock_scientific_metrics`."""
    ta, ta_p = _cur_prev(hist, d, "total_assets")
    gp, gp_p = _cur_prev(hist, d, "gross_profit")
    ni, ni_p = _cur_prev(hist, d, "net_income")
    ocf, _ = _cur_prev(hist, d, "ocf")
    fcf, _ = _cur_prev(hist, d, "fcf")
    rev, rev_p = _cur_prev(hist, d, "total_revenue")
    debt, debt_p = _cur_prev(hist, d, "total_debt")
    eq = d.get("shareholder_equity")
    ca, ca_p = _cur_prev(hist, d, "current_assets")
    cl, cl_p = _cur_prev(hist, d, "current_liabilities")
    sh, sh_p = _cur_prev(hist, d, "shares_outstanding")
    ebit, _ = _cur_prev(hist, d, "operating_income")
    cash_flow = ocf if ocf is not None else fcf

    gpa = (gp / ta) if (gp is not None and ta and ta > 0) else None
    accruals = ((ni - cash_flow) / ta) if (ni is not None and cash_flow is not None and ta and ta > 0) else None
    asset_g = (ta / ta_p - 1.0) if (ta is not None and ta_p and ta_p > 0) else None
    sh_iss = (sh / sh_p - 1.0) if (sh is not None and sh_p and sh_p > 0) else None

    f_pts, f_tested = 0, 0
    if ni is not None and ta and ta > 0:
        f_tested += 1
        f_pts += int((ni / ta) > 0)
    if cash_flow is not None:
        f_tested += 1
        f_pts += int(cash_flow > 0)
    if ni is not None and ni_p is not None and ta and ta > 0 and ta_p and ta_p > 0:
        f_tested += 1
        f_pts += int((ni / ta) > (ni_p / ta_p))
    if cash_flow is not None and ni is not None:
        f_tested += 1
        f_pts += int(cash_flow > ni)
    if debt is not None and debt_p is not None and ta and ta > 0 and ta_p and ta_p > 0:
        f_tested += 1
        f_pts += int((debt / ta) <= (debt_p / ta_p))
    if ca is not None and cl and cl > 0 and ca_p is not None and cl_p and cl_p > 0:
        f_tested += 1
        f_pts += int((ca / cl) > (ca_p / cl_p))
    if sh is not None and sh_p and sh_p > 0:
        f_tested += 1
        f_pts += int(sh <= sh_p * 1.005)
    if gp is not None and rev and rev > 0 and gp_p is not None and rev_p and rev_p > 0:
        f_tested += 1
        f_pts += int((gp / rev) > (gp_p / rev_p))
    if rev is not None and ta and ta > 0 and rev_p is not None and ta_p and ta_p > 0:
        f_tested += 1
        f_pts += int((rev / ta) > (rev_p / ta_p))
    f_score = f_pts if f_tested >= 4 else None
    f_test_out = f_tested if f_tested >= 4 else None

    z2, z_zone = None, None
    if ta and ta > 0 and ca is not None and cl is not None and eq is not None and ebit is not None:
        x1 = (ca - cl) / ta
        x2 = eq / ta
        x3 = ebit / ta
        x4 = (eq / debt) if (debt and debt > 0) else 3.0
        z2 = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * min(x4, 10.0)
        z_zone = "safe" if z2 > 2.60 else ("distress" if z2 < 1.10 else "grey")

    prac_arch = acad_arch = None
    prac_comp = acad_comp = None
    sc = d.get("scores")
    if sc and len(sc) >= 6:
        prac_comp = sc[0]
        factors = sc[1:6]
        prac_arch = _stock_archetype(factors)
        acad_arch = _academic_archetype(factors)
        vals = [(x if x is not None else 0.5) for x in factors]
        acad_comp = (sum(vals) / len(vals)) * 100.0

    # Multibagger Practitioner & Academic (SQM) gate checks
    mb_prac, mb_acad = None, None
    sec = d.get("sector") or ""
    fin = sec in ("Financial Services", "Financials")
    de = (debt / eq) if (debt is not None and eq and eq > 0) else None
    roe = d.get("roe")
    om = d.get("operating_margin")
    pe = d.get("pe")
    mcap = d.get("market_cap")
    sma200 = d.get("sma200_ratio")
    r12 = d.get("ret_12m")
    r1 = d.get("ret_1m")
    r12_1 = d.get("ret_12_1")
    if r12_1 is None and r12 is not None and r1 is not None and r1 > -0.95:
        r12_1 = (1.0 + r12) / (1.0 + r1) - 1.0
    elif r12_1 is None:
        r12_1 = r12
    pos52 = d.get("pos_52w")
    rev_g = cagr(hist.get("total_revenue", []), min_periods=2)
    eps_g = cagr(hist.get("eps", []), min_periods=2)
    ni_g = cagr(hist.get("net_income", []), min_periods=2)
    earn_g = eps_g if eps_g is not None else ni_g
    growth = max(v for v in (rev_g, earn_g) if v is not None) if (rev_g is not None or earn_g is not None) else None
    peg = (pe / (earn_g * 100.0)) if (pe and pe > 0 and earn_g and earn_g > 0) else None

    if (size_pct <= 0.70 and growth is not None and growth >= 0.15
            and ((roe is not None and roe >= 0.15) or (om is not None and om >= 0.15))
            and (fin or (de is not None and 0 <= de <= 1.0))
            and ((peg is not None and 0 < peg <= 2.0) or (pe is not None and 0 < pe <= 25.0))
            and (sma200 is None or sma200 >= 1.0) and (r12 is None or r12 > 0)):
        mb_prac = round(min(100.0, growth * 200.0 + (roe or 0.15) * 100.0), 1)

    fcf_y = (cash_flow / mcap) if (cash_flow is not None and mcap and mcap > 0) else None
    if (size_pct <= 0.70
            and ((roe is not None and roe >= 0.15) or (om is not None and om >= 0.12))
            and (cash_flow is not None and cash_flow > 0)
            and (fin or (de is not None and 0 <= de <= 1.0))
            and (r12_1 is not None and r12_1 > 0)
            and (pos52 is None or pos52 >= 0.50)
            and (sma200 is None or sma200 >= 1.0)
            and ((fcf_y is not None and fcf_y >= 0.015) or (pe is not None and 0 < pe <= 40.0))):
        mb_acad = round(min(100.0, ((roe or 0.15) * 100.0 + (r12_1 or 0.10) * 100.0 + (1.0 - size_pct) * 50.0)), 1)

    mb_lens = ("both" if (mb_prac is not None and mb_acad is not None)
               else "practitioner" if mb_prac is not None
               else "academic" if mb_acad is not None else None)

    return (symbol, today, d["universe"], d.get("sector"),
            gpa, accruals, asset_g, sh_iss, f_score, f_test_out, z2, z_zone,
            prac_arch, acad_arch, prac_comp, acad_comp, mb_prac, mb_acad, mb_lens)


def ensure_schema_and_catalog(cur):
    """Ensure key 25 tables/columns exist and upsert `VALUATION_CATALOG` into `valuation_methods`."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS valuation_methods (
          method_key    TEXT PRIMARY KEY,
          method_name   TEXT NOT NULL,
          category      TEXT NOT NULL DEFAULT 'valuation',
          lens          TEXT NOT NULL,
          sub_school    TEXT NOT NULL,
          source_ref    TEXT NOT NULL,
          formula       TEXT NOT NULL,
          target_table  TEXT NOT NULL DEFAULT 'stock_valuations',
          display_order SMALLINT NOT NULL DEFAULT 0
        )
    """)
    cur.executemany("""
        INSERT INTO valuation_methods (
          method_key, method_name, category, lens, sub_school,
          source_ref, formula, target_table, display_order
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (method_key) DO UPDATE SET
          method_name   = EXCLUDED.method_name,
          category      = EXCLUDED.category,
          lens          = EXCLUDED.lens,
          sub_school    = EXCLUDED.sub_school,
          source_ref    = EXCLUDED.source_ref,
          formula       = EXCLUDED.formula,
          target_table  = EXCLUDED.target_table,
          display_order = EXCLUDED.display_order
    """, VALUATION_CATALOG)
    for col, ctype in (
        ("ev_ebitda_peer", "DOUBLE PRECISION"),
        ("owner_earnings", "DOUBLE PRECISION"),
        ("easton_mpeg", "DOUBLE PRECISION"),
        ("intangible_ri", "DOUBLE PRECISION"),
        ("total_payout_ddm", "DOUBLE PRECISION"),
        ("practitioner_fair_value", "DOUBLE PRECISION"),
        ("practitioner_methods", "INT"),
        ("academic_fair_value", "DOUBLE PRECISION"),
        ("academic_methods", "INT"),
        ("all_fair_value", "DOUBLE PRECISION"),
        ("all_methods", "INT"),
    ):
        cur.execute(f"ALTER TABLE stock_valuations ADD COLUMN IF NOT EXISTS {col} {ctype}")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_scientific_metrics (
          symbol                 TEXT NOT NULL,
          ts                     TIMESTAMPTZ NOT NULL,
          universe               TEXT NOT NULL,
          sector                 TEXT,
          gpa                    DOUBLE PRECISION,
          operating_accruals     DOUBLE PRECISION,
          asset_growth_cma       DOUBLE PRECISION,
          net_share_issuance     DOUBLE PRECISION,
          piotroski_f_score      SMALLINT,
          piotroski_f_tested     SMALLINT,
          altman_z2              DOUBLE PRECISION,
          altman_zone            TEXT,
          practitioner_archetype TEXT,
          academic_archetype     TEXT,
          practitioner_composite DOUBLE PRECISION,
          academic_composite     DOUBLE PRECISION,
          mb_practitioner_score  DOUBLE PRECISION,
          mb_academic_score      DOUBLE PRECISION,
          mb_lens                TEXT,
          PRIMARY KEY (symbol, ts)
        )
    """)
    for col, ctype in (
        ("mb_practitioner_score", "DOUBLE PRECISION"),
        ("mb_academic_score", "DOUBLE PRECISION"),
        ("mb_lens", "TEXT"),
    ):
        cur.execute(f"ALTER TABLE stock_scientific_metrics ADD COLUMN IF NOT EXISTS {col} {ctype}")
    for col, ctype in (
        ("median_practitioner_upside_pct", "DOUBLE PRECISION"),
        ("median_academic_upside_pct", "DOUBLE PRECISION"),
        ("median_gpa", "DOUBLE PRECISION"),
        ("median_f_score", "DOUBLE PRECISION"),
    ):
        cur.execute(f"ALTER TABLE sector_metrics ADD COLUMN IF NOT EXISTS {col} {ctype}")


def main() -> int:
    with job_run("stock-valuation") as run:
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                   microsecond=0)
        conn = connect()
        try:
            with conn.cursor() as cur:
                rates = discount_rates(cur)
                styles, weights = style_weights(cur)
                data = load_inputs(cur)
                hist = load_history(cur)
            medians = sector_medians(data)
            # Compute within-universe market_cap percentile for multibagger size gate
            uni_caps = {}
            for s, d_ in data.items():
                if d_.get("market_cap") and d_["market_cap"] > 0:
                    uni_caps.setdefault(d_["universe"], []).append((d_["market_cap"], s))
            size_pcts = {}
            for _u, pairs_ in uni_caps.items():
                pairs_.sort()
                n_ = max(len(pairs_) - 1, 1)
                for idx_, (_c, sym_) in enumerate(pairs_):
                    size_pcts[sym_] = idx_ / n_

            rows, sci_rows, skipped, upsides = [], [], 0, {}
            prac_ups, acad_ups, gpa_map, f_map = {}, {}, {}, {}
            for symbol, d in sorted(data.items()):
                sym_hist = hist.get(symbol, {})
                out = value_one(d, sym_hist, medians, styles,
                                weights, rates)
                if out is None:
                    skipped += 1
                    continue
                close_px = d["close"]
                upsides[symbol] = out["upside_pct"]
                if out["practitioner_fair_value"] and close_px:
                    prac_ups[symbol] = (out["practitioner_fair_value"] / close_px - 1.0) * 100.0
                if out["academic_fair_value"] and close_px:
                    acad_ups[symbol] = (out["academic_fair_value"] / close_px - 1.0) * 100.0
                rows.append((symbol, today, d["universe"], d["close"],
                             *[out[c] for c in METHOD_COLS],
                             out["fair_value"], out["upside_pct"], out["methods"],
                             out["discount_rate"], out["growth_used"],
                             *[out[c] for c in EXTENDED_COLS],
                             out["practitioner_fair_value"], out["practitioner_methods"],
                             out["academic_fair_value"], out["academic_methods"],
                             out["all_fair_value"], out["all_methods"]))
                srow = scientific_row(symbol, today, d, sym_hist, size_pcts.get(symbol, 0.5))
                sci_rows.append(srow)
                if srow[4] is not None:
                    gpa_map[symbol] = srow[4]
                if srow[8] is not None:
                    f_map[symbol] = float(srow[8])
            sectors = sector_rows(data, medians, upsides, today)
            sec_groups = {}
            for s, d_ in data.items():
                sec_groups.setdefault((d_["universe"], d_["sector"]), []).append(s)
            sec_updates = []
            for (uni, sec), syms in sorted(sec_groups.items()):
                pu = [prac_ups[s] for s in syms if s in prac_ups]
                au = [acad_ups[s] for s in syms if s in acad_ups]
                gp_l = [gpa_map[s] for s in syms if s in gpa_map]
                fs_l = [f_map[s] for s in syms if s in f_map]
                sec_updates.append((
                    statistics.median(pu) if len(pu) >= MEDIAN_MIN_N else None,
                    statistics.median(au) if len(au) >= MEDIAN_MIN_N else None,
                    statistics.median(gp_l) if len(gp_l) >= MEDIAN_MIN_N else None,
                    statistics.median(fs_l) if len(fs_l) >= MEDIAN_MIN_N else None,
                    uni, sec, today,
                ))
            with conn.transaction():
                with conn.cursor() as cur:
                    ensure_schema_and_catalog(cur)
                    cur.execute("DELETE FROM stock_valuations WHERE ts = %s", (today,))
                    cur.executemany(
                        "INSERT INTO stock_valuations (symbol, ts, universe, close, "
                        "graham, peer_multiple, fcf_yield, dcf, ddm, epv, "
                        "residual_income, peg_lynch, damodaran_fcff, graham_growth, "
                        "fair_value, upside_pct, methods, "
                        "discount_rate, growth_used, "
                        "ev_ebitda_peer, owner_earnings, easton_mpeg, intangible_ri, "
                        "total_payout_ddm, practitioner_fair_value, practitioner_methods, "
                        "academic_fair_value, academic_methods, all_fair_value, all_methods) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        rows)
                    cur.execute("DELETE FROM stock_scientific_metrics WHERE ts = %s", (today,))
                    cur.executemany(
                        "INSERT INTO stock_scientific_metrics (symbol, ts, universe, sector, "
                        "gpa, operating_accruals, asset_growth_cma, net_share_issuance, "
                        "piotroski_f_score, piotroski_f_tested, altman_z2, altman_zone, "
                        "practitioner_archetype, academic_archetype, "
                        "practitioner_composite, academic_composite, "
                        "mb_practitioner_score, mb_academic_score, mb_lens) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        sci_rows)
                    cur.execute("DELETE FROM sector_metrics WHERE ts = %s", (today,))
                    cur.executemany(
                        "INSERT INTO sector_metrics (universe, sector, ts, members, "
                        "median_pe, median_pb, median_ev_ebitda, median_roe, "
                        "median_op_margin, avg_ret_6m, avg_ret_12m, median_upside_pct) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", sectors)
                    cur.executemany(
                        "UPDATE sector_metrics SET median_practitioner_upside_pct = %s, "
                        "median_academic_upside_pct = %s, median_gpa = %s, "
                        "median_f_score = %s WHERE universe = %s AND sector = %s AND ts = %s",
                        sec_updates)
            run.rows = len(rows)
            # fair_value sits right after the 10 core method columns.
            fair_idx = 4 + len(METHOD_COLS)
            valued = sum(1 for r in rows if r[fair_idx] is not None)
        finally:
            conn.close()
        print(f"VALUATION-OK {len(rows)} symbols ({valued} with a fair value, "
              f"{skipped} without a recent close) + {len(sci_rows)} scientific rows "
              f"+ {len(sectors)} sector rows",
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
