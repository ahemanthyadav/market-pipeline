#!/usr/bin/env python3
"""ML-3 volatility forecasts — EWMA + GARCH(1,1) + GJR-GARCH baselines.

Workstation runner: fetches stock_prices returns via PSQL_CMD, computes
volatility forecasts, writes to app.vol_forecasts.

Usage (workstation; DB via $PSQL_CMD like ev0.py):
  PSQL_CMD='kubectl -n market-data exec -i timescaledb-0 -- env PGPASSWORD=… psql -U postgres -d postgres' \\
    python ops-tools/ml3.py run --symbols SP500,NIFTY500 --horizon 1 --days 252
  PSQL_CMD='…' python ops-tools/ml3.py show --limit 20
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

# Reuse compute functions from the in-cluster module
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "manifests" / "ml"))
from ml3_volatility import ewma_forecast, garch_forecast, gjr_garch_forecast  # noqa: E402

OUT = "out/ml3"
MODELS = {"ewma": ewma_forecast, "garch": garch_forecast, "gjr_garch": gjr_garch_forecast}


# ---------------------------------------------------------------- DB helpers

def psql(sql, text=True):
    cmd = os.environ.get("PSQL_CMD")
    if not cmd:
        sys.exit("set PSQL_CMD (see module docstring)")
    out = subprocess.run(cmd + " -v ON_ERROR_STOP=1 -At -F, -c " + _q(sql), shell=True,
                         check=True, capture_output=True, text=text)
    return out.stdout


def _q(s):
    return "'" + s.replace("'", "'\"'\"'") + "'"


def write_forecasts(rows, batch_size=500):
    """Insert forecast rows into app.vol_forecasts in batches (idempotent). Returns rows written."""
    if not rows:
        return 0
    n = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        values = []
        for r in chunk:
            symbol = _q(r["symbol"])
            ts = _q(pd.Timestamp(r["ts"]).isoformat())
            model = _q(r["model"])
            horizon = int(r["horizon"])
            forecast = repr(float(r["forecast"]))
            values.append(f"({symbol}, {ts}, {model}, {horizon}, {forecast})")
        sql = (f"INSERT INTO app.vol_forecasts (symbol, ts, model, horizon, forecast) "
               f"VALUES {', '.join(values)} "
               f"ON CONFLICT (symbol, ts, model, horizon) DO NOTHING")
        raw = psql(sql).strip()
        if raw.startswith("INSERT 0 "):
            n += int(raw.split()[-1])
    return n


# ---------------------------------------------------------------- fetch + compute

def fetch_returns(symbols, days=252):
    """Fetch split-adjusted daily returns for symbols from stock_prices."""
    sym_list = ",".join(_q(s) for s in symbols)
    sql = (f"SELECT symbol, ts::date AS ts, COALESCE(adj_close, close) AS close FROM stock_prices "
           f"WHERE symbol IN ({sym_list}) AND ts >= NOW() - INTERVAL '{days} days' "
           f"ORDER BY symbol, ts")
    rows = psql(sql)
    if not rows.strip():
        return pd.DataFrame(columns=["symbol", "ts", "ret"])
    data = [line.split(",") for line in rows.strip().split("\n") if line]
    df = pd.DataFrame(data, columns=["symbol", "ts", "close"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df["ret"] = df.groupby("symbol")["close"].pct_change(fill_method=None)
    return df[["symbol", "ts", "ret"]].dropna()


def run(symbols=None, days=252, horizon=1, models=None):
    """Run volatility forecasts for given symbols. Returns total rows written."""
    if symbols is None:
        # Default: all index tickers from stock_prices
        rows = psql("SELECT DISTINCT symbol FROM stock_prices ORDER BY symbol")
        symbols = [s for s in rows.strip().split("\n") if s]
    else:
        symbols = [s.strip() for s in symbols.split(",")]

    if models is None:
        models = list(MODELS.keys())

    rets = fetch_returns(symbols, days=days)
    if rets.empty:
        print("ML-3: no return data", flush=True)
        return 0

    all_written = 0
    for model_name in models:
        compute_fn = MODELS[model_name]
        print(f"ML-3: computing {model_name} forecasts for {len(symbols)} symbols...", flush=True)
        fc = compute_fn(rets, horizon=horizon)
        if fc.empty:
            print(f"ML-3: {model_name} produced no forecasts", flush=True)
            continue
        n = write_forecasts(fc.to_dict("records"))
        all_written += n
        print(f"ML-3: {model_name} — {len(fc)} forecasts computed, {n} written", flush=True)

    print(f"ML-3: {all_written} total forecasts written to app.vol_forecasts", flush=True)
    return all_written


def show(limit=20):
    """List recent forecasts from app.vol_forecasts."""
    sql = (f"SELECT symbol, ts, model, horizon, forecast FROM app.vol_forecasts "
           f"ORDER BY ts DESC LIMIT {limit}")
    rows = psql(sql)
    if not rows.strip():
        print("no forecasts")
        return
    print(f"{'symbol':<12} {'ts':<20} {'model':<12} {'horizon':<8} {'forecast'}")
    print("-" * 70)
    for line in rows.strip().split("\n"):
        parts = line.split(",")
        symbol, ts, model, horizon, forecast = parts[:5]
        print(f"{symbol:<12} {ts:<20} {model:<12} {horizon:<8} {forecast}")


def main():
    ap = argparse.ArgumentParser(description="ML-3 volatility forecasts (shares PSQL_CMD pattern with ev0/ml0/ml1)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="compute volatility forecasts + write to app.vol_forecasts")
    r.add_argument("--symbols", default=None, help="comma-separated symbol list (default: all in stock_prices)")
    r.add_argument("--days", type=int, default=252, help="lookback days for returns")
    r.add_argument("--horizon", type=int, default=1, help="forecast horizon in days")
    r.add_argument("--models", default="ewma,garch,gjr_garch", help="comma-separated model list")
    s = sub.add_parser("show", help="list recent forecasts from app.vol_forecasts")
    s.add_argument("--limit", type=int, default=20, help="max rows to show")
    a = ap.parse_args()
    cmd = os.environ.get("PSQL_CMD")
    if not cmd:
        sys.exit("set PSQL_CMD (see module docstring)")
    if a.cmd == "run":
        run(symbols=a.symbols, days=a.days, horizon=a.horizon,
            models=a.models.split(",") if a.models else None)
    elif a.cmd == "show":
        show(limit=a.limit)


if __name__ == "__main__":
    main()
