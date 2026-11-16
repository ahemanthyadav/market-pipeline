#!/usr/bin/env python3
"""OS-16: weekly size + disk baseline — DB size, top-10 tables, TX2 disk %.

Appends one row per run to app.disk_baseline (schema key 27) so growth is a
trend, not a snapshot. Complements OS-4 (DB health panels).

DB size + top tables come from TimescaleDB SQL; TX2 disk % come from env vars
populated by a tx2 host cron reading `df` (see ops manual §12 / pending-tasks).

Usage:
  export KUBECONFIG=~/.kube/pi-cluster.yaml
  # workstation (DB size + top tables only; disk % NULL):
  PSQL_CMD='kubectl -n market-data exec -i timescaledb-0 -- env PGPASSWORD=… psql …' \
    python3 ops-tools/disk-baseline.py
  # on tx2 host (full, incl. disk %):
  TX2_EMMC_PCT=60 TX2_SSD_PCT=16 \
    PSQL_CMD='kubectl -n market-data exec -i timescaledb-0 -- env PGPASSWORD=… psql …' \
    python3 ops-tools/disk-baseline.py
"""
import argparse
import os
import subprocess
import sys


def psql_one_value(sql, psql_cmd):
    raw = subprocess.run(psql_cmd + " -tAc " + repr(sql), shell=True,
                         capture_output=True, text=True, check=True).stdout.strip()
    return raw or "0"


def insert_baseline(db_size, top_table, top_table_bytes, emmc_pct, ssd_pct, psql_cmd):
    emmc = "NULL" if emmc_pct is None else repr(float(emmc_pct))
    ssd = "NULL" if ssd_pct is None else repr(float(ssd_pct))
    sql = (f"INSERT INTO app.disk_baseline "
           f"(db_size_bytes, top_table, top_table_bytes, tx2_emmc_pct, tx2_ssd_pct) "
           f"VALUES ({int(db_size)}, {top_table!r}, {int(top_table_bytes)}, {emmc}, {ssd})")
    subprocess.run(psql_cmd + " -c " + repr(sql), shell=True, check=True,
                   capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser(description="Record weekly DB + disk baseline")
    ap.add_argument("--emmc-pct", type=float, default=None,
                    help="TX2 eMMC usage % (from host df)")
    ap.add_argument("--ssd-pct", type=float, default=None,
                    help="TX2 SSD (pgdata) usage % (from host df)")
    a = ap.parse_args()
    psql_cmd = os.environ.get("PSQL_CMD")
    if not psql_cmd:
        sys.exit("set PSQL_CMD (see ml1.py/ev0.py docstring)")

    db_size = int(psql_one_value("SELECT pg_database_size('postgres')", psql_cmd))
    # Top-10 tables by total size (excluding indexes/toast noise is handled by pg_total_relation_size)
    rows = subprocess.run(psql_cmd + " -tAc " + repr(
        "SELECT relname, pg_total_relation_size(oid) FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname NOT IN ('pg_catalog','information_schema') "
        "AND c.relkind IN ('r','p') ORDER BY 2 DESC LIMIT 10"),
        shell=True, capture_output=True, text=True, check=True).stdout
    top_table, top_bytes = "", 0
    for line in rows.strip().splitlines():
        if not line.strip():
            continue
        name, size = line.split("|", 1)
        size = int(size)
        if size > top_bytes:
            top_table, top_bytes = name, size
    insert_baseline(db_size, top_table or "(none)", top_bytes,
                    a.emmc_pct, a.ssd_pct, psql_cmd)
    print(f"disk_baseline: db_size={db_size} top_table={top_table} "
          f"top_table_bytes={top_bytes} emmc={a.emmc_pct} ssd={a.ssd_pct}")


if __name__ == "__main__":
    main()
