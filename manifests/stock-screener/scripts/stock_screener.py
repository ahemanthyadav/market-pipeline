"""Balanced multi-factor screener (Phase F, job 2 of 2) — plan §7 formulas.

Per universe, per day: winsorize (1/99) → cross-sectional percentile ranks →
factor scores → weighted composite → rank. Eligibility: coverage ≥ 0.60 and
20d dollar volume ≥ universe p10. Point-in-time: fundamentals only as of
latest metric ts (filing_published_at respected). Writes stock_scores +
screening_runs. Factor components (v1): VALUE(pe,pb,ev_ebitda,fcf_yield)
QUALITY(roe,operating_margin,−de) GROWTH(rev_cagr_3y,eps_growth_yoy)
MOMENTUM(ret_6m,ret_12m,pos_52w,sma200_ratio) RISK(−vol_180d,−max_dd_252d).

Connection discipline (house pattern, lesson 2026-10-04; applied here in v8
after the SP500 leg died at the NIFTY→SP500 boundary 5 nights running
[2026-10-05..09]: "psycopg.OperationalError: the connection is closed"):
reads in autocommit (no idle-in-transaction window across the long EDGAR/
metrics pulls), one atomic conn.transaction() per universe's writes, and a
reconnect-and-retry of the universe leg on a dropped connection.
"""
import os
import sys
from datetime import datetime, timezone

import pandas as pd
import psycopg

W = {"value": 0.25, "quality": 0.25, "growth": 0.20, "momentum": 0.20, "risk": 0.10}
NEUTRAL = 0.5
FLOW_METRICS = ("total_revenue", "net_income", "operating_income", "eps", "fcf")


def connect():
    conn = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    conn.autocommit = True  # reads never hold a transaction open
    return conn


def pct_rank(s: pd.Series, negate=False) -> pd.Series:
    """Winsorize at 1/99 then percentile rank in [0,1]; higher = better."""
    v = -s if negate else s
    v = v.astype(float)
    if v.notna().sum() < 3:
        return pd.Series(NEUTRAL, index=s.index)
    lo, hi = v.quantile(0.01), v.quantile(0.99)
    v = v.clip(lo, hi)
    return v.rank(pct=True).fillna(NEUTRAL)


SECTOR_NEUTRAL = os.environ.get("SECTOR_NEUTRAL", "0").strip().lower()
REGIME_TILT = os.environ.get("REGIME_TILT", "0").strip().lower()


def regime_weights(regime_state: str | None = None) -> dict[str, float]:
    """Return factor weights, optionally tilted for risk-off ('red') regimes when REGIME_TILT=1."""
    if REGIME_TILT not in ("1", "true", "yes") or regime_state != "red":
        return dict(W)
    return {"value": 0.25, "quality": 0.30, "growth": 0.15, "momentum": 0.15, "risk": 0.15}


def construct_portfolio(df: pd.DataFrame, max_names: int = 25,
                        max_per_sector: int = 5, max_weight: float = 0.08) -> pd.DataFrame:
    """Build a sector-capped, inverse-volatility weighted portfolio from ranked eligible rows."""
    if df.empty or "rank" not in df.columns:
        return pd.DataFrame()
    el = df[df["rank"].notna()].sort_values("rank")
    picked = []
    sec_counts: dict[str, int] = {}
    for idx, row in el.iterrows():
        sec = row.get("sector") or "Unknown"
        if sec_counts.get(sec, 0) >= max_per_sector:
            continue
        picked.append(idx)
        sec_counts[sec] = sec_counts.get(sec, 0) + 1
        if len(picked) >= max_names:
            break
    if not picked:
        return pd.DataFrame()
    port = el.loc[picked].copy()
    vol = port["vol_180d"].astype(float).fillna(0.25).clip(lower=0.12, upper=0.80) \
        if "vol_180d" in port.columns else pd.Series(0.25, index=port.index)
    inv_vol = 1.0 / vol
    raw_w = inv_vol / inv_vol.sum()
    port["eq_weight"] = 1.0 / len(port)
    port["risk_weight"] = raw_w.clip(upper=max_weight)
    port["risk_weight"] = port["risk_weight"] / port["risk_weight"].sum()
    return port


def sector_pct_rank(df: pd.DataFrame, col: str, negate=False, min_group=5) -> pd.Series:
    """Percentile rank within sector (when sector has >= min_group valid values),
    falling back to universe-wide pct_rank for small/unmapped sectors.
    Controlled by SECTOR_NEUTRAL ('0' = universe, 'blend' = 50/50, '1'/'full' = sector)."""
    if col not in df.columns:
        return pd.Series([NEUTRAL] * len(df), index=df.index)
    uni_rank = pct_rank(df[col], negate)
    if SECTOR_NEUTRAL in ("0", "false", "none", "") or "sector" not in df.columns:
        return uni_rank.where(df[col].notna(), NEUTRAL)
    sec_rank = uni_rank.copy()
    for _sec, grp in df.groupby("sector", dropna=True):
        if grp[col].notna().sum() >= min_group:
            sec_rank.loc[grp.index] = pct_rank(grp[col], negate)
    if SECTOR_NEUTRAL == "blend":
        out = 0.5 * sec_rank + 0.5 * uni_rank
    else:
        out = sec_rank
    return out.where(df[col].notna(), NEUTRAL)


def latest_fundamentals(cur, symbols, as_of):
    """{symbol: {metric: value}} — latest statement facts + live ratios ≤ as_of.
    Flow metrics come from full-year (yfinance) rows only: EDGAR period rows are
    year-to-date for 10-Q periods (DQ-2, 2026-10-10)."""
    cur.execute(
        """SELECT DISTINCT ON (symbol, metric) symbol, metric, value
           FROM stock_fundamentals
           WHERE symbol = ANY(%s) AND ts <= %s AND filing_published_at <= %s
             AND (metric <> ALL(%s) OR source = 'yfinance')
           ORDER BY symbol, metric, ts DESC""",
        (symbols, as_of, as_of, list(FLOW_METRICS)))
    out = {}
    for sym, metric, val in cur.fetchall():
        out.setdefault(sym, {})[metric] = val
    return out


def annual_series(cur, symbols, metric, as_of, years_back):
    """{symbol: [(period_end, value)]} annual (full-year) values, newest first.
    Reads yfinance annual statements only. EDGAR rows mix 3/6/9/12-month
    year-to-date periods under one key, so "max period end per calendar year"
    compared a half-year with a full year (median 3y revenue CAGR −17.8 % vs
    +8.5 %, DQ-2). The EDGAR path returns via financial_facts (TTM, later)."""
    cur.execute(
        """SELECT symbol, ts, value FROM stock_fundamentals
           WHERE symbol = ANY(%s) AND metric = %s AND ts <= %s
             AND filing_published_at <= %s AND source = 'yfinance'
           ORDER BY symbol, ts DESC""",
        (symbols, metric, as_of, as_of))
    raw = {}
    for sym, ts, val in cur.fetchall():
        raw.setdefault(sym, []).append((ts, val))
    series = {}
    for sym, entries in raw.items():
        per_year = {}
        for ts, val in entries:
            y = ts.year
            if y not in per_year or ts > per_year[y][0]:
                per_year[y] = (ts, val)
        series[sym] = [per_year[y] for y in sorted(per_year, reverse=True)]
    return series


# DQ-2 reader switch (v12): 'yfinance' = v9 behaviour (full-year yfinance rows only);
# 'facts' = financial_facts first (duration-aware, point-in-time by first filing): EDGAR for
# US (v12) and NSE consolidated filings for India (G-6, v14); yfinance is the fallback.
FACT_SOURCES = ["edgar", "nse_cons"]
FUND_SOURCE = os.environ.get("FUND_SOURCE", "yfinance").strip().lower()
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"   # score + print the shortlist, write nothing
# A facts series whose newest annual period ended longer ago than this is stale (e.g. a
# company switched XBRL revenue tags and our map stopped seeing it: CBOE ends 2017).
# Stale -> fall back to yfinance, never score on it. ~15 months = FY end + 10-K deadline.
FACTS_MAX_AGE_DAYS = 460


def ttm_value(rows, as_of=None):
    """Pure. rows: [(period_end date, duration_days, value)] as known on the as-of date.
    Trailing twelve months = latest annual + newest YTD after it − same-length YTD one year
    earlier; falls back to the latest annual when no matching pair exists; None without
    an annual figure. Durations are fuzzy: EDGAR uses 89–91 / 182–184 / 273–274 / 364–365."""
    annual = [r for r in rows if 350 <= r[1] <= 380]
    if not annual:
        return None
    a = max(annual, key=lambda r: r[0])
    if as_of is not None and (as_of - a[0]).days > FACTS_MAX_AGE_DAYS:
        return None
    ytd = [r for r in rows if 80 <= r[1] <= 290 and r[0] > a[0]]
    if not ytd:
        return a[2]
    q = max(ytd, key=lambda r: (r[0], r[1]))

    def year_before(r):
        cands = [x for x in rows if abs(x[1] - r[1]) <= 10 and abs((r[0] - x[0]).days - 365) <= 20]
        return min(cands, key=lambda x: abs((r[0] - x[0]).days - 365)) if cands else None

    if abs((q[0] - a[0]).days - q[1]) <= 20:          # q is a true YTD: it starts where the annual ends
        p = year_before(q)
        return a[2] + q[2] - p[2] if p else a[2]
    # Only single quarters exist (NSE 2012-2024): TTM = annual + quarters since − the same quarters a year earlier.
    quarters = sorted({r[0]: r for r in rows if 80 <= r[1] <= 100 and r[0] > a[0]}.values())
    span = 0
    for r in quarters:
        span += r[1]
    if not quarters or abs((quarters[-1][0] - a[0]).days - span) > 20:
        return a[2]                                    # gaps: refuse to guess
    priors = [year_before(r) for r in quarters]
    if any(x is None for x in priors):
        return a[2]
    return a[2] + sum(r[2] for r in quarters) - sum(x[2] for x in priors)


def facts_annual_series(cur, symbols, metric, as_of):
    """{symbol: [(period_end, value)]} full-year facts (EDGAR / NSE) known on as_of, newest first
    (latest filing per period wins, so restatements count only once they were filed)."""
    cur.execute(
        """SELECT DISTINCT ON (symbol, period_end) symbol, period_end, value
           FROM financial_facts
           WHERE source = ANY(%s) AND symbol = ANY(%s) AND metric = %s
             AND duration_days BETWEEN 350 AND 380 AND filed <= %s
           ORDER BY symbol, period_end, filed DESC""",
        (FACT_SOURCES, symbols, metric, as_of))
    out = {}
    for sym, end, val in cur.fetchall():
        out.setdefault(sym, []).append((end, val))
    return {s: sorted(v, reverse=True) for s, v in out.items()}


def facts_ttm(cur, symbols, metric, as_of):
    """{symbol: ttm value} from financial_facts (EDGAR / NSE consolidated), point-in-time."""
    cur.execute(
        """SELECT DISTINCT ON (symbol, period_end, duration_days)
                  symbol, period_end, duration_days, value
           FROM financial_facts
           WHERE source = ANY(%s) AND symbol = ANY(%s) AND metric = %s AND filed <= %s
             AND period_end > (%s::date - 800)
           ORDER BY symbol, period_end, duration_days, filed DESC""",
        (FACT_SOURCES, symbols, metric, as_of, as_of))
    rows = {}
    for sym, end, dur, val in cur.fetchall():
        rows.setdefault(sym, []).append((end, dur, val))
    return {s: v for s, v in ((s, ttm_value(r, as_of)) for s, r in rows.items()) if v is not None}


def latest_metrics(cur, symbols):
    """{symbol: {metric: value}} at each symbol's max ts."""
    cur.execute(
        """SELECT DISTINCT ON (symbol) symbol, ts FROM stock_metrics_daily
           WHERE symbol = ANY(%s) ORDER BY symbol, ts DESC""", (symbols,))
    stamps = {r[0]: r[1] for r in cur.fetchall()}
    if not stamps:
        return {}, {}
    cur.execute(
        """SELECT symbol, metric, value FROM stock_metrics_daily
           WHERE symbol = ANY(%s) AND ts = ANY(%s)
           ORDER BY symbol, metric, ts DESC""",
        (list(stamps), list(set(stamps.values()))))
    out = {}
    for sym, metric, val in cur.fetchall():
        if stamps.get(sym):
            out.setdefault(sym, {})[metric] = val  # last row wins = newest ts
    return out, stamps


def run_universe(conn, universe, today, limit):
    """One universe leg: read → score → atomic write. Raises OperationalError
    on a dropped connection so the caller can reconnect and retry the leg."""
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, sector FROM universe_constituents WHERE active AND universe=%s "
                    "ORDER BY 1", (universe,))
        const_rows = cur.fetchall()
        symbols = [r[0] for r in const_rows]
        sectors = {r[0]: r[1] for r in const_rows if len(r) > 1}
        if limit:
            symbols = symbols[:limit]
        if not symbols:
            return (0, 0, 0)
        fund = latest_fundamentals(cur, symbols, today)
        mets, stamps = latest_metrics(cur, symbols)
        rev = annual_series(cur, symbols, "total_revenue", today, 3)
        eps = annual_series(cur, symbols, "eps", today, 1)
        fcf_ttm = {}
        if FUND_SOURCE == "facts":
            day = today.date()
            for name, series in (("total_revenue", rev), ("eps", eps)):
                for sym, hist in facts_annual_series(cur, symbols, name, day).items():
                    if len(hist) >= 2 and (day - hist[0][0]).days <= FACTS_MAX_AGE_DAYS:
                        series[sym] = hist
            ocf, capex = facts_ttm(cur, symbols, "ocf", day), facts_ttm(cur, symbols, "capex", day)
            fcf_ttm = {s: ocf[s] - capex[s] for s in ocf if s in capex}

        rows = []
        for sym in symbols:
            f, m = fund.get(sym, {}), mets.get(sym, {})
            ts = stamps.get(sym)
            if not ts:
                continue
            # derived fundamentals
            rev_cagr = eps_g = eps_accel = None
            hist = rev.get(sym, [])
            if len(hist) >= 2:  # annualized: latest vs ~3 annual steps back
                back = min(3, len(hist) - 1)
                latest_v, old_v = hist[0][1], hist[back][1]
                if old_v and old_v > 0 and latest_v and latest_v > 0:
                    yrs = max((hist[0][0] - hist[back][0]).days / 365.25, 1)
                    rev_cagr = (latest_v / old_v) ** (1 / yrs) - 1
            e = eps.get(sym, [])
            if len(e) >= 2 and e[1][1] and e[1][1] != 0:
                eps_g = e[0][1] / e[1][1] - 1
                if len(e) >= 3 and e[2][1] and e[2][1] != 0:
                    prior_g = e[1][1] / e[2][1] - 1
                    eps_accel = eps_g - prior_g
            de = None
            if f.get("total_debt") is not None and f.get("shareholder_equity"):
                if f["shareholder_equity"] != 0:
                    de = f["total_debt"] / f["shareholder_equity"]
            fcf_y = None
            fcf_v = fcf_ttm.get(sym, f.get("fcf"))
            if fcf_v is not None and f.get("market_cap"):
                if f["market_cap"] != 0:
                    fcf_y = fcf_v / f["market_cap"]
            rows.append({
                "symbol": sym, "ts": ts, "sector": sectors.get(sym),
                "pe": f.get("pe"), "pb": f.get("pb"), "ev_ebitda": f.get("ev_ebitda"),
                "fcf_yield": fcf_y, "roe": f.get("roe"),
                "operating_margin": f.get("operating_margin"), "de": de,
                "rev_cagr_3y": rev_cagr, "eps_growth_yoy": eps_g,
                "eps_accel": eps_accel,
                "ret_6m": m.get("ret_6m"), "ret_12m": m.get("ret_12m"),
                "pos_52w": m.get("pos_52w"), "sma200_ratio": m.get("sma200_ratio"),
                "vol_180d": m.get("vol_180d"), "max_dd_252d": m.get("max_dd_252d"),
                "dol_vol_20d": m.get("dol_vol_20d"),
            })
        df = pd.DataFrame(rows)
        if df.empty:
            print(f"WARN {universe}: no rows", flush=True)
            return (0, 0, 0)

        def rank(col, negate=False):  # noqa: F811 (kept: debugging helper)
            return pct_rank(df[col], negate) if col in df else \
                pd.Series(pd.NA, index=df.index)

        P = {}
        for col, neg in (("pe", True), ("pb", True), ("ev_ebitda", True),
                         ("fcf_yield", False), ("roe", False),
                         ("operating_margin", False), ("de", True),
                         ("rev_cagr_3y", False), ("eps_growth_yoy", False),
                         ("eps_accel", False),
                         ("ret_6m", False), ("ret_12m", False), ("pos_52w", False),
                         ("sma200_ratio", False), ("vol_180d", True),
                         ("max_dd_252d", True)):
            P[col] = sector_pct_rank(df, col, neg)
        factors = {
            "value": (P["pe"] + P["pb"] + P["ev_ebitda"] + P["fcf_yield"]) / 4,
            "quality": (P["roe"] + P["operating_margin"] + P["de"]) / 3,
            "growth": (0.45 * P["rev_cagr_3y"] + 0.35 * P["eps_growth_yoy"]
                       + 0.20 * P["eps_accel"]),
            "momentum": (P["ret_6m"] + P["ret_12m"] + P["pos_52w"]
                         + P["sma200_ratio"]) / 4,
            "risk": (P["vol_180d"] + P["max_dd_252d"]) / 2,
        }
        # coverage: share of non-imputed components
        comps = ["pe", "pb", "ev_ebitda", "fcf_yield", "roe", "operating_margin",
                 "de", "rev_cagr_3y", "eps_growth_yoy", "ret_6m", "ret_12m",
                 "pos_52w", "sma200_ratio", "vol_180d", "max_dd_252d"]
        coverage = df[comps].notna().mean(axis=1)
        composite = sum(W[k] * factors[k] for k in W) * 100
        liq_floor = df["dol_vol_20d"].quantile(0.10) \
            if "dol_vol_20d" in df else 0
        eligible = (coverage >= 0.60) & (df["dol_vol_20d"].fillna(0) >= liq_floor)
        # rank among eligible only
        rank_series = pd.Series(pd.NA, index=df.index, dtype="Int64")
        el_pos = composite[eligible].rank(ascending=False, method="min")
        rank_series[eligible] = el_pos.astype(int).values
        n_short = int((rank_series <= 25).sum())
        if DRY_RUN:
            top = df.assign(c=composite, r=rank_series)[eligible].sort_values("r").head(50)
            for _, row in top.iterrows():
                print(f"TOP {FUND_SOURCE} {universe} {int(row['r'])} {row['symbol']} "
                      f"{row['c']:.2f} rev_cagr={row['rev_cagr_3y']} fcf_y={row['fcf_yield']}",
                      flush=True)
            print(f"{universe}: DRY-RUN scored={len(df)} eligible={int(eligible.sum())}", flush=True)
            return (len(df), int(eligible.sum()), n_short)
        with conn.transaction():  # single atomic write per universe
            with conn.cursor() as c2:
                for i in df.index:
                    c2.execute("DELETE FROM stock_scores WHERE symbol=%s AND ts=%s",
                               (df.at[i, "symbol"], df.at[i, "ts"]))
                    c2.execute(
                        "INSERT INTO stock_scores (symbol, ts, universe, value_score, "
                        "quality_score, growth_score, momentum_score, risk_score, "
                        "composite, rank, coverage, eligible) VALUES "
                        "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (df.at[i, "symbol"], df.at[i, "ts"], universe,
                         float(factors["value"].at[i]), float(factors["quality"].at[i]),
                         float(factors["growth"].at[i]),
                         float(factors["momentum"].at[i]), float(factors["risk"].at[i]),
                         float(composite.at[i]),
                         int(rank_series.at[i]) if pd.notna(rank_series.at[i]) else None,
                         float(coverage.at[i]), bool(eligible.at[i])))
                c2.execute("DELETE FROM screening_runs WHERE ts=%s AND universe=%s",
                           (today, universe))
                c2.execute("INSERT INTO screening_runs (ts, universe, n_scored, "
                            "n_eligible, n_shortlisted, params_hash) VALUES "
                            "(%s,%s,%s,%s,%s,%s)",
                            (today, universe, len(df), int(eligible.sum()), n_short,
                             "v1-balanced-25/25-20-20-10"))
        print(f"{universe}: scored={len(df)} eligible={int(eligible.sum())} "
              f"shortlist={n_short} composite[min/max]="
              f"{composite.min():.1f}/{composite.max():.1f}", flush=True)
        return (len(df), int(eligible.sum()), n_short)


def main() -> int:
    limit = int(os.environ.get("UNIVERSE_LIMIT", "0") or 0) or None
    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT universe FROM universe_constituents WHERE active")
        universes = [r[0] for r in cur.fetchall()]
    total = (0, 0, 0)
    for universe in universes:
        try:
            res = run_universe(conn, universe, today, limit)
        except psycopg.OperationalError as exc:
            print(f"WARN {universe} conn dropped ({exc}); reconnecting, retrying "
                  f"leg once", flush=True)
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            conn = connect()
            res = run_universe(conn, universe, today, limit)  # retry once, idempotent
        total = tuple(a + b for a, b in zip(total, res))
    conn.close()
    print(f"SCREENER-OK scored={total[0]} eligible={total[1]} "
          f"shortlisted={total[2]} fund_source={FUND_SOURCE}{' dry_run' if DRY_RUN else ''}",
          flush=True)
    return 0 if total[0] else 1


if __name__ == "__main__":
    sys.exit(main())
