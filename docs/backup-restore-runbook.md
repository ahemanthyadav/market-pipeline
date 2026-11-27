# Backup & Restore Runbook

> **Single source of truth for every backup in the cluster:** k3s control-plane datastore (`state.db`) and TimescaleDB (`pg_dump`).
> **Last verified: 2026-11-26** (rewritten for the k3s-cp control plane; both restores rehearsed that day). Commands are copy-paste ready. No IP addresses — node names only (real values in gitignored `.hosts.env`).
> Secrets never enter tracked files or chat output; use `@@PW@@` token via `runcmd.py` / `putfile.py`.

## 1. Backup overview

Two independent backups run on different schedules to different destinations:

| Backup | What it protects | Source | Schedule | Destination | Retention |
|---|---|---|---|---|---|
| **k3s control-plane** (`pull-k3s-backup.sh`) | Cluster config: 36 CronJobs, secrets, ConfigMaps, Deployments, node join tokens, TLS certs | k3s-cp (node `minipc`) `state.db` (SQLite via kine; archive ~1.5 MB) | Workstation cron 03:30 local | Workstation `out/backups/k3s/` (primary DR) + pi3 `~/k3s-cp-backups/` (fast-path) | 14 (workstation) / 7 (pi3) |
| **TimescaleDB** (`pg-backup` CronJob) | Market data: all prices, fundamentals, sentiment, scores, etc. | TimescaleDB on tx2 (`sda4` SSD) | 01:30 America/Los_Angeles | pi3 `/var/lib/market-data/backups/` + workstation via `pull-backups.py` | 7 daily |

The control-plane backup is **tiny** (~1.5 MB compressed, keys included) because it holds cluster *configuration*, not market data. The TimescaleDB dump is **~578 MB** (2026-11-26) because it holds all market data.

**Both workstation pulls are watched from outside the workstation** (since 2026-11-26): each successful pull touches a stamp on pi0 (`ops-tools/backup-stamp.sh`), and pi0's `nodewatch.sh` sends a Telegram alert when a stamp is missing or older than 36 h. Before that, both pulls were dead from 2026-11-12 to 2026-11-26 without anyone noticing (cron ran them from a folder that no longer existed, and the k3s script had a quoting bug).

**Failure domains covered:**
- k3s-cp (N100) disk/datastore loss → control-plane backup restores cluster config
- pi3 disk loss → TimescaleDB dump on pi3 SSD lost, but workstation copy survives
- Site-wide power loss → all nodes down together; workstation copy is the off-cluster DR copy (pi0 watcher also goes down — see §7)
- TX2 SSD failure → TimescaleDB data lost unless pg-backup copy on pi3/workstation exists

---

## 2. k3s control-plane backup (k3s-cp → workstation + pi3)

### What's backed up
`/var/lib/rancher/k3s/server/db/state.db` (SQLite via kine), plus:
- `/var/lib/rancher/k3s/server/token` — node join token
- `/var/lib/rancher/k3s/server/tls` — cluster CA + keys
- `/var/lib/rancher/k3s/server/cred` — credentials

**Mode 600.** This archive holds the cluster's root keys — anyone who can read it has full admin over the cluster.

### How it runs
`ops-tools/pull-k3s-backup.sh` (workstation cron 03:30 local):

1. Key-auth SSH to k3s-cp as `minipc` (no password, no `runcmd.py`); the address comes from `K3S_CP_IP` in `.hosts.env`. There is no load gate any more — that protected the 1 GB pi2.
2. On k3s-cp: Python `sqlite3` online-backup API → consistent `state.db` copy while k3s keeps running
3. `PRAGMA integrity_check` + row count
4. Copies `token`, `tls`, `cred`; tars + gzips to `/tmp/k3s-cp-backup.tar.gz` on k3s-cp, readable only by `minipc`
5. `scp` to the workstation `out/backups/k3s/k3s-cp-backup-<UTC date>.tar.gz`, sha256 compared with the remote value
6. Remote `/tmp` copy deleted
7. `putfile.py` pushes the same archive to pi3 at `~/k3s-cp-backups/` (dir 700, files 600, sha256 re-verified on pi3, keeps 7). It lives in the pi3 user's home, never under `/var/lib/market-data` — that tree is mounted as root into `pg-backup`/`restore-drill`, so the token and CA keys would be readable from those pods.
8. Workstation keeps 14; rotation deletes older
9. `backup-stamp.sh k3s-backup` → stamp on pi0 for nodewatch

### Where copies live
| Copy | Location | Mode | Retention | Purpose |
|---|---|---|---|---|
| Primary DR | `out/backups/k3s/k3s-cp-backup-<date>.tar.gz` (workstation) | 600 | 14 | Off-cluster disaster recovery |
| Fast-path | `~/k3s-cp-backups/k3s-cp-backup-<date>.tar.gz` (pi3) | 600 | 7 | In-cluster restore without waiting on workstation |

### Verify it ran
```bash
# Workstation copy
ls -lh out/backups/k3s/ | tail -5
cat out/logs/k3s-backup.log | tail -5

# Pi3 in-cluster copy
python3 ops-tools/runcmd.py pi3 pi3 'ls -lh ~/k3s-cp-backups/ | tail -5'
```

### Restore procedure (from either copy)

**Prerequisites:** quiet window. The control plane is k3s-cp (the N100, node `minipc`) since 2026-11-16; it is
reached by key-auth SSH. The copy-paste steps live in one place — ops manual §9 "Control-plane datastore" — so they
cannot drift again.

> ✅ **Rehearsed 2026-11-26** on a throwaway container: 2 s from start to a serving API, all objects present, CA
> matches the live cluster. Record and the two gotchas (no default route; `dynamic-cert.json` on a new address):
> ops manual §9.

---

## 3. TimescaleDB backup (pg-backup on pi3)

### What's backed up
Full TimescaleDB (`index_prices`, `fx_rates`, `bond_yields`, `interest_rates`, `news_sentiment`, `technical_indicators`, `capital_flows`, `intraday_prices`, `raw_headlines`, `pipeline_heartbeat`, `ticker_names`, screener layer, bot layer, data foundations, etc.) plus the `litellm` DB (best effort).

### How it runs
CronJob `pg-backup` (01:30 America/Los_Angeles, pinned to pi3):
- `pg_dump -h timescaledb -U postgres -d postgres -Fc` (custom format)
- Writes to pi3 USB SSD at `/var/lib/market-data/backups/` (hostPath)
- Verifies before finalizing: size floor (half previous dump), `pg_restore -l` readable TOC, ≥100 entries, `index_prices` table data present
- Atomic `mv` from `.tmp` to final name + `.sha256` sidecar
- Keeps newest 7 readable dumps; empty/unreadable files handled specially
- `litellm` dump as best-effort sidecar

### Where copies live
| Copy | Location | Retention | Purpose |
|---|---|---|---|
| Primary | `/var/lib/market-data/backups/marketdata-<date>.dump` (pi3 SSD) | 7 daily | Nightly restore source |
| Workstation | `out/backups/` via `ops-tools/pull-backups.py` (cron 03:00 local) | 7 local | Off-cluster DR copy |
| Off-site | **NOT configured** — owner decision needed (no rclone/restic/aws/b2 on workstation) | — | AR-11 open item |

### Verify it ran
```bash
# Pi3 backup directory (should show today's dump, ~578 MB; 0-byte file = failed run)
python3 ops-tools/runcmd.py pi3 pi3 'ls -lh /var/lib/market-data/backups/ | tail -5'

# Workstation copy
ls -lh out/backups/ | tail -5
cat out/logs/pull-backups.log | tail -5
```

### Restore procedure (TimescaleDB)

`pg_dump -Fc` of a TimescaleDB database must be restored between `timescaledb_pre_restore()` and
`timescaledb_post_restore()`, serially (`-j` fails on the catalog foreign keys). A plain `pg_restore -c` into the live
database is **not** the procedure.

```bash
# Into an EMPTY database on any host that has the same image (timescale/timescaledb:2.30.1-pg16):
psql -U postgres -c "CREATE DATABASE restored"
psql -U postgres -d restored -c "CREATE EXTENSION IF NOT EXISTS timescaledb" -c "SELECT timescaledb_pre_restore()"
pg_restore -U postgres -d restored /backups/marketdata-<date>.dump      # serial; GRANT errors for missing roles are expected
psql -U postgres -d restored -c "SELECT timescaledb_post_restore()"
# then re-create the roles (schema-configmap creates them) and re-run the schema Job under a new name.
```

In-cluster, `manifests/timescaledb/restore-drill-job.yaml` does exactly this into a throwaway `drill_restore` database
and logs `RESTORE-DRILL-OK rto= rpo=` (quarterly check, ops manual §9).

> **TX2-loss rehearsal, 2026-11-26 (OS-7):** `marketdata-2026-11-26.dump` (578 MB, sha256 verified) restored on the
> workstation into a fresh `timescale/timescaledb:2.30.1-pg16` container with no network, as if the TX2 were gone.
> Container up and database prepared in 8 s, `pg_restore` 72 s, **80 s in total**; 19 hypertables, 642 chunks, 3.7 GB
> on disk. Seven tables compared with live, all identical (`stock_prices` 495,303 rows and `index_prices` 9,300 for
> 2026-01-01…11-19, `fx_rates` 9,544, `raw_headlines` 5,151, `stock_fundamentals` 1,209,175, `universe_constituents`
> 2,264, `bot_users` 3). The only errors were 714 `GRANT`s to roles a bare server does not have (`grafana_ro` 710,
> `pipeline_rw` 4) — expected: roles are not in a `-Fc` dump. **Not rehearsed:** pointing the cluster at the copy
> (the `timescaledb` Service would need to resolve to the new host, and that host needs the role passwords from the
> existing Secrets). The pull from pi3 to the workstation is the nightly 03:00 job (~578 MB over the LAN).
> Earlier drills: 2026-10-12 off-cluster (171 MB, 30 s) and 2026-10-17 in-cluster on pi3 (289 s).

---

## 4. Where each backup-relevant piece lives

The full per-node table (hardware, pinned workloads, blast radius) is `docs/node-roles.md` §1 — not repeated here,
because the copy that used to be here went stale at the 2026-11-16 control-plane move.

| Piece | Host | Notes |
|---|---|---|
| k3s server + `state.db` | **k3s-cp** (N100, node `minipc`, amd64, bare metal) | Tainted `CriticalAddonsOnly`; shares the host with an experiments-only Coolify |
| TimescaleDB data | **tx2**, Retain PV on the SSD | Deleting the PVC keeps the data; the StatefulSet is never deleted casually |
| Nightly dump | **pi3** SSD, `/var/lib/market-data/backups/` | `pg-backup` and `restore-drill` are pinned here |
| Control-plane fast-path copy | **pi3**, `~/k3s-cp-backups/` | Outside every hostPath |
| Second copies of both | **workstation**, `out/backups/` and `out/backups/k3s/` | Needs the workstation on at 03:00–03:30 |
| Pull watchdog + node watchdog | **pi0** (`nodewatch.sh`, every 5 min) | Out of cluster; goes dark with a site-wide power loss |
| Off-site copy | **none** | AR-11, owner decision |

---

## 5. DR scenarios & recovery order

| Scenario | What fails | Recovery order |
|---|---|---|
| **k3s-cp dies (control plane)** | No API, no scheduling; running pods keep running | 1. Reinstall k3s server (same version, same address and flags) 2. Restore `state.db` + token + tls + cred from the workstation or pi3 copy (ops manual §9, rehearsed 2026-11-26: 2 s to a serving API) 3. Agents reconnect 4. Verify all 36 CronJobs |
| **TX2 SSD dies (DB host)** | All pipeline writes fail; market data at risk | 1. Replace SSD 2. Re-deploy timescaledb-0 on tx2 3. Restore TimescaleDB from pi3/workstation pg-dump (§3) 4. Re-run fetchers to backfill |
| **pi3 dies (Grafana + backups)** | No dashboards, no nightly dump, no k3s fast-path copy | 1. Workstation copy still valid for k3s restore 2. TimescaleDB workstation copy still valid 3. Replace pi3, re-deploy grafana + pg-backup |
| **Site-wide power loss** | All nodes down together; pi0 watcher silent | 1. Wait for mains 2. Nodes auto-recover (verified 2026-10-18) 3. Verify all CronJobs ran; check `pipeline_heartbeat` freshness |
| **A Pi worker dies** | Its pinned workloads stop (see `node-roles.md`) | Floating CronJobs reschedule onto the other Pis (images are on pi1 + pi2 + pi3). The Orins run no market-data jobs |
| **Workstation off or its cron broken** | No second copies | pi0 alerts after 36 h (`backup-pull:pg-dumps` / `backup-pull:k3s-backup`); the pi3 dump keeps being written regardless |

**Critical order:** Always restore control plane (k3s) **before** TimescaleDB, because fetchers need the API server to schedule, and TimescaleDB needs the cluster network to be reachable.

---

## 6. Known gaps & open items

| Item | Status | Owner action |
|---|---|---|
| Control-plane restore rehearsal | ✅ Done 2026-11-26 (ops manual §9) | Repeat after a k3s minor upgrade |
| Off-site backup copy | Open | No rclone/restic/aws/b2 on workstation; pick destination + encryption (AR-11) |
| TX2-loss database restore (OS-7) | ✅ Rehearsed 2026-11-26 on the workstation (§3: 80 s, counts identical); client cut-over not rehearsed | Repeat after a TimescaleDB version change |
| Workstation pulls unwatched | ✅ Fixed 2026-11-26 (stamps on pi0 + nodewatch) | — |
| Backup role separation (OS-8) | Open | `pg-backup` still dumps as `postgres`; needs an owner-created Secret |
| Roles are not in the dump | Known | `pg_dump -Fc` carries no roles; they come back from the schema ConfigMap |

---

## 7. Backup schedule (America/Los_Angeles)

| Time | Job | Where it runs | What it does |
|---|---|---|---|
| 01:30 | `pg-backup` (CronJob, explicit `timeZone`) | pi3 | TimescaleDB dump → pi3 SSD, verified, sha256 sidecar |
| 03:00 | `pull-backups.py --prune-local 7` | workstation cron | Newest 2 verified dumps → `out/backups/`; stamp `pg-dumps` on pi0 |
| 03:30 | `pull-k3s-backup.sh` | workstation cron | k3s-cp datastore → `out/backups/k3s/` + pi3; stamp `k3s-backup` on pi0 |
| every 5 min | `nodewatch.sh` | pi0 root cron | Nodes, API port, and both stamps (alert at > 36 h) |

Every other job's schedule is in ops manual §3 (checked against the manifests by `ops-tools/workloads.py --check`).
The workstation cron lines must point at this repo's current path — `crontab -l | grep marketdata`.

---

## 8. Quick reference commands

```bash
export KUBECONFIG=~/.kube/pi-cluster.yaml

# Health
kubectl get nodes
kubectl get cronjobs -n market-data
kubectl get jobs -n market-data --sort-by=.metadata.creationTimestamp | tail -10

# Verify backups ran
python3 ops-tools/runcmd.py pi3 pi3 'ls -lh /var/lib/market-data/backups/ | tail -5'
python3 ops-tools/runcmd.py pi3 pi3 'ls -lh ~/k3s-cp-backups/ | tail -5'
ls -lh out/backups/ | tail -5
ls -lh out/backups/k3s/ | tail -5

# DB freshness (read-only)
PW=$(kubectl -n market-data get secret timescaledb-auth -o jsonpath='{.data.password}' | base64 -d)
kubectl -n market-data exec timescaledb-0 -- env PGPASSWORD="$PW" \
  psql -U postgres -d postgres -c "SELECT feed, last_check, fresh FROM pipeline_heartbeat ORDER BY feed;"

# Are the workstation pulls alive? (stamps on pi0, and the logs)
ssh pi0@<pi0> 'ls -l --time-style=long-iso /var/lib/nodewatch-stamps/'
tail -3 out/logs/pull-backups.log out/logs/k3s-backup.log

# Restore k3s:        ops manual §9 "Control-plane datastore"
# Restore TimescaleDB: §3 above
```

---

## 9. Related documents

- `docs/node-roles.md` — per-node roles, pinned workloads, blast radius
- `docs/market-data-pipeline-operations-manual.md` — full operations manual (§9 backup & restore, §8 failure runbook)
- `docs/pending-tasks.md` — single task tracker (AR-10, AR-11, AR-12 items)
- `AGENTS.md` (repo root) — agent ground rules (no secrets/IPs in tracked files, no `crictl rmi --prune`, etc.)
- `ops-tools/pull-k3s-backup.sh` — control-plane backup script
- `ops-tools/pull-backups.py` — TimescaleDB dump pull to workstation
- `ops-tools/backup-stamp.sh` + `ops-tools/nodewatch.sh` — the pull watchdog
- `manifests/backup/cronjob.yaml` — pg-backup CronJob manifest
