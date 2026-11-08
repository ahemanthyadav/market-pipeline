#!/usr/bin/env python3
"""ML-5 peer clusters — correlation + text-based clusters per symbol.

Workstation runner: fetches stock_prices returns via PSQL_CMD, computes
peer clusters by return correlation within each sector, writes to
app.peer_clusters.

Usage (workstation; DB via $PSQL_CMD like ev0.py):
  PSQL_CMD='kubectl -n market-data exec -i timescaledb-0 -- env PGPASSWORD=… psql -U postgres -d postgres' \\
    python ops-tools/ml5.py run --universe SP500 --method correlation --n-clusters 10
  PSQL_CMD='…' python ops-tools/ml5.py show --limit 20
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ev0  # reuse month_ends, etc.

OUT = "out/ml5"


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


def write_clusters(rows, batch_size=500):
    """Insert cluster rows into app.peer_clusters in batches (idempotent). Returns rows written."""
    if not rows:
        return 0
    n = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        values = []
        for r in chunk:
            symbol = _q(r["symbol"])
            cluster = int(r["cluster"])
            method = _q(r["method"])
            as_of = _q(str(r["as_of"]))
            values.append(f"({symbol}, {cluster}, {method}, {as_of})")
        sql = (f"INSERT INTO app.peer_clusters (symbol, cluster, method, as_of) "
               f"VALUES {', '.join(values)} "
               f"ON CONFLICT (symbol, method, as_of) DO NOTHING")
        raw = psql(sql).strip()
        if raw.startswith("INSERT 0 "):
            n += int(raw.split()[-1])
    return n


# ---------------------------------------------------------------- fetch + cluster

def fetch_universe_symbols(universe):
    """Return (symbols, sectors) for active constituents of a universe."""
    sql = (f"SELECT symbol, sector FROM universe_constituents "
           f"WHERE universe = {_q(universe)} AND active = true ORDER BY symbol")
    rows = psql(sql)
    if not rows.strip():
        return [], {}
    symbols, sectors = [], {}
    for line in rows.strip().split("\n"):
        parts = line.split(",", 1)
        symbols.append(parts[0])
        sectors[parts[0]] = parts[1] if len(parts) > 1 else None
    return symbols, sectors


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


def correlation_clusters(px_wide, n_clusters=10, min_corr=0.3):
    """Cluster symbols by return correlation using hierarchical clustering.

    Returns dict: symbol -> cluster_id.
    """
    try:
        from scipy.cluster.hierarchy import linkage, fcluster
        from scipy.spatial.distance import squareform
    except ImportError:
        # scipy not available — assign all symbols to cluster 1
        return {sym: 1 for sym in px_wide.columns}

    # Correlation matrix
    corr = px_wide.corr(method="spearman").fillna(0)
    # Distance = 1 - |corr|
    dist = 1 - corr.abs().values
    dist = np.clip(dist, 0, 2)  # ensure valid distances
    dist = squareform(dist, checks=False)

    Z = linkage(dist, method="average")
    labels = fcluster(Z, t=n_clusters, criterion="maxclust")

    return {sym: int(label) for sym, label in zip(px_wide.columns, labels)}


def run(universe="SP500", method="correlation", n_clusters=10, days=252):
    """Run peer clustering for a universe. Returns total rows written."""
    symbols, sectors = fetch_universe_symbols(universe)
    if not symbols:
        print(f"ML-5: no symbols found for universe {universe}", flush=True)
        return 0

    print(f"ML-5: {len(symbols)} symbols in {universe}", flush=True)

    rets = fetch_returns(symbols, days=days)
    if rets.empty:
        print(f"ML-5: no return data", flush=True)
        return 0

    # Pivot to wide format
    px_wide = rets.pivot(index="ts", columns="symbol", values="ret")

    # Cluster within each sector for sector-aware peers
    all_clusters = {}
    cluster_id = 0
    for sector in sorted(set(s for s in sectors.values() if s)):
        syms_in_sector = [s for s in symbols if sectors.get(s) == sector]
        sector_rets = px_wide[syms_in_sector].dropna(axis=1, how="all")
        if len(sector_rets.columns) < 3:
            # Too few symbols — assign all to one cluster
            for s in sector_rets.columns:
                all_clusters[s] = cluster_id
            cluster_id += 1
            continue

        try:
            sector_clusters = correlation_clusters(sector_rets, n_clusters=min(n_clusters, len(sector_rets.columns)))
        except Exception:
            # Fallback: all in one cluster
            sector_clusters = {s: 0 for s in sector_rets.columns}

        for s, c in sector_clusters.items():
            all_clusters[s] = cluster_id + c
        cluster_id += n_clusters

    # Symbols without sector data — cluster separately
    no_sector = [s for s in symbols if not sectors.get(s)]
    if no_sector:
        ns_rets = px_wide[no_sector].dropna(axis=1, how="all")
        if len(ns_rets.columns) >= 3:
            try:
                ns_clusters = correlation_clusters(ns_rets, n_clusters=min(n_clusters, len(ns_rets.columns)))
            except Exception:
                ns_clusters = {s: 0 for s in ns_rets.columns}
            for s, c in ns_clusters.items():
                all_clusters[s] = cluster_id + c
            cluster_id += n_clusters
        else:
            for s in ns_rets.columns:
                all_clusters[s] = cluster_id
            cluster_id += 1

    as_of = pd.Timestamp.now(tz="UTC").date().isoformat()
    rows = [{"symbol": s, "cluster": c, "method": method, "as_of": as_of}
            for s, c in all_clusters.items()]

    n = write_clusters(rows)
    print(f"ML-5: {len(rows)} cluster assignments computed, {n} written to app.peer_clusters", flush=True)
    return n


def show(limit=50):
    """List recent cluster assignments from app.peer_clusters."""
    sql = (f"SELECT symbol, cluster, method, as_of FROM app.peer_clusters "
           f"ORDER BY as_of DESC, cluster, symbol LIMIT {limit}")
    rows = psql(sql)
    if not rows.strip():
        print("no clusters")
        return
    print(f"{'symbol':<12} {'cluster':<8} {'method':<15} {'as_of'}")
    print("-" * 50)
    for line in rows.strip().split("\n"):
        parts = line.split(",")
        symbol, cluster, method, as_of = parts[:4]
        print(f"{symbol:<12} {cluster:<8} {method:<15} {as_of}")


def main():
    ap = argparse.ArgumentParser(description="ML-5 peer clusters (shares PSQL_CMD pattern with ev0/ml0/ml1/ml3)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="compute peer clusters + write to app.peer_clusters")
    r.add_argument("--universe", default="SP500", help="universe to cluster")
    r.add_argument("--method", default="correlation", help="clustering method (correlation)")
    r.add_argument("--n-clusters", type=int, default=10, help="number of clusters per sector")
    r.add_argument("--days", type=int, default=252, help="lookback days for returns")
    s = sub.add_parser("show", help="list recent cluster assignments")
    s.add_argument("--limit", type=int, default=50, help="max rows to show")
    a = ap.parse_args()
    cmd = os.environ.get("PSQL_CMD")
    if not cmd:
        sys.exit("set PSQL_CMD (see module docstring)")
    if a.cmd == "run":
        run(universe=a.universe, method=a.method, n_clusters=a.n_clusters, days=a.days)
    elif a.cmd == "show":
        show(limit=a.limit)


if __name__ == "__main__":
    main()
