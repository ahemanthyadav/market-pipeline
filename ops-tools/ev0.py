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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--start", default="1999-01-01")
    r = sub.add_parser("run")
    r.add_argument("--start", default="2001-01-01")
    r.add_argument("--price", nargs="+", default=["adj_close", "close"])
    a = ap.parse_args()
    if a.cmd == "export":
        export(a.start)
    else:
        res, tab = run(a.price, a.start)
        report(res, tab, a.start)


if __name__ == "__main__":
    main()
