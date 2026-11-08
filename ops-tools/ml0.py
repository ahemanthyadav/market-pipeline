"""ML-0 experiment infrastructure — shares the EV-0 harness.

Walk-forward splitter, leakage test, trial budget, and ml_runs tracking
for signal experiments. All writes go to app.ml_runs via pipeline_rw.

Usage (workstation; DB via $PSQL_CMD like ev0.py):
  PSQL_CMD='kubectl -n market-data exec -i timescaledb-0 -- env PGPASSWORD=… psql -U postgres -d postgres' \\
    python ops-tools/ml0.py run "momentum-12m" ret_12m --price adj_close --start 2001-01-01 --budget 20
  python ops-tools/ml0.py budget --max 20

Writes one row to app.ml_runs per experiment run. Trial budget counts existing
runs and refuses to start a new one when the cap is reached (prevents
p-hacking by limiting total experiments).
"""
import argparse
import datetime as dt
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ev0  # same directory

import numpy as np
import pandas as pd

OUT = "out/ev0"
DEFAULT_BUDGET = 20          # max total experiments (p-hacking guard)
MIN_TRAIN_MONTHS = 24        # min training window before first test month
STEP_MONTHS = 12             # walk-forward step size


# ---------------------------------------------------------------- ml_runs tracking

def _git_sha():
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return "unknown"


def _params_hash(params):
    return hashlib.sha256(repr(sorted(params.items())).encode()).hexdigest()[:16]


def ml_runs_count(cmd):
    """Number of existing experiment runs (for trial budget)."""
    raw = subprocess.run(
        cmd + " -t -A -v ON_ERROR_STOP=1 -c \"SELECT count(*) FROM app.ml_runs\"",
        shell=True, check=True, capture_output=True,
    ).stdout.decode().strip()
    return int(raw)


def write_ml_run(cmd, name, params, data_hash_val, seed, metrics, artifact_path, artifact_hash):
    """Insert one row into app.ml_runs. Returns run_id."""
    run_id = f"{name}-{_params_hash(params)}-{dt.date.today().isoformat()}"
    started = dt.datetime.now(dt.timezone.utc).isoformat()

    def q(s):
        return "'" + str(s).replace("'", "''") + "'"

    # Serialize complex objects to JSON for SQL storage
    params_json = json.dumps(params, default=str)
    metrics_json = json.dumps(metrics, default=str)

    full_sql = (
        f"INSERT INTO app.ml_runs "
        f"(run_id, name, git_sha, params, data_hash, seed, metrics, "
        f" artifact_path, artifact_hash, started_at, ended_at) "
        f"VALUES ({q(run_id)}, {q(name)}, {q(_git_sha())}, "
        f"{q(params_json)}, {q(data_hash_val)}, {seed}, {q(metrics_json)}, "
        f"{q(artifact_path)}, {q(artifact_hash)}, {q(started)}, {q(started)}) "
        f"RETURNING run_id;"
    )
    raw = subprocess.run(
        cmd + f" -t -A -v ON_ERROR_STOP=1 -c {ev0._q(full_sql)}",
        shell=True, check=True, capture_output=True,
    ).stdout.decode().strip()
    return raw if raw else run_id


# ---------------------------------------------------------------- walk-forward splitter

def walk_forward_splits(me, n_splits=5, min_train_months=MIN_TRAIN_MONTHS, step_months=STEP_MONTHS):
    """Expanding-window walk-forward splits.

    Yields (train_months, test_months) pairs where train grows and test is the
    next step_months. No overlap, no look-ahead: test months always come after
    all train months. Pass n_splits=None to expand across the entire timeline.
    """
    me = pd.DatetimeIndex(sorted(me))
    n = len(me)
    splits = []
    train_end = min_train_months
    max_iters = n if n_splits is None else n_splits
    for _ in range(max_iters):
        test_start = train_end
        test_end = min(train_end + step_months, n)
        if test_start >= n or test_end <= test_start:
            break
        splits.append((me[:train_end], me[test_start:test_end]))
        train_end = test_end
    return splits


# ---------------------------------------------------------------- leakage test

def leakage_test(sig, fwd, liq, controls=None, cost=0.002):
    """Check for look-ahead bias: if |IC| ≈ 1.0, the signal IS the forward
    return (sig[t] = fwd[t]), which means it uses price[t+1] — classic
    look-ahead bias.

    Returns (leaked: bool, detail: str). leaked=True means the signal FAILS
    the leakage test (it uses future data).
    """
    controls = controls or {}
    base = ev0.evaluate(sig, fwd, liq, controls, cost)
    if base.empty:
        return False, "no data"
    base_ic = abs(base.ic.mean())

    if base_ic > 0.9:
        return True, f"IC {base_ic:.3f} > 0.9 (signal is the forward return - look-ahead bias)"
    return False, f"IC {base_ic:.3f} <= 0.9 (no obvious look-ahead)"


# ---------------------------------------------------------------- data hash

def data_hash():
    """SHA256 of the prices parquet (for reproducibility tracking)."""
    path = f"{OUT}/prices.parquet"
    if not os.path.exists(path):
        return "missing"
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


# ---------------------------------------------------------------- run with ML-0 tracking

def ml0_run(cmd, name, signal, price_col="adj_close", start="2001-01-01",
            budget=DEFAULT_BUDGET, min_train_months=MIN_TRAIN_MONTHS,
            step_months=STEP_MONTHS):
    """Run one ML-0 experiment: walk-forward CV + leakage test + ml_runs write."""

    # --- trial budget check ---
    n = ml_runs_count(cmd)
    if n >= budget:
        print(f"ML-0 BUDGET EXCEEDED: {n}/{budget} runs used. Aborting.")
        return None

    # --- export data (idempotent: re-export if parquet missing) ---
    ev0.export(start)

    # --- load and build panels ---
    df = pd.read_parquet(f"{OUT}/prices.parquet")
    df = df[df.d >= pd.Timestamp(start) - pd.Timedelta(days=400)]
    results = []
    splits = []

    for uni, g in df.groupby("universe"):
        wide = {c: g.pivot(index="d", columns="symbol", values=c)
                for c in ("close", "adj_close", "volume")}
        tr = wide["adj_close"].fillna(wide["close"]).ffill(limit=5)
        px = wide[price_col].fillna(wide["close"]).ffill(limit=5) \
            if price_col == "adj_close" else wide["close"].ffill(limit=5)
        sig = ev0.signals(px, wide["volume"], wide["close"])
        me = ev0.month_ends(px.index)
        me = me[me >= pd.Timestamp(start)]
        fwd = (tr.loc[me].shift(-1) / tr.loc[me] - 1)
        liq = sig["_liq"].loc[me]
        member = ev0.membership_mask(me, px.columns, uni)

        # --- leakage test ---
        leaked, detail = leakage_test(sig[signal].loc[me], fwd, liq)
        print(f"{uni}: leakage test {'FAILED' if leaked else 'passed'} — {detail}", flush=True)
        if leaked:
            print(f"ML-0 ABORT {uni}: signal {signal} leaks future information", flush=True)
            continue

        # --- walk-forward CV ---
        splits = walk_forward_splits(me, n_splits=None, min_train_months=min_train_months,
                                     step_months=step_months)
        split_results = []
        for train_me, test_me in splits:
            # Train on train months, test on test months
            s_train = sig[signal].loc[train_me]
            f_train = fwd.loc[train_me]
            l_train = liq.loc[train_me]
            s_test = sig[signal].loc[test_me]
            f_test = fwd.loc[test_me]
            l_test = liq.loc[test_me]
            m_train_mask = member.loc[train_me] if member is not None else None
            m_test_mask = member.loc[test_me] if member is not None else None

            m_train = ev0.evaluate(s_train, f_train, l_train, {},
                                   cost=ev0.COST.get(uni, 0.003), member_me=m_train_mask)
            m_test = ev0.evaluate(s_test, f_test, l_test, {},
                                  cost=ev0.COST.get(uni, 0.003), member_me=m_test_mask)
            if not m_train.empty and not m_test.empty:
                split_results.append({
                    "train_start": str(m_train.index.min().date()),
                    "train_end": str(m_train.index.max().date()),
                    "test_start": str(m_test.index.min().date()),
                    "test_end": str(m_test.index.max().date()),
                    "train_ic": m_train.ic.mean(),
                    "test_ic": m_test.ic.mean(),
                    "train_net": m_train.net.mean(),
                    "test_net": m_test.net.mean(),
                    "train_turnover": m_train.turnover.mean(),
                    "test_turnover": m_test.turnover.mean(),
                })

        # --- full-period evaluate (for ml_runs metrics) ---
        controls = {c: sig[c].loc[me] for c in ev0.EXISTING if c != signal}
        m_full = ev0.evaluate(sig[signal].loc[me], fwd, liq, controls,
                              cost=ev0.COST.get(uni, 0.003), member_me=member)
        sm = ev0.summarize(m_full)

        results.append({
            "universe": uni,
            "signal": signal,
            "price_col": price_col,
            "splits": split_results,
            "summary": sm,
        })

    # --- write to ml_runs ---
    metrics = {"universes": len(results), "signal": signal, "price_col": price_col}
    for r in results:
        if r["summary"]:
            metrics[r["universe"]] = r["summary"]

    artifact_path = f"{OUT}/ml0-{name}-{dt.date.today().isoformat()}.json"
    import json
    with open(artifact_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    artifact_hash = hashlib.sha256(open(artifact_path, "rb").read()).hexdigest()[:16]

    run_id = write_ml_run(
        cmd, name, {"signal": signal, "price_col": price_col, "start": start,
                    "budget": budget},
        data_hash(), 0, metrics, artifact_path, artifact_hash,
    )
    print(f"ML-0 run recorded: {run_id} ({len(splits)} walk-forward splits, "
          f"{n + 1}/{budget} budget used)", flush=True)
    return run_id


def budget(cmd, max_budget=DEFAULT_BUDGET):
    """Show trial budget remaining."""
    n = ml_runs_count(cmd)
    remaining = max_budget - n
    print(f"ML-0 budget: {n}/{max_budget} used, {remaining} remaining")
    if remaining <= 0:
        print("ML-0 BUDGET EXCEEDED — no new experiments allowed.")
    return remaining


def main():
    ap = argparse.ArgumentParser(description="ML-0 experiment infrastructure (shares EV-0 harness)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run one ML-0 experiment with walk-forward CV + leakage test")
    r.add_argument("name", help="experiment name")
    r.add_argument("signal", help="signal name (ret_12m, ret_6m, etc.)")
    r.add_argument("--price", default="adj_close", help="price column (adj_close or close)")
    r.add_argument("--start", default="2001-01-01", help="start date (ISO)")
    r.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="max total experiments")

    b = sub.add_parser("budget", help="show trial budget remaining")
    b.add_argument("--max", type=int, default=DEFAULT_BUDGET, help="max total experiments")

    a = ap.parse_args()
    cmd = os.environ.get("PSQL_CMD")
    if not cmd:
        sys.exit("set PSQL_CMD (see module docstring)")

    if a.cmd == "run":
        ml0_run(cmd, a.name, a.signal, price_col=a.price, start=a.start, budget=a.budget)
    elif a.cmd == "budget":
        budget(cmd, max_budget=a.max)


if __name__ == "__main__":
    main()
