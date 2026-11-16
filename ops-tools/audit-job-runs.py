#!/usr/bin/env python3
"""OS-14: audit that every CronJob records a job_runs row in the last 7 days.

Usage:
  export KUBECONFIG=~/.kube/pi-cluster.yaml
  python3 ops-tools/audit-job-runs.py        # list jobs missing a job_runs row
  PSQL_CMD='kubectl -n market-data exec -i timescaledb-0 -- env PGPASSWORD=… psql …' \
    python3 ops-tools/audit-job-runs.py --fix   # also backfill fx-fetcher-style gaps noted

Reads the CronJob list from kubectl, then queries app.job_runs (via PSQL_CMD,
like ev0.py/ml1.py) for a row per job in the last 7 days. Exits nonzero if any
job is missing — suitable as a CI/check step.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys

WINDOW_DAYS = 7


def kubectl(args):
    out = subprocess.run(["kubectl"] + args, capture_output=True, text=True,
                         check=True).stdout
    return out


def list_cronjobs():
    out = kubectl(["-n", "market-data", "get", "cronjobs", "-o", "json"])
    data = json.loads(out)
    return [i["metadata"]["name"] for i in data["items"]]


def job_runs_recent(job_name, psql_cmd):
    """Return True if app.job_runs has a row for job_name in the last WINDOW_DAYS."""
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    sql = (f"SELECT count(*) FROM app.job_runs "
           f"WHERE job_name = {job_name!r} AND started_at >= '{since}'")
    raw = subprocess.run(psql_cmd + " -tAc " + repr(sql), shell=True,
                         capture_output=True, text=True, check=True).stdout.strip()
    return int(raw) > 0


def main():
    ap = argparse.ArgumentParser(description="Audit job_runs coverage for all CronJobs")
    ap.add_argument("--fix", action="store_true",
                    help="print the jobs missing job_runs (no auto-fix; guidance only)")
    a = ap.parse_args()
    psql_cmd = os.environ.get("PSQL_CMD")
    if not psql_cmd:
        sys.exit("set PSQL_CMD (see ml1.py/ev0.py docstring)")

    jobs = list_cronjobs()
    print(f"{len(jobs)} CronJobs found in market-data namespace")
    missing = []
    for j in jobs:
        try:
            ok = job_runs_recent(j, psql_cmd)
        except Exception as exc:  # DB may not have job_runs yet (schema 23)
            print(f"  {j}: CHECK FAILED ({exc})")
            missing.append(j)
            continue
        print(f"  {j}: {'OK' if ok else 'MISSING job_runs row (last {WINDOW_DAYS}d)'}")
        if not ok:
            missing.append(j)

    print(f"\n{len(missing)} job(s) missing a job_runs row in the last {WINDOW_DAYS} days:")
    for j in missing:
        print(f"  - {j}")
    if missing:
        print("\nGuidance: add a job_runs INSERT (job_name, status='ok', rows_written) "
              "at the end of that job's script, mirroring other fetchers, then re-run this audit.")
        sys.exit(1)
    print("\nAll jobs record job_runs.")


if __name__ == "__main__":
    main()
