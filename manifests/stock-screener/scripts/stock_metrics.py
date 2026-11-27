"""Price-derived daily metrics (Phase F, job 1 of 2).

Per active symbol, from the last 260 bars of stock_prices, computes the §6 metric
set and writes (metric, value) rows keyed to the symbol's latest bar date (and to any of the
five newest bars that has no row yet — see bars_to_write):
close, ret_1m, ret_6m, ret_12m, vol_180d, max_dd_252d, pos_52w, sma50_ratio,
sma200_ratio, dol_vol_20d. Missing window (new listing) → metric omitted.
"""
import os
import sys

import pandas as pd
import psycopg

BARS = 260


def pct_rank_scale(v):
    return v


def compute(df):
    """df indexed by ts ascending, columns [close, volume] (and optional adj_close).
    Returns {metric: val}. Uses adj_close when present for total-return / risk series
    (ret_1m, ret_6m, ret_12m, vol_180d, max_dd_252d; DG-07 / DF-06), falling back to close."""
    out = {}
    c = df["close"]
    px = df["adj_close"].combine_first(c) if "adj_close" in df.columns else c
    out["close"] = float(c.iloc[-1])
    for name, lag in (("ret_1m", 21), ("ret_6m", 126), ("ret_12m", 252)):
        if len(px) > lag and px.iloc[-1 - lag] and not pd.isna(px.iloc[-1 - lag]):
            out[name] = float(px.iloc[-1] / px.iloc[-1 - lag] - 1)
    if len(px) > 252 and px.iloc[-1 - 252] and not pd.isna(px.iloc[-1 - 252]) and not pd.isna(px.iloc[-1 - 21]):
        out["ret_12_1"] = float(px.iloc[-1 - 21] / px.iloc[-1 - 252] - 1)
    ret = px.pct_change().dropna()
    if len(ret) >= 120:
        out["vol_180d"] = float(ret.tail(180).std() * (252 ** 0.5))
    if len(c) >= 60:
        w_px = px.tail(252)
        run_max = w_px.cummax()
        out["max_dd_252d"] = float((w_px / run_max - 1).min())
        w = c.tail(252)
        lo, hi = float(w.min()), float(w.max())
        if hi > lo:
            out["pos_52w"] = (float(c.iloc[-1]) - lo) / (hi - lo)
        if hi > 0:
            out["near_high"] = float(c.iloc[-1]) / hi
    for name, n in (("sma50_ratio", 50), ("sma200_ratio", 200)):
        if len(c) >= n:
            out[name] = float(c.iloc[-1] / c.tail(n).mean())
    if len(df) >= 5 and "volume" in df:
        out["dol_vol_20d"] = float((df["close"] * df["volume"]).tail(20).mean())
    return out


HEAL_BARS = 5


def bars_to_write(bar_ts, have_ts, heal=HEAL_BARS):
    """Pure. Which bars get a metric row this run: always the newest, plus any of the `heal`
    newest that has none yet. Rows used to be written at the newest bar only, so a symbol whose
    bar arrived a run late (2026-11-25/26: Yahoo served ~40 % of NSE names with an empty close on
    the newest day) never got a row for the day it skipped. An existing older row is left alone."""
    recent = list(bar_ts)[-heal:]
    have = set(have_ts)
    return [t for t in recent[:-1] if t not in have] + recent[-1:]


def main() -> int:
    limit = int(os.environ.get("UNIVERSE_LIMIT", "0") or 0) or None
    conn = psycopg.connect(
        host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )
    conn.autocommit = True  # no idle transaction across the symbol loop
    with conn.cursor() as cur:
        cur.execute("SELECT symbol FROM universe_constituents WHERE active ORDER BY 1")
        symbols = [r[0] for r in cur.fetchall()]
    if limit:
        symbols = symbols[:limit]
    computed, skipped, written, healed = 0, 0, 0, 0
    for sym in symbols:
        with conn.cursor() as cur:
            cur.execute("SELECT ts, close, volume, adj_close FROM stock_prices WHERE symbol=%s "
                        "ORDER BY ts DESC LIMIT %s", (sym, BARS + HEAL_BARS))
            rows = cur.fetchall()
            have = []
            if len(rows) >= 5:
                cur.execute("SELECT DISTINCT ts FROM stock_metrics_daily WHERE symbol=%s AND ts >= %s",
                            (sym, rows[min(HEAL_BARS, len(rows)) - 1][0]))
                have = [pd.Timestamp(r[0]) for r in cur.fetchall()]
        if len(rows) < 5:
            skipped += 1
            continue
        df = pd.DataFrame([(r[0], r[1], r[2], r[3] if len(r) > 3 else None) for r in reversed(rows)],
                           columns=["ts", "close", "volume", "adj_close"]).set_index("ts")
        todo = [(t, compute(df.loc[:t].tail(BARS))) for t in bars_to_write(list(df.index), have)]
        todo = [(t, m) for t, m in todo if m]
        if not todo:
            skipped += 1
            continue
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    for t, metrics in todo:
                        ts = t.to_pydatetime() if hasattr(t, "to_pydatetime") else t
                        for metric, val in metrics.items():
                            cur.execute("DELETE FROM stock_metrics_daily WHERE symbol=%s "
                                        "AND ts=%s AND metric=%s", (sym, ts, metric))
                            cur.execute("INSERT INTO stock_metrics_daily (symbol, ts, metric, value) "
                                        "VALUES (%s,%s,%s,%s)", (sym, ts, metric, val))
                            written += 1
            computed += 1
            healed += len(todo) - 1
        except psycopg.OperationalError as exc:
            print(f"WARN {sym} conn dropped ({exc}); reconnecting, skipping sym", flush=True)
            skipped += 1
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            conn = psycopg.connect(
                host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
                user=os.environ.get("PGUSER", "postgres"),
                password=os.environ["PGPASSWORD"],
                dbname=os.environ.get("PGDATABASE", "postgres"),
            )
            conn.autocommit = True
    conn.close()
    print(f"METRICS-OK computed={computed} skipped={skipped} rows={written} healed_days={healed}", flush=True)
    return 0 if computed else 1


if __name__ == "__main__":
    from mdjob import run_main   # one job_runs row per execution; never fails the job
    sys.exit(run_main("stock-metrics", main))
