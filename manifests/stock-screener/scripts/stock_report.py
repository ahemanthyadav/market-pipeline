"""Shortlist reports (Phase G) — deterministic templates + existing FinBERT.

Per plan §8: for rank <= SHORTLIST_SIZE per universe, render a markdown report
(valuation vs universe medians, quality block, price block, news-sentiment
block joined from news_sentiment within 3 days) into stock_reports, plus one
combined '__digest__' row. No LLM in v1 — pluggable hook documented in the plan.

v8: the shortlist is each symbol's LATEST scores row (today's run) — the v1-v7
query had no ts filter and reported the union of every historical top-25.

Connection discipline (house pattern, 2026-10-04): autocommit reads, one atomic
transaction per report write, reconnect-and-retry once on a dropped
connection. The earlier shared-cursor/implicit-transaction version died
deterministically ("connection is closed") mid-loop — this layout matches the
proven prices/fundamentals/metrics fetchers.
"""
import os
import re
import sys
from datetime import datetime, timezone

import psycopg

SHORTLIST = int(os.environ.get("SHORTLIST_SIZE", "25"))


def fmt(v, suffix=""):
    try:
        return f"{v:,.2f}{suffix}" if isinstance(v, (int, float)) else "n/a"
    except Exception:  # noqa: BLE001
        return "n/a"


def stock_archetype(factors):
    """Classify a stock's factor profile (value, quality, growth, momentum, risk) in [0,1]."""
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


def stock_drivers(factors, top_k=3):
    """Return top factor drivers (percentile >= 0.55) formatted for human display."""
    names = ("Value", "Quality", "Growth", "Momentum", "Low-Risk")
    scored = [(val, name) for name, val in zip(names, factors) if val is not None and val >= 0.55]
    scored.sort(reverse=True)
    if not scored:
        return "none ≥ 55th pct"
    return ", ".join(f"{name} ({val * 100:.0f}th)" for val, name in scored[:top_k])


def academic_composite(factors):
    """DeMiguel, Garlappi & Uppal (2009) 1/N equal-weighted composite (20% each, 0-100 scale)."""
    vals = [(x if x is not None else 0.5) for x in factors]
    return (sum(vals) / len(vals)) * 100.0 if vals else 50.0


def academic_archetype(factors):
    """Peer-reviewed factor-interaction classification (FF5 2015 / Asness QMJ 2019 / Piotroski 2000 / Mohanram 2005)."""
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


def archetype_comparison(factors, prac_comp=None):
    """Explain the difference between the Practitioner and Academic readings for `factors`."""
    v, qu, g, mo, r = [(x if x is not None else 0.5) for x in factors]
    prac = stock_archetype(factors)
    acad = academic_archetype(factors)
    eq_comp = academic_composite(factors)
    w_comp = prac_comp if prac_comp is not None else (0.25 * v + 0.25 * qu + 0.20 * g + 0.20 * mo + 0.10 * r) * 100.0
    gap = eq_comp - w_comp
    if g >= 0.65 and qu < 0.50:
        return ("Practitioner rewards headline growth (20% weight); Academic discounts "
                "unconfirmed growth without high Profitability (RMW, Fama-French 2015)")
    if v >= 0.70 and qu >= 0.60:
        return ("Both lenses agree: cheap multiples are backed by high Profitability "
                "(Novy-Marx 2013), separating true value from value traps")
    if r >= 0.70 and gap >= 1.5:
        return (f"Academic 1/N weights Low-Vol 20% vs Practitioner's 10% (+{gap:.1f} pts; "
                "Ang et al. 2006 / Betting Against Beta)")
    if qu >= 0.70 and r >= 0.60:
        return ("Confirmed by both: Practitioner sees growth/quality; Academic attributes "
                "edge to Quality Minus Junk × Low-Vol (Asness 2019)")
    return (f"Practitioner ({prac}) weights Value/Quality 25% each and Risk 10%; "
            f"Academic ({acad.split(' (')[0]}) equal-weights all 5 pillars at 20% (1/N Δ {gap:+.1f} pts)")


def connect():
    conn = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    conn.autocommit = True
    return conn


def q1(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchone()


def news_block(cur, sym, name):
    # The ticker matches only ticker_relevance (where it IS a symbol) and the
    # company name only headlines: tickers like ALL/IT/ON are English words,
    # so ILIKE '%ALL%' — and even \mALL\M against headlines — pulled in
    # "falls"/"rally"/"All eyes" noise and skewed the average (2026-10-26).
    base = sym.replace(".NS", "").replace("-", ".")
    first = name.split()[0] if name else ""
    pat_base = rf"\m{re.escape(base)}\M"
    pat_name = rf"\m{re.escape(first)}\M" if first else pat_base
    cur.execute(
        """SELECT headline, sentiment_score FROM news_sentiment
           WHERE ts >= now() - interval '3 days'
             AND (ticker_relevance ~* %s OR headline ~* %s)
           ORDER BY ts DESC LIMIT 10""",
        (pat_base, pat_name))
    rows = cur.fetchall()
    if not rows:
        return "_No matching headlines in the last 3 days._\n", 0.0
    avg = sum(r[1] or 0 for r in rows) / len(rows)
    pos = max(rows, key=lambda r: r[1] or -9)
    neg = min(rows, key=lambda r: r[1] or 9)
    lines = [f"Recent sentiment (FinBERT, 3d): **{avg:+.2f}** over {len(rows)} "
             f"headlines.\n"]
    lines.append(f"- Most positive ({pos[1]:+.2f}): {str(pos[0])[:140]}")
    lines.append(f"- Most negative ({neg[1]:+.2f}): {str(neg[0])[:140]}\n")
    return "\n".join(lines), avg


def render_report(cur, row, medians):
    (sym, rank, comp, vs, qs, gs, ms, rs, cov, name) = row
    med_v, med_q, med_c = medians
    cur.execute("SELECT metric, value FROM stock_metrics_daily WHERE symbol=%s "
                "ORDER BY ts DESC LIMIT 20", (sym,))
    mets, seen = {}, set()
    for metric, val in cur.fetchall():
        if metric not in seen:
            mets[metric] = val
            seen.add(metric)
    # flow metrics: full-year (yfinance) rows only — EDGAR 10-Q rows are YTD (DQ-2)
    cur.execute("SELECT DISTINCT ON (metric) metric, value FROM stock_fundamentals "
                "WHERE symbol=%s AND (source='yfinance' OR metric NOT IN "
                "('total_revenue','net_income','operating_income','eps','fcf')) "
                "ORDER BY metric, ts DESC", (sym,))
    fund = dict(cur.fetchall())
    # ADR 0026: deterministic fair value + the sector lens (schemas 15/16).
    cur.execute("SELECT fair_value, upside_pct, methods, graham, peer_multiple, "
                "fcf_yield, dcf, ddm, epv, residual_income, peg_lynch, "
                "damodaran_fcff, graham_growth, discount_rate, growth_used "
                "FROM stock_valuations WHERE symbol=%s "
                "AND ts >= now() - interval '7 days' ORDER BY ts DESC LIMIT 1", (sym,))
    val = cur.fetchone()
    cur.execute("SELECT c.sector, coalesce(st.style,'default'), st.note "
                "FROM universe_constituents c LEFT JOIN sector_styles st "
                "ON st.sector = c.sector WHERE c.symbol=%s LIMIT 1", (sym,))
    sec = cur.fetchone() or (None, "default", None)
    cur.execute("SELECT metric, weight, note FROM metric_relevance WHERE style=%s "
                "ORDER BY weight DESC, metric", (sec[1],))
    rel = cur.fetchall()
    cur.execute("SELECT rank, composite FROM stock_scores WHERE symbol=%s "
                "AND ts >= now() - interval '30 days' ORDER BY ts DESC OFFSET 1 LIMIT 1",
                (sym,))
    prior = cur.fetchone()
    nb, sent = news_block(cur, sym, name)
    md = []
    md.append(f"# {name} ({sym}) — shortlist\n")
    if sec[0]:
        md.append(f"Sector: **{sec[0]}**" + (f" — _{sec[2]}_" if sec[2] else ""))
    md.append(f"**Rank {rank}** · composite **{fmt(comp)}** "
              f"(universe median {fmt(med_c)}) · coverage {int((cov or 0) * 100)}%")
    arch = stock_archetype((vs, qs, gs, ms, rs))
    acad_arch = academic_archetype((vs, qs, gs, ms, rs))
    acad_comp = academic_composite((vs, qs, gs, ms, rs))
    arch_diff = archetype_comparison((vs, qs, gs, ms, rs), comp)
    drivers = stock_drivers((vs, qs, gs, ms, rs))
    md.append(f"Profile (Practitioner): **{arch}** (25/25/20/20/10 composite **{fmt(comp)}**) · top drivers: {drivers}")
    md.append(f"Profile (Academic FF5/QMJ/1/N): **{acad_arch}** (equal-weight 1/N composite **{fmt(acad_comp)}**) — _{arch_diff}_")
    if prior and prior[0] is not None and rank is not None:
        r_delta = prior[0] - rank
        c_delta = (comp - prior[1]) if (comp is not None and prior[1] is not None) else 0.0
        md.append(f"Delta vs prior run: rank **{r_delta:+d}** · composite **{c_delta:+.2f}**")
    md.append(f"Factors: value {fmt(vs)} (med {fmt(med_v)}) · quality {fmt(qs)} "
              f"(med {fmt(med_q)}) · growth {fmt(gs)} · momentum {fmt(ms)} · "
              f"risk {fmt(rs)}\n")
    md.append("## Valuation")
    md.append(f"- P/E {fmt(fund.get('pe'))} · P/B {fmt(fund.get('pb'))} · "
              f"EV/EBITDA {fmt(fund.get('ev_ebitda'))}")
    mcap = fund.get("market_cap")
    md.append(f"- Market cap: {fmt(mcap / 1e9, suffix='B') if mcap else 'n/a'}")
    if val and val[0]:
        md.append(f"- **Fair value {fmt(val[0])}** ({val[1]:+.0f}% vs close, "
                  f"median of {val[2]} methods — ADR 0026, not advice)")
        # Methods grouped by philosophy (ADR 0030 §grouping) + Practitioner vs Research-Backed lenses.
        by_name = dict(zip(("Graham", "Peer multiple", "FCF yield", "DCF",
                            "DDM", "EPV", "Residual income", "PEG",
                            "Damodaran FCFF", "Graham growth"), val[3:13]))
        r_rate = val[13] or 0.095
        close_px = mets.get("close")
        shares_imp = (mcap / close_px) if (mcap and close_px and close_px > 0) else None
        debt_v = fund.get("total_debt") or 0.0
        eq_v = fund.get("shareholder_equity")
        fcf_cur = fund.get("fcf")
        if fcf_cur and fcf_cur > 0 and shares_imp and r_rate > 0.025:
            oe_eq = fcf_cur * 1.025 / (r_rate - 0.025) - debt_v
            if oe_eq > 0:
                by_name["Owner earnings"] = oe_eq / shares_imp
        if by_name.get("PEG") and r_rate > 0:
            # Easton (2004) / Ohlson-Juettner (2005) MPEG scales PEG=1 by (0.10 / r)^2
            by_name["Easton MPEG"] = by_name["PEG"] / ((r_rate * 10.0) ** 2)
        gp_cur = fund.get("gross_profit")
        ebit_cur = fund.get("operating_income")
        roe_cur = fund.get("roe")
        if (gp_cur and ebit_cur is not None and gp_cur > ebit_cur and eq_v and eq_v > 0
                and shares_imp and roe_cur and r_rate > 0.025):
            ovh = gp_cur - ebit_cur
            adj_eq = eq_v + 1.5 * ovh
            adj_roe = (roe_cur * eq_v + 0.10 * ovh) / adj_eq
            if adj_roe > r_rate:
                adj_jpb = min((adj_roe - 0.025) / (r_rate - 0.025), 8.0)
                by_name["Intangible RI"] = adj_jpb * (adj_eq / shares_imp)

        prac_keys = ("Graham", "Graham growth", "PEG", "Peer multiple", "FCF yield", "DCF", "Owner earnings")
        acad_keys = ("Residual income", "Intangible RI", "Easton MPEG", "Damodaran FCFF", "EPV", "DDM")
        prac_got = sorted(by_name[k] for k in prac_keys if by_name.get(k))
        acad_got = sorted(by_name[k] for k in acad_keys if by_name.get(k))
        prac_med = prac_got[len(prac_got) // 2] if prac_got else None
        acad_med = acad_got[len(acad_got) // 2] if acad_got else None
        if prac_med is not None or acad_med is not None:
            md.append(f"- **Lens comparison**: Practitioner median **{fmt(prac_med)}** ({len(prac_got)} methods) "
                      f"vs Research-Backed (Academic) median **{fmt(acad_med)}** ({len(acad_got)} methods)")

        for school, members in (
                ("Practitioner — Conservative & GARP (Graham / Lynch)",
                 ("Graham", "Graham growth", "PEG")),
                ("Practitioner — Relative & Cash-Flow (Comps / DCF / Buffett)",
                 ("Peer multiple", "FCF yield", "DCF", "Owner earnings")),
                ("Research-Backed — Clean-Surplus & Intangible Accounting (Ohlson 1995 / Peters-Taylor 2017)",
                 ("Residual income", "Intangible RI")),
                ("Research-Backed — Abnormal Earnings Growth & Corporate Finance (Easton 2004 / Damodaran / Greenwald)",
                 ("Easton MPEG", "Damodaran FCFF", "EPV", "DDM"))):
            got = [(n, by_name[n]) for n in members if by_name.get(n)]
            if got:
                md.append(f"- {school}: " + " · ".join(
                    f"{n} {fmt(v)}" for n, v in got))
        gtxt = (f"{val[14] * 100:.1f}% rev-CAGR [0–15%]" if val[14] is not None
                else "n/a")
        md.append(f"- _Assumptions: r = {val[13] * 100:.2f}% (10y + 4.5% ERP) · "
                  f"growth {gtxt} · terminal 2.5% · tax 25% · kd = 10y+2% (synthetic) · "
                  f"Graham Y = 10y+1% · annual-filing EPS/BVPS/EBIT · debt netted, "
                  f"no cash credit_")
    if rel:
        md.append("## What matters in this sector")
        for metric, weight, note in rel:
            if weight >= 3:
                md.append(f"- **{metric}**: {note}")
        for metric, weight, note in rel:
            if weight == 0:
                md.append(f"- ~~{metric}~~ (misleading here): {note}")
    md.append("## Quality & growth")
    md.append(f"- ROE {fmt(fund.get('roe'))} · operating margin "
              f"{fmt(fund.get('operating_margin'))}")
    divs = fund.get("dividends_paid")
    ni = fund.get("net_income")
    fcf_v = fund.get("fcf")
    if divs and divs > 0:
        payout = f"{divs / ni * 100:.1f}%" if ni and ni > 0 else "n/a"
        fcf_cov = f"{fcf_v / divs:.2f}x" if fcf_v is not None else "n/a"
        div_y = f"{divs / mcap * 100:.2f}%" if mcap and mcap > 0 else "n/a"
        md.append(f"- Dividend sustainability: yield {div_y} · payout {payout} · "
                  f"FCF coverage {fcf_cov}")
    ta = fund.get("total_assets")
    gp = fund.get("gross_profit")
    ocf_v = fund.get("ocf") if fund.get("ocf") is not None else fcf_v
    if ta and ta > 0:
        gpa_str = f"{gp / ta * 100:.1f}%" if gp is not None else "n/a"
        acc_str = f"{(ni - ocf_v) / ta * 100:+.1f}%" if (ni is not None and ocf_v is not None) else "n/a"
        md.append(f"- Scientific accounting (Novy-Marx 2013 / Sloan 1996): "
                  f"Gross profitability (GP/A) **{gpa_str}** · Operating accruals **{acc_str}**")
    md.append("## Price")
    md.append(f"- Close {fmt(mets.get('close'))} · 6m return {fmt(mets.get('ret_6m'))} "
              f"· 12m return {fmt(mets.get('ret_12m'))} · 12-1 momentum (Jegadeesh-Titman) {fmt(mets.get('ret_12_1'))}")
    md.append(f"- 52w position {fmt(mets.get('pos_52w'))} · near 52w high (George-Hwang) {fmt(mets.get('near_high'))} · ann. vol "
              f"{fmt(mets.get('vol_180d'))} · max drawdown (1y) "
              f"{fmt(mets.get('max_dd_252d'))}")
    md.append("## News (AI: FinBERT)")
    md.append(nb)
    md.append("---\n_Research/screening output — not investment advice. Data as "
              "ingested; verify before acting._")
    return "\n".join(md), sent


def main() -> int:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                microsecond=0)
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT universe FROM stock_scores ORDER BY 1")
        universes = [r[0] for r in cur.fetchall()]
    n_reports, digest_lines = 0, []
    for universe in universes:
        with conn.cursor() as cur:
            # v8: restrict to each symbol's LATEST scores row (= today's run).
            # Scores carry the per-symbol metric ts, which can differ across
            # symbols (stale/fresh bars), so max(ts) alone misses rows; the old
            # unfiltered query unioned every historical top-25 (51 reports
            # instead of 25 per universe). DISTINCT ON gives the current run.
            cur.execute(
                """WITH latest AS (
                       SELECT DISTINCT ON (symbol) s.*
                       FROM stock_scores s WHERE s.universe=%s
                       ORDER BY s.symbol, s.ts DESC)
                   SELECT l.symbol, l.rank, l.composite, l.value_score,
                          l.quality_score, l.growth_score, l.momentum_score,
                          l.risk_score, l.coverage,
                          coalesce(c.name, l.symbol) AS name
                   FROM latest l
                   LEFT JOIN universe_constituents c
                     ON c.symbol = l.symbol AND c.universe = l.universe
                   WHERE l.rank IS NOT NULL AND l.rank <= %s
                   ORDER BY l.rank""", (universe, SHORTLIST))
            shortlist = cur.fetchall()
        if not shortlist:
            print(f"{universe}: no shortlist rows", flush=True)
            continue
        with conn.cursor() as cur:
            cur.execute(
                """SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY value_score),
                          percentile_cont(0.5) WITHIN GROUP (ORDER BY quality_score),
                          percentile_cont(0.5) WITHIN GROUP (ORDER BY composite)
                   FROM (SELECT DISTINCT ON (symbol) s.*
                         FROM stock_scores s WHERE s.universe=%s
                         ORDER BY s.symbol, s.ts DESC) l""",
                (universe,))
            medians = cur.fetchone()
        print(f"{universe}: {len(shortlist)} shortlisted", flush=True)
        for row in shortlist:
            sym = row[0]
            try:
                with conn.cursor() as cur:
                    report, _ = render_report(cur, row, medians)
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM stock_reports WHERE symbol=%s AND ts=%s",
                                    (sym, today))
                        cur.execute("INSERT INTO stock_reports (symbol, ts, report_md) "
                                    "VALUES (%s,%s,%s)", (sym, today, report))
                n_reports += 1
                digest_lines.append(f"| {universe} | {row[1]} | {row[9]} ({sym}) | "
                                    f"{fmt(row[2])} | V {fmt(row[3])} "
                                    f"Q {fmt(row[4])} |")
                print(f"  report ok: {sym} (rank {row[1]})", flush=True)
            except psycopg.OperationalError as exc:
                print(f"WARN {sym} conn dropped ({exc}); reconnecting, retrying",
                      flush=True)
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
                conn = connect()
                try:
                    with conn.cursor() as cur:
                        report, _ = render_report(cur, row, medians)
                    with conn.transaction():
                        with conn.cursor() as cur:
                            cur.execute("DELETE FROM stock_reports WHERE symbol=%s "
                                        "AND ts=%s", (sym, today))
                            cur.execute("INSERT INTO stock_reports (symbol, ts, "
                                        "report_md) VALUES (%s,%s,%s)",
                                        (sym, today, report))
                    n_reports += 1
                    print(f"  report ok after retry: {sym}", flush=True)
                except Exception as exc2:  # noqa: BLE001
                    print(f"WARN {sym} failed after reconnect: {exc2}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"WARN {sym} failed: {exc}", flush=True)
    if digest_lines:
        digest = ("# Daily shortlist digest — " + today.strftime("%Y-%m-%d")
                  + "\n\n| Universe | Rank | Stock | Composite | Factors |\n"
                  "|---|---|---|---|---|\n" + "\n".join(digest_lines))
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("DELETE FROM stock_reports WHERE symbol='__digest__' "
                            "AND ts=%s", (today,))
                cur.execute("INSERT INTO stock_reports (symbol, ts, report_md) "
                            "VALUES ('__digest__', %s, %s)", (today, digest))
    # Sector analysis per universe (ADR 0026): today's sector_metrics rendered
    # as one '__sectors__' report row, sorted cheapest-by-median-upside first.
    with conn.cursor() as cur:
        cur.execute(
            """SELECT m.universe, m.sector, m.members, m.median_pe, m.median_pb,
                      m.median_ev_ebitda, m.avg_ret_6m, m.avg_ret_12m,
                      m.median_upside_pct, st.style, st.note
               FROM sector_metrics m LEFT JOIN sector_styles st USING (sector)
               WHERE m.ts >= now() - interval '7 days'
                 AND m.ts = (SELECT max(ts) FROM sector_metrics
                             WHERE ts >= now() - interval '7 days')
               ORDER BY m.universe, m.median_upside_pct DESC NULLS LAST""")
        sector_rows = cur.fetchall()
    if sector_rows:
        md = ["# Sector analysis — " + today.strftime("%Y-%m-%d"),
              "", "_Medians over members; upside = median of each member's "
              "fair-value gap (ADR 0026). Not advice._", ""]
        last_u = None
        for (u, sector, n, pe, pb, ev, r6, r12, up, _style, note) in sector_rows:
            if u != last_u:
                md += [f"## {u}", "", "| Sector | n | P/E | P/B | EV/EBITDA | "
                       "6m | 12m | Upside | Lens |", "|---|---|---|---|---|---|---|---|---|"]
                last_u = u
            md.append(f"| {sector} | {n} | {fmt(pe)} | {fmt(pb)} | {fmt(ev)} | "
                      f"{fmt(r6)} | {fmt(r12)} | "
                      f"{f'{up:+.0f}%' if up is not None else 'n/a'} | "
                      f"{note or ''} |")
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("DELETE FROM stock_reports WHERE symbol='__sectors__' "
                            "AND ts=%s", (today,))
                cur.execute("INSERT INTO stock_reports (symbol, ts, report_md) "
                            "VALUES ('__sectors__', %s, %s)", (today, "\n".join(md)))
        print(f"sector analysis: {len(sector_rows)} sector rows", flush=True)
    conn.close()
    print(f"REPORT-OK reports={n_reports} digest={'yes' if digest_lines else 'no'}",
          flush=True)
    return 0 if n_reports else 1


if __name__ == "__main__":
    sys.exit(main())
