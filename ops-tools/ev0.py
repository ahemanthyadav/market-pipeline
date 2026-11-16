#!/usr/bin/env python3
"""EV-0 — signal test harness (evidence-based-signals-roadmap.md §5, written before testing).

Monthly rebalance. For each month-end and universe: cross-sectional Spearman IC between a
signal and the NEXT month's total return (from adj_close), quintile spread (Q5−Q1), turnover,
spread net of costs, Fama-MacBeth slope with the screener's other price inputs as controls.
Verdict per §5: (1) Newey-West IC t > 3; (2) same sign in both halves and both universes;
(3) net quintile spread > 0 at 0.2 % (US) / 0.4 % (India) per trade; (4) FM t > 2 with controls
— PARTIAL: price controls only (value/quality/growth history is not point-in-time for India yet).

Known bias, printed in every report: the universe is TODAY's constituents (survivorship) until
EV-1 historical membership exists; `universe_membership_snapshots` started 2026-10-18.

Usage (workstation; DB via a psql command in $PSQL_CMD, like audit-price-adjustments.py):
  PSQL_CMD='kubectl -n market-data exec -i timescaledb-0 -- env PGPASSWORD=… psql -U postgres -d postgres' \\
    uv run --with pandas --with pyarrow python ops-tools/ev0.py export   # -> out/ev0/prices.parquet
  uv run --with pandas --with pyarrow python ops-tools/ev0.py run       # -> out/ev0/report-<date>.md + .csv

EV-19 (PEAD / SUE) and EV-20 (point-in-time value & quality sleeves), roadmap §8 — research only:
  PSQL_CMD=… uv run --with pandas --with pyarrow python ops-tools/ev0.py export-pit
      # -> out/ev0/financials_pit.parquet (all durations, first-filed resolved locally) + splits.parquet
  uv run --with pandas --with pyarrow python ops-tools/ev0.py run-pit   # -> out/ev0/report-ev19-20-<date>.md + .csv
"""
import argparse
import datetime as dt
import io
import os
import subprocess
import sys

import numpy as np
import pandas as pd

OUT = "out/ev0"
COST = {"SP500": 0.002, "NIFTY500": 0.004}          # per trade, §5 criterion 3
EXISTING = ["ret_6m", "ret_12m", "pos_52w", "sma200_ratio", "low_vol"]  # composite price inputs
NEW = ["ret_12_1", "near_high", "low_beta", "rev_1m"]                  # EV-3, EV-4, EV-5, reversal
G21 = ["amihud", "max_daily_ret", "idio_vol"]                          # G-21 price/volume signals
G21_EXTRA = ["industry_momentum", "distress_score"]  # needs sector/financials data (computed in run())
MIN_NAMES = 50


# ---------------------------------------------------------------- export
def export(start):
    cmd = os.environ.get("PSQL_CMD")
    if not cmd:
        sys.exit("set PSQL_CMD (see module docstring)")
    # start is interpolated into SQL run as postgres — accept only a real ISO date
    start = str(dt.date.fromisoformat(str(start)))
    sql = ("COPY (SELECT p.symbol, p.ts::date AS d, p.close, p.adj_close, p.volume, u.universe, u.sector "
           "FROM stock_prices p JOIN universe_constituents u USING (symbol) "
           f"WHERE p.ts >= '{start}' ORDER BY 1, 2) TO STDOUT WITH CSV HEADER")
    raw = subprocess.run(cmd + " -v ON_ERROR_STOP=1 -c " + _q(sql), shell=True, check=True,
                         capture_output=True).stdout
    df = pd.read_csv(io.BytesIO(raw), parse_dates=["d"])
    os.makedirs(OUT, exist_ok=True)
    df.to_parquet(f"{OUT}/prices.parquet", index=False)
    print(f"exported {len(df)} rows, {df.symbol.nunique()} symbols, {df.d.min().date()}..{df.d.max().date()}")
    # EV-1 point-in-time membership (ADR 0025): fixes the survivorship bias
    # for the SP500 leg (2026-10-27 re-run; India still pending).
    msql = ("COPY (SELECT universe, symbol, added, removed FROM index_membership "
            "ORDER BY 1, 2, 3) TO STDOUT WITH CSV HEADER")
    raw = subprocess.run(cmd + " -v ON_ERROR_STOP=1 -c " + _q(msql), shell=True, check=True,
                         capture_output=True).stdout
    mem = pd.read_csv(io.BytesIO(raw), parse_dates=["added", "removed"])
    mem.to_parquet(f"{OUT}/membership.parquet", index=False)
    print(f"membership: {len(mem)} intervals, universes {sorted(mem.universe.unique())}")

    # Financial facts for distress score and fundamentals-based signals (G-21)
    fsql = ("COPY (SELECT symbol, source, metric, period_end::date AS period_end, "
            "filed::date AS filed, value FROM financial_facts "
            "WHERE source = 'edgar' ORDER BY symbol, filed, period_end) TO STDOUT WITH CSV HEADER")
    raw = subprocess.run(cmd + " -v ON_ERROR_STOP=1 -c " + _q(fsql), shell=True, check=True,
                         capture_output=True).stdout
    if raw.strip():
        fin = pd.read_csv(io.BytesIO(raw), parse_dates=["period_end", "filed"])
        fin.to_parquet(f"{OUT}/financials.parquet", index=False)
        print(f"financials: {len(fin)} rows, {fin.symbol.nunique()} symbols")
    else:
        print("financials: 0 rows (no EDGAR facts yet)")


def _q(s):
    return "'" + s.replace("'", "'\"'\"'") + "'"


# ---------------------------------------------------------------- signals
def signals(px, vol, close):
    """Daily panels (date x symbol) -> {name: panel}. px = the series signals are built on."""
    r = px.pct_change(fill_method=None)
    mkt = r.mean(axis=1)
    hi, lo = px.rolling(252, min_periods=200).max(), px.rolling(252, min_periods=200).min()
    cov = r.rolling(252, min_periods=200).cov(mkt)
    beta = cov.div(mkt.rolling(252, min_periods=200).var(), axis=0)
    # --- G-21 extra signals (price/volume only, testable via EV-0) ---
    dollar_vol = close * vol
    amihud = (r.abs() / dollar_vol).rolling(252, min_periods=200).mean()
    max_daily_ret = r.rolling(252, min_periods=200).max()
    # idiosyncratic vol: residual std from market model r_i = a + b*r_m + e
    resid = r - beta.mul(mkt, axis=0)
    idio_vol = resid.rolling(252, min_periods=200).std()
    out = {
        "ret_6m": px / px.shift(126) - 1,
        "ret_12m": px / px.shift(252) - 1,
        "ret_12_1": px.shift(21) / px.shift(252) - 1,
        "near_high": px / hi,
        "pos_52w": (px - lo) / (hi - lo),
        "sma200_ratio": px / px.rolling(200, min_periods=180).mean(),
        "low_vol": -r.rolling(252, min_periods=200).std(),
        "low_beta": -beta,
        "rev_1m": -(px / px.shift(21) - 1),
        # G-21: higher score = better (illiquidity negated, idio vol negated)
        "amihud": -amihud,
        "max_daily_ret": max_daily_ret,
        "idio_vol": -idio_vol,
        "_liq": (close * vol).rolling(63, min_periods=40).median(),
    }
    # Equal-weighted cross-sectional percentile composite of the 5 existing price signals
    comp = sum(out[k].rank(axis=1, pct=True) for k in EXISTING) / len(EXISTING)
    out["composite_price"] = comp
    return out


def month_ends(index):
    s = pd.Series(index, index=index)
    return pd.DatetimeIndex(s.groupby(index.to_period("M")).max().values)


# ---------------------------------------------------------------- statistics
def nw_t(x, lag=6):
    """Newey-West t-statistic of the mean (Bartlett kernel)."""
    x = np.asarray(pd.Series(x).dropna(), float)
    n = len(x)
    if n < 12:
        return np.nan
    m, d = x.mean(), x - x.mean()
    s = d @ d / n
    for k in range(1, min(lag, n - 1) + 1):
        s += 2 * (1 - k / (lag + 1)) * (d[k:] @ d[:-k]) / n
    return m / np.sqrt(s / n) if s > 0 else np.nan


def rank(s):
    return s.rank(pct=True)


def evaluate(sig_me, fwd_me, liq_me, controls_me, cost, member_me=None):
    """Per-month IC / quintile spread / turnover / FM slope for one signal in one universe.
    member_me: optional (month-end x symbol) bool panel — point-in-time membership."""
    rows, prev_long, prev_short = [], None, None
    for t in sig_me.index:
        s, f, liq = sig_me.loc[t], fwd_me.loc[t], liq_me.loc[t]
        ok = s.notna() & f.notna() & liq.notna() & (liq >= liq.quantile(0.2))
        if member_me is not None:
            ok &= member_me.loc[t].reindex(ok.index, fill_value=False)
        if ok.sum() < MIN_NAMES:
            continue
        s, f = s[ok], f[ok]
        ic = rank(s).corr(rank(f))
        q = pd.qcut(s.rank(method="first"), 5, labels=False)
        long_, short = set(s.index[q == 4]), set(s.index[q == 0])
        spread = f[q == 4].mean() - f[q == 0].mean()
        to_l = 1.0 if prev_long is None else 1 - len(long_ & prev_long) / max(len(long_), 1)
        to_s = 1.0 if prev_short is None else 1 - len(short & prev_short) / max(len(short), 1)
        prev_long, prev_short = long_, short
        fm = np.nan
        X = pd.DataFrame({"sig": rank(s)})
        for c, panel in controls_me.items():
            X[c] = rank(panel.loc[t][ok])
        X = X.dropna()
        if len(X) >= MIN_NAMES:
            y = rank(f.loc[X.index]).values - 0.5
            A = np.column_stack([np.ones(len(X)), X.values - 0.5])
            coef, *_ = np.linalg.lstsq(A, y, rcond=None)
            fm = coef[1]
        rows.append({"t": t, "n": int(ok.sum()), "ic": ic, "spread": spread,
                     "net": spread - cost * 2 * (to_l + to_s), "turnover": (to_l + to_s) / 2,
                     "fm": fm})
    return pd.DataFrame(rows).set_index("t") if rows else pd.DataFrame()


def summarize(m):
    if m.empty:
        return {}
    half = len(m) // 2
    return {"months": len(m), "start": m.index.min().date(), "names": int(m.n.median()),
            "ic_mean": m.ic.mean(), "ic_t_nw": nw_t(m.ic), "ic_h1": m.ic.iloc[:half].mean(),
            "ic_h2": m.ic.iloc[half:].mean(), "spread_ann": m.spread.mean() * 12,
            "net_ann": m.net.mean() * 12, "turnover": m.turnover.mean(), "fm_t_nw": nw_t(m.fm)}


def verdict(per_uni):
    """§5 criteria 1-4 across universes. per_uni: {universe: summary}."""
    s = [v for v in per_uni.values() if v]
    if not s:
        return "NO DATA", {}
    signs = {np.sign(v[k]) for v in s for k in ("ic_h1", "ic_h2")}
    c = {"1 t>3": all(abs(v["ic_t_nw"]) > 3 for v in s),
         "2 same sign": len(signs) == 1,
         "3 net>0": all(v["net_ann"] * np.sign(v["ic_mean"]) > 0 for v in s),
         "4 FM t>2 (price controls)": all(abs(v["fm_t_nw"]) > 2 for v in s)}
    return ("PASS" if all(c.values()) else "FAIL"), c


# ---------------------------------------------------------------- run
def membership_mask(me, symbols, universe):
    """(month-end x symbol) bool panel from index_membership intervals, or
    None when the universe has no point-in-time history yet."""
    path = f"{OUT}/membership.parquet"
    if not os.path.exists(path):
        return None
    mem = pd.read_parquet(path)
    mem = mem[mem.universe == universe]
    if mem.empty:
        return None
    mask = pd.DataFrame(False, index=me, columns=symbols)
    for row in mem.itertuples():
        if row.symbol not in mask.columns:
            continue
        end = row.removed if pd.notna(row.removed) else pd.Timestamp.max
        sel = (me >= row.added) & (me < end)
        mask.loc[sel, row.symbol] = True
    return mask


def run(price_cols, start):
    df = pd.read_parquet(f"{OUT}/prices.parquet")
    df = df[df.d >= pd.Timestamp(start) - pd.Timedelta(days=400)]
    results, table = {}, []
    for price_col in price_cols:
        for uni, g in df.groupby("universe"):
            wide = {c: g.pivot(index="d", columns="symbol", values=c).sort_index()
                    for c in ("close", "adj_close", "volume")}
            tr = wide["adj_close"].fillna(wide["close"]).ffill(limit=5)
            px = wide[price_col].fillna(wide["close"]).ffill(limit=5) if price_col == "adj_close" \
                else wide["close"].ffill(limit=5)
            sig = signals(px, wide["volume"], wide["close"])
            # --- G-21: industry momentum (12-month sector return, equal-weighted) ---
            sector_map = dict(zip(g.symbol, g.sector))
            sym_rets = px.pct_change(fill_method=None)
            sector_rets = {}
            for sym in px.columns:
                sec = sector_map.get(sym)
                if sec:
                    sector_rets.setdefault(sec, []).append(sym_rets[sym])
            sector_panel = pd.DataFrame({s: np.mean(v, axis=0) for s, v in sector_rets.items()},
                                        index=sym_rets.index)
            # 12-month cumulative return per sector
            sector_mom = (sector_panel + 1).rolling(252, min_periods=200).apply(
                lambda x: np.prod(x + 1) - 1, raw=True)
            # Map back to symbols: each symbol gets its sector's momentum
            sig["industry_momentum"] = pd.DataFrame(
                {sym: sector_mom[sector_map.get(sym)] for sym in px.columns
                 if sector_map.get(sym) in sector_mom.columns},
                index=sector_mom.index)
            # Align columns to px.columns
            sig["industry_momentum"] = sig["industry_momentum"].reindex(columns=px.columns)

            # --- G-21: distress score (Altman Z-score, point-in-time financials) ---
            fin_path = f"{OUT}/financials.parquet"
            if os.path.exists(fin_path):
                fin = pd.read_parquet(fin_path)
                # Pivot to wide: one row per (symbol, period_end) with metrics as columns
                fin_wide = fin.pivot_table(index=["symbol", "period_end"],
                                           columns="metric", values="value", aggfunc="first")
                fin_wide = fin_wide.sort_index(level="period_end")
                # For each symbol, forward-fill financials across dates (point-in-time)
                # Build a daily panel: merge_asof per symbol
                dates = px.index
                distress = pd.DataFrame(index=dates, columns=px.columns, dtype=float)
                fin_symbols = set(fin_wide.index.get_level_values("symbol").unique())
                for sym in px.columns:
                    if sym not in fin_symbols:
                        continue
                    sym_fin = fin_wide.xs(sym, level="symbol", drop_level=False)
                    if sym_fin.empty:
                        continue
                    # Reset index to get period_end as a column for merge_asof
                    sym_fin = sym_fin.reset_index(level="symbol", drop=True)
                    sym_fin = sym_fin.sort_values("period_end")
                    # merge_asof: for each date, get latest financials where period_end <= date
                    merged = pd.merge_asof(
                        pd.DataFrame({"d": dates}),
                        sym_fin,
                        left_on="d", right_on="period_end",
                        direction="backward"
                    )
                    # Compute Z-score components
                    ca = merged["current_assets"]
                    cl = merged["current_liabilities"]
                    ta = merged["total_assets"]
                    se = merged["shareholder_equity"]
                    oi = merged["operating_income"]
                    rev = merged["total_revenue"]
                    so = merged["shares_outstanding"]
                    close_sym = px[sym].reindex(merged["d"]).values
                    mve = close_sym * so  # market value of equity
                    bvl = ta - se  # book value of total liabilities
                    wc = ca - cl  # working capital
                    z = (1.2 * wc / ta + 1.4 * se / ta + 3.3 * oi / ta
                         + 0.6 * mve / bvl + 1.0 * rev / ta)
                    distress[sym] = z.values
                sig["distress_score"] = distress
            else:
                print(f"{uni}: no financials.parquet — skipping distress_score", flush=True)
            me = month_ends(px.index)
            me = me[me >= pd.Timestamp(start)]
            fwd = (tr.loc[me].shift(-1) / tr.loc[me] - 1)
            liq = sig["_liq"].loc[me]
            member = membership_mask(me, px.columns, uni)
            if member is not None:
                print(f"{uni}: point-in-time membership applied "
                      f"({int(member.values.sum())} symbol-months)", flush=True)
            for name in EXISTING + NEW + G21 + G21_EXTRA:
                ctrl = {c: sig[c].loc[me] for c in EXISTING if c != name}
                m = evaluate(sig[name].loc[me], fwd, liq, ctrl, COST.get(uni, 0.003),
                             member_me=member)
                sm = summarize(m)
                results.setdefault((price_col, name), {})[uni] = sm
                if sm:
                    table.append({"price": price_col, "signal": name, "universe": uni, **sm})
    return results, pd.DataFrame(table)


def report(results, table, start):
    today = dt.date.today().isoformat()
    os.makedirs(OUT, exist_ok=True)
    table.to_csv(f"{OUT}/ev0-{today}.csv", index=False)
    L = [f"# EV-0 report — {today}", "",
         f"Monthly rebalance from {start}; forward return = next month's total return (adj_close). "
         "Liquidity: 63-day median dollar volume ≥ the month's 20th percentile. "
         f"Costs per trade: {COST}.", "",
         "> **Survivorship:** SP500 uses POINT-IN-TIME membership (EV-1, ADR 0025 — applied "
         "2026-10-27). Universes without interval history (NIFTY500 and the ADR 0029 expansion) "
         "still use today's constituents; their price-signal results stay biased toward winners.",
         "> Criterion 4 uses price controls only (the other composite price inputs).", "",
         "| price | signal | verdict | universe | months | IC | NW t | IC 1st/2nd half | Q5−Q1 /yr | net /yr | turnover | FM t |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for (price_col, name), per in results.items():
        v, crit = verdict(per)
        for uni, s in per.items():
            if not s:
                continue
            L.append(f"| {price_col} | {name} | **{v}** | {uni} | {s['months']} | {s['ic_mean']:.3f} | "
                     f"{s['ic_t_nw']:.1f} | {s['ic_h1']:.3f}/{s['ic_h2']:.3f} | {s['spread_ann']:.1%} | "
                     f"{s['net_ann']:.1%} | {s['turnover']:.0%} | {s['fm_t_nw']:.1f} |")
        if crit:
            L.append(f"| | | {' · '.join(k + (' ✓' if ok else ' ✗') for k, ok in crit.items())} | | | | | | | | | |")
    path = f"{OUT}/report-{today}.md"
    open(path, "w").write("\n".join(L) + "\n")
    print(path)
    return path


# ---------------------------------------------------------------- EV-19 / EV-20: point-in-time fundamentals
# Research only (evidence roadmap §8): nothing here feeds scoring. Every fact is used only
# from the day AFTER its first filing (`filed` < month-end, strictly), and only its
# first-filed value — later restatements (comparatives re-filed in the next 10-Q/10-K)
# are dropped, so the panel never sees a number the market did not have at the time.
PIT_SOURCES = ("edgar", "nse_cons", "nse_sa")
PIT_METRICS = ("eps", "net_income", "operating_income", "total_revenue", "ocf", "capex",
               "shareholder_equity", "total_debt", "shares_outstanding",
               "profit_before_tax", "finance_costs")
FLOW = ("eps", "net_income", "operating_income", "total_revenue", "ocf", "capex",
        "profit_before_tax", "finance_costs")
INSTANT = ("shareholder_equity", "total_debt")
STALE_DAYS = 456          # ~15 months: an older fact counts as missing
SUE_N, SUE_MIN = 8, 4     # sigma over the prior 8 seasonal differences, at least 4
SUE_HOLD_DAYS = 91        # a surprise is "live" for ~60 trading days after its filing
PIT_FLAGSHIP = ("SP500", "NIFTY500")
US_VALUE = {"ep": True, "bp": True, "ebit_ev": True, "fcf_yield": True}
US_QUALITY = {"roe": True, "op_margin": True, "low_de": True}
IN_VALUE = {"ep": True}
IN_QUALITY = {"ebit_margin": True, "int_cover": True}
Q_DUR = (75, 105)         # financial_facts.duration_days of a discrete quarter


def export_pit():
    """financial_facts (all durations, with duration_days) + split history -> out/ev0/.
    financial_facts / corporate_actions are plain tables (not hypertables); one bounded
    COPY each, no server-side sort (sorted locally)."""
    cmd = os.environ.get("PSQL_CMD")
    if not cmd:
        sys.exit("set PSQL_CMD (see module docstring)")
    src = ",".join(f"'{s}'" for s in PIT_SOURCES)
    met = ",".join(f"'{m}'" for m in PIT_METRICS)
    fsql = ("COPY (SELECT symbol, source, metric, period_end, duration_days, filed, value "
            f"FROM financial_facts WHERE source IN ({src}) AND metric IN ({met})) "
            "TO STDOUT WITH CSV HEADER")
    raw = subprocess.run(cmd + " -v ON_ERROR_STOP=1 -c " + _q(fsql), shell=True, check=True,
                         capture_output=True).stdout
    fin = pd.read_csv(io.BytesIO(raw), parse_dates=["period_end", "filed"])
    os.makedirs(OUT, exist_ok=True)
    fin.to_parquet(f"{OUT}/financials_pit.parquet", index=False)
    print(f"financials_pit: {len(fin)} rows, {fin.symbol.nunique()} symbols, "
          f"sources {sorted(fin.source.unique())}")
    ssql = ("COPY (SELECT symbol, ts::date AS d, ratio FROM corporate_actions "
            "WHERE action_type = 'split' AND ratio > 0) TO STDOUT WITH CSV HEADER")
    raw = subprocess.run(cmd + " -v ON_ERROR_STOP=1 -c " + _q(ssql), shell=True, check=True,
                         capture_output=True).stdout
    spl = pd.read_csv(io.BytesIO(raw), parse_dates=["d"])
    spl.to_parquet(f"{OUT}/splits.parquet", index=False)
    print(f"splits: {len(spl)} events, {spl.symbol.nunique()} symbols")


def first_filed(ff):
    """One row per (symbol, source, metric, period_end, duration_days): the FIRST filing."""
    keys = ["symbol", "source", "metric", "period_end", "duration_days"]
    return ff.sort_values("filed", kind="stable").drop_duplicates(keys, keep="first")


def pick_nse_source(ff):
    """India: per symbol keep consolidated (nse_cons) when it has any quarterly EPS, else
    standalone (nse_sa). Mixing the two would break year-on-year differences."""
    nse = ff[ff.source.isin(["nse_cons", "nse_sa"])]
    has_cons = set(nse[(nse.source == "nse_cons") & (nse.metric == "eps")
                       & nse.duration_days.between(*Q_DUR)].symbol)
    keep = ~ff.source.isin(["nse_cons", "nse_sa"]) | \
        ((ff.source == "nse_cons") & ff.symbol.isin(has_cons)) | \
        ((ff.source == "nse_sa") & ~ff.symbol.isin(has_cons))
    return ff[keep]



def split_factor(dates, split_dates, ratios):
    """F(t) = product of split ratios with ex-date AFTER t. stock_prices.close is back-adjusted
    to today's share basis, so a per-share number reported at t converts to today's basis as
    x / F(t) (EPS) or x * F(t) (share counts)."""
    sd = np.asarray(split_dates, dtype="datetime64[ns]")
    order = np.argsort(sd)
    sd, r = sd[order], np.asarray(ratios, float)[order]
    suffix = np.append(np.cumprod(r[::-1])[::-1], 1.0)        # suffix[i] = prod r[i:]
    idx = np.searchsorted(sd, np.asarray(dates, dtype="datetime64[ns]"), side="right")
    return suffix[idx]


def split_adjust(ff, splits):
    """EPS -> divide by F(filed); shares_outstanding -> multiply by F(filed)."""
    ff = ff.copy()
    if splits is None or splits.empty:
        return ff
    by = {s: g for s, g in splits.groupby("symbol")}
    for metric, op in (("eps", "div"), ("shares_outstanding", "mul")):
        m = ff.metric == metric
        for sym, idx in ff[m].groupby("symbol").groups.items():
            g = by.get(sym)
            if g is None:
                continue
            f = split_factor(ff.loc[idx, "filed"].values, g.d.values, g.ratio.values)
            ff.loc[idx, "value"] = ff.loc[idx, "value"] / f if op == "div" else ff.loc[idx, "value"] * f
    return ff


def discrete_quarters(ff):
    """Flow facts -> discrete ~3-month quarters. A 3M row is used as filed; a quarter that only
    exists inside a year-to-date figure (Q4 = FY - 9M; cash-flow quarters, which EDGAR reports
    YTD) is YTD(n quarters) - YTD(n-1 quarters, period_end ~91 days earlier), available from
    the later filing. Input: first-filed rows (symbol, metric, period_end, duration_days,
    filed, value). Output: symbol, metric, period_end, filed, value."""
    cols = ["symbol", "metric", "period_end", "filed", "value"]
    r = ff.dropna(subset=["duration_days"]).copy()
    r = r[r.duration_days >= 75]
    r["n"] = (r.duration_days / 91.3).round().astype(int)
    q = r[r.duration_days.between(*Q_DUR)]
    q = q.sort_values("filed").drop_duplicates(["symbol", "metric", "period_end"])[cols]
    y = r[(r.n >= 2) & (r.n <= 4)].copy()
    if y.empty:
        return q.reset_index(drop=True)
    y["target"] = y.period_end - pd.Timedelta(days=91)
    y["n_prev"] = y.n - 1
    prev = r[["symbol", "metric", "n", "period_end", "filed", "value"]].rename(
        columns={"n": "n_prev", "period_end": "pe_prev", "filed": "filed_prev", "value": "v_prev"})
    y = pd.merge_asof(y.sort_values("target"), prev.sort_values("pe_prev"),
                      left_on="target", right_on="pe_prev", by=["symbol", "metric", "n_prev"],
                      tolerance=pd.Timedelta(days=20), direction="nearest")
    y = y.dropna(subset=["v_prev"])
    y["value"] = y.value - y.v_prev
    y["filed"] = y[["filed", "filed_prev"]].max(axis=1)
    y = y.sort_values("filed").drop_duplicates(["symbol", "metric", "period_end"])[cols]
    # a period that already has a direct 3M row keeps it
    k = q.assign(k=1).sort_values("period_end")[["symbol", "metric", "period_end", "k"]] \
        .rename(columns={"period_end": "pe_q"})
    y = pd.merge_asof(y.sort_values("period_end"), k, left_on="period_end", right_on="pe_q",
                      by=["symbol", "metric"], tolerance=pd.Timedelta(days=10),
                      direction="nearest")
    y = y[y.k.isna()][cols]
    return pd.concat([q, y], ignore_index=True).sort_values(
        ["symbol", "metric", "period_end"]).reset_index(drop=True)


def ttm(q):
    """Trailing-twelve-month sums of 4 consecutive discrete quarters (span 250-300 days
    between the 1st and 4th period_end), available from the latest of their filings."""
    q = q.sort_values(["symbol", "metric", "period_end"]).reset_index(drop=True)
    g = q.groupby(["symbol", "metric"])
    v, f = q.value.copy(), q.filed.copy()
    for k in (1, 2, 3):
        v = v + g.value.shift(k)
        f = pd.concat([f, g.filed.shift(k)], axis=1).max(axis=1)
    span = (q.period_end - g.period_end.shift(3)).dt.days
    out = q.assign(value=v, filed=f)[span.between(250, 300) & v.notna()]
    return out.reset_index(drop=True)


def sue_events(q, n=SUE_N, min_n=SUE_MIN):
    """Standardized unexpected earnings per filed quarter (Bernard & Thomas 1989 seasonal
    random walk): SUE = (EPS_q - EPS_{q-4}) / sigma(prior n seasonal differences, >= min_n).
    q: discrete quarterly EPS (symbol, period_end, filed, value), split-adjusted."""
    q = q.sort_values(["symbol", "period_end"]).reset_index(drop=True)
    lag = q[["symbol", "period_end", "value"]].rename(
        columns={"period_end": "pe_lag", "value": "v_lag"}).sort_values("pe_lag")
    q["target"] = q.period_end - pd.Timedelta(days=364)
    m = pd.merge_asof(q.sort_values("target"), lag, left_on="target", right_on="pe_lag",
                      by="symbol", tolerance=pd.Timedelta(days=20), direction="nearest")
    m = m.sort_values(["symbol", "period_end"]).reset_index(drop=True)
    m["diff"] = m.value - m.v_lag
    m["sigma"] = m.groupby("symbol")["diff"].transform(
        lambda s: s.shift(1).rolling(n, min_periods=min_n).std())
    m["sue"] = m["diff"] / m.sigma.where(m.sigma > 0)
    return m.dropna(subset=["sue"])[["symbol", "period_end", "filed", "sue"]].reset_index(drop=True)


def asof_panel(facts, me, symbols, value="value", max_age=STALE_DAYS, age_from="period_end"):
    """(month-end x symbol) panel of the latest fact FILED STRICTLY BEFORE each month-end.
    A filing that arrives late for an older period never replaces a newer period already
    known. Facts older than max_age days (measured from period_end, or from filed for
    event signals) are treated as missing."""
    out = pd.DataFrame(np.nan, index=me, columns=pd.Index(symbols))
    f = facts[facts.symbol.isin(set(symbols))].sort_values(["symbol", "filed", "period_end"])
    if f.empty:
        return out
    prior = f.groupby("symbol").period_end.transform(lambda s: s.cummax().shift(1))
    f = f[prior.isna() | (f.period_end > prior)]
    grid = pd.DataFrame({"t": np.repeat(me.values, f.symbol.nunique()),
                         "symbol": np.tile(f.symbol.unique(), len(me))}).sort_values("t")
    m = pd.merge_asof(grid, f.sort_values("filed")[["symbol", "filed", "period_end", value]],
                      left_on="t", right_on="filed", by="symbol", allow_exact_matches=False,
                      direction="backward")
    age = (m.t - m[age_from]).dt.days
    m.loc[age > max_age, value] = np.nan
    p = m.pivot(index="t", columns="symbol", values=value)
    out.update(p)
    return out


def winsor_rank(p, higher_is_better=True):
    """Screener pct_rank, per month: winsorize 1/99, cross-sectional percentile in [0,1]."""
    v = p if higher_is_better else -p
    lo, hi = v.quantile(0.01, axis=1), v.quantile(0.99, axis=1)
    return v.clip(lo, hi, axis=0).rank(axis=1, pct=True)


def sleeve(components, directions, min_present):
    """Screener-style sleeve: mean of component percentile ranks, a missing component = 0.5
    (NEUTRAL, as stock_screener.py does), NaN when fewer than min_present components exist."""
    ranks = [winsor_rank(components[k], d) for k, d in directions.items()]
    present = sum(r.notna().astype(int) for r in ranks)
    s = sum(r.fillna(0.5) for r in ranks) / len(ranks)
    return s.where(present >= min_present)


def ratio(a, b, positive_denominator=True):
    b = b.where(b > 0) if positive_denominator else b.where(b != 0)
    return a / b


def fundamental_panels(fin, splits, me, symbols, close_me):
    """All EV-19/EV-20 month-end panels for one universe. fin: raw financials_pit rows.
    close_me: month-end close (split-adjusted to today's basis, like stock_prices.close)."""
    ff = first_filed(fin[fin.symbol.isin(set(symbols))])
    ff = pick_nse_source(ff)
    ff = split_adjust(ff, splits)
    flows = discrete_quarters(ff[ff.metric.isin(FLOW)])
    t4 = ttm(flows)
    P = lambda df, metric, **kw: asof_panel(df[df.metric == metric], me, symbols, **kw)  # noqa: E731
    inst = ff[ff.metric.isin(INSTANT) & (ff.duration_days.fillna(0) < 75)]
    sh = ff[ff.metric == "shares_outstanding"].sort_values("duration_days") \
        .drop_duplicates(["symbol", "period_end"])
    out = {}
    out["sue"] = asof_panel(sue_events(flows[flows.metric == "eps"]), me, symbols, value="sue",
                            max_age=SUE_HOLD_DAYS, age_from="filed")
    ni, oi, rev = P(t4, "net_income"), P(t4, "operating_income"), P(t4, "total_revenue")
    eq, debt = P(inst, "shareholder_equity"), P(inst, "total_debt")
    mcap = P(sh, "shares_outstanding") * close_me
    is_us = ff.source.eq("edgar").groupby(ff.symbol).any().reindex(symbols, fill_value=False)
    us = is_us[is_us].index
    ind = is_us[~is_us].index
    # US (EDGAR): the screener's sleeves rebuilt point-in-time.
    #   value  = E/P (pe inverted, so losses rank cheap-last instead of best), B/P (pb),
    #            EBIT/EV (ev_ebitda proxy — the EDGAR set has no D&A; EV = mcap + gross debt,
    #            no cash credit, as ADR 0026), FCF yield = (OCF - capex) / mcap
    #   quality = ROE, operating margin, -D/E   (all TTM flows over latest balance sheet)
    us_c = {"ep": ratio(ni, mcap), "bp": ratio(eq, mcap),
            "ebit_ev": ratio(oi, mcap + debt.fillna(0)),
            "fcf_yield": ratio(P(t4, "ocf") - P(t4, "capex").abs(), mcap),
            "roe": ratio(ni, eq), "op_margin": ratio(oi, rev),
            "low_de": -ratio(debt, eq)}
    # India (NSE results): income statement only — no balance sheet, no share count.
    #   value  = TTM EPS / price;  quality = EBIT margin, interest cover (EBIT / finance costs)
    eps = P(t4, "eps")
    ebit = P(t4, "profit_before_tax") + P(t4, "finance_costs")
    fc = P(t4, "finance_costs")
    cover = (ebit / fc.where(fc > 0)).clip(upper=100)
    cover = cover.mask((fc == 0) & ebit.notna(), 100.0)      # no finance costs = best cover
    in_c = {"ep": ratio(eps, close_me), "ebit_margin": ratio(ebit, rev), "int_cover": cover}

    def only(p, keep):
        p = p.reindex(columns=symbols).copy()
        p.loc[:, ~symbols.isin(keep)] = np.nan
        return p
    for k, p in us_c.items():
        out[k] = only(p, us)
    for k, p in in_c.items():
        ip = only(p, ind)
        out[k] = ip if k not in out else out[k].fillna(ip)
    return out, (len(us), len(ind))


def run_pit(start):
    """EV-19 (SUE) and EV-20 (value/quality sleeves) through the same evaluate()/verdict()."""
    df = pd.read_parquet(f"{OUT}/prices.parquet")
    df = df[df.d >= pd.Timestamp(start) - pd.Timedelta(days=400)]
    fin = pd.read_parquet(f"{OUT}/financials_pit.parquet")
    spl = pd.read_parquet(f"{OUT}/splits.parquet") if os.path.exists(f"{OUT}/splits.parquet") \
        else None
    fin_syms = set(fin.symbol)
    results, table, notes = {}, [], []
    for uni, g in df.groupby("universe"):
        cover = g.symbol[g.symbol.isin(fin_syms)].nunique() / g.symbol.nunique()
        if cover < 0.5:
            notes.append(f"{uni}: skipped — fundamentals for {cover:.0%} of symbols")
            continue
        wide = {c: g.pivot(index="d", columns="symbol", values=c).sort_index()
                for c in ("close", "adj_close", "volume")}
        tr = wide["adj_close"].fillna(wide["close"]).ffill(limit=5)
        sig = signals(tr, wide["volume"], wide["close"])
        me = month_ends(tr.index)
        me = me[me >= pd.Timestamp(start)]
        fwd = tr.loc[me].shift(-1) / tr.loc[me] - 1
        liq = sig["_liq"].loc[me]
        member = membership_mask(me, tr.columns, uni)
        close_me = wide["close"].ffill(limit=5).loc[me]
        fp, (n_us, n_in) = fundamental_panels(fin, spl, me, tr.columns, close_me)
        if member is not None:   # rank sleeves among that month's members only
            fp = {k: v.where(member) for k, v in fp.items()}
        if n_us >= n_in:
            fp["value"] = sleeve(fp, US_VALUE, 2)
            fp["quality"] = sleeve(fp, US_QUALITY, 2)
            comps = list(US_VALUE) + list(US_QUALITY)
        else:
            fp["value"] = sleeve(fp, IN_VALUE, 1)
            fp["quality"] = sleeve(fp, IN_QUALITY, 1)
            comps = list(IN_VALUE) + list(IN_QUALITY)
        notes.append(f"{uni}: {n_us} EDGAR / {n_in} NSE symbols with facts; "
                     f"{'point-in-time' if member is not None else 'TODAY’S (biased)'} membership")
        price_ctrl = {c: sig[c].loc[me] for c in EXISTING}
        cost = COST.get(uni, 0.003)
        for name in ["sue", "value", "quality"] + comps:
            m = evaluate(fp[name], fwd, liq, price_ctrl, cost, member_me=member)
            sm = summarize(m)
            if name in ("sue", "value", "quality") and sm:
                full = dict(price_ctrl, **{k: fp[k] for k in ("sue", "value", "quality")
                                           if k != name})
                sm["fm_t_full"] = summarize(evaluate(fp[name], fwd, liq, full, cost,
                                                     member_me=member)).get("fm_t_nw", np.nan)
            results.setdefault(name, {})[uni] = sm
            if sm:
                table.append({"signal": name, "universe": uni, **sm})
        print(f"{uni}: done", flush=True)
    return results, pd.DataFrame(table), notes


def report_pit(results, table, notes, start):
    today = dt.date.today().isoformat()
    os.makedirs(OUT, exist_ok=True)
    table.to_csv(f"{OUT}/ev19-20-{today}.csv", index=False)
    L = [f"# EV-19 / EV-20 report — {today}", "",
         "Point-in-time fundamentals through the EV-0 harness (`ops-tools/ev0.py run-pit`). "
         f"Monthly from {start}; forward return = next month's total return; liquidity floor and "
         f"costs {COST} as EV-0. Facts used only when FIRST filed strictly before the month-end; "
         "restatements ignored; EPS/share counts split-adjusted to today's basis via "
         "corporate_actions.", "",
         "- **EV-19 SUE**: (EPS_q − EPS_q−4) / σ(prior 8 seasonal diffs, ≥4); Q4 and other "
         "YTD-only quarters derived as YTD − prior YTD; signal live for 91 days after the filing date. "
         "earnings_calendar (schema 19) holds only forward dates from 2026-10-27, so `filed` "
         "(10-Q/10-K or NSE results date — usually AFTER the press-release announcement) is the event date: "
         "the measured drift is post-filing, a conservative lower bound on PEAD.",
         "- **EV-20 sleeves** (screener pct-rank style, NA = 0.5): US value = E/P, B/P, EBIT/EV, FCF/P; "
         "US quality = ROE, op margin, −D/E. India (NSE results, no balance sheet): value = E/P only; "
         "quality = EBIT margin, interest cover — a PARTIAL sleeve.",
         "- Verdict: §5 criteria per universe; **flagship** = SP500 + NIFTY500 jointly (criterion 2 needs both). "
         "FM t = price controls (the five composite price inputs); FM t full = price + the other "
         "EV-19/20 sleeves.",
         "- Survivorship: prices/facts exist only for TODAY's constituents of some universe; SP500 "
         "membership is point-in-time (EV-1) but removed names without prices drop out. NIFTY500 and "
         "SP400/SP600 use today's constituents (biased).", ""]
    L += [f"- {n}" for n in notes] + [""]
    L += ["| signal | universe | verdict (this universe) | months | names | IC | NW t | IC 1st/2nd half | "
          "Q5−Q1 /yr | net /yr | turnover | FM t | FM t full |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, per in results.items():
        for uni, s in per.items():
            if not s:
                continue
            v, crit = verdict({uni: s})
            fails = ", ".join(k for k, ok in crit.items() if not ok) or "—"
            full = s.get("fm_t_full", np.nan)
            L.append(f"| {name} | {uni} | **{v}** ({'fails ' + fails if v == 'FAIL' else 'all 4'}) | "
                     f"{s['months']} | {s['names']} | {s['ic_mean']:.3f} | {s['ic_t_nw']:.1f} | "
                     f"{s['ic_h1']:.3f}/{s['ic_h2']:.3f} | {s['spread_ann']:.1%} | {s['net_ann']:.1%} | "
                     f"{s['turnover']:.0%} | {s['fm_t_nw']:.1f} | "
                     f"{'' if pd.isna(full) else f'{full:.1f}'} |")
        flag = {u: s for u, s in per.items() if u in PIT_FLAGSHIP and s}
        if flag:
            v, crit = verdict(flag)
            L.append(f"| {name} | **flagship {'+'.join(flag)}** | **{v}** · "
                     f"{' · '.join(k + (' ✓' if ok else ' ✗') for k, ok in crit.items())} "
                     "| | | | | | | | | | |")
    path = f"{OUT}/report-ev19-20-{today}.md"
    open(path, "w").write("\n".join(L) + "\n")
    print(path)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--start", default="1999-01-01")
    r = sub.add_parser("run")
    r.add_argument("--start", default="2001-01-01")
    r.add_argument("--price", nargs="+", default=["adj_close", "close"])
    sub.add_parser("export-pit", help="EV-19/20: financial_facts with durations + splits")
    rp = sub.add_parser("run-pit", help="EV-19 SUE + EV-20 value/quality sleeves")
    rp.add_argument("--start", default="2005-01-01")
    a = ap.parse_args()
    if a.cmd == "export":
        export(a.start)
    elif a.cmd == "export-pit":
        export_pit()
    elif a.cmd == "run-pit":
        res, tab, notes = run_pit(a.start)
        report_pit(res, tab, notes, a.start)
    else:
        res, tab = run(a.price, a.start)
        report(res, tab, a.start)


if __name__ == "__main__":
    main()
