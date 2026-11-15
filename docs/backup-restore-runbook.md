# Backup & Restore Runbook

> **Single source of truth for every backup in the cluster:** k3s control-plane datastore (`state.db`) and TimescaleDB (`pg_dump`).
> **Last verified: 2026-11-15.** Commands are copy-paste ready. No IP addresses — node names only (real values in gitignored `.hosts.env`).
> Secrets never enter tracked files or chat output; use `@@PW@@` token via `runcmd.py` / `putfile.py`.

## 1. Backup overview

Two independent backups run on different schedules to different destinations:

| Backup | What it protects | Source | Schedule | Destination | Retention |
|---|---|---|---|---|---|
| **k3s control-plane** (`pull-k3s-backup.sh`) | Cluster config: 36 CronJobs, secrets, ConfigMaps, Deployments, node join tokens, TLS certs | pi2 `state.db` (~8 MB SQLite) | Workstation cron 03:30 local | Workstation `out/backups/k3s/` (primary DR) + pi3 `/var/lib/market-data/backups/k3s/` (fast-path) | 14 (workstation) / 7 (pi3) |
| **TimescaleDB** (`pg-backup` CronJob) | Market data: all prices, fundamentals, sentiment, scores, etc. | TimescaleDB on tx2 (`sda4` SSD) | 01:30 UTC node-local | pi3 `/var/lib/market-data/backups/` + workstation via `pull-backups.py` | 7 daily |

The control-plane backup is **tiny** (~8 MB + keys) because it holds cluster *configuration*, not market data. The TimescaleDB dump is **~539 MB** because it holds all market data.

**Failure domains covered:**
- pi2 disk/datastore loss → control-plane backup restores cluster config
- pi3 disk loss → TimescaleDB dump on pi3 SSD lost, but workstation copy survives
- Site-wide power loss → all nodes down together; workstation copy is the off-cluster DR copy (pi0 watcher also goes down — see §7)
- TX2 SSD failure → TimescaleDB data lost unless pg-backup copy on pi3/workstation exists

---

## 2. k3s control-plane backup (pi2 → workstation + pi3)

### What's backed up
`/var/lib/rancher/k3s/server/db/state.db` (~8 MB SQLite via kine), plus:
- `/var/lib/ranched/k3s/server/token` — node join token
- `/var/lib/ranched/k3s/server/tls` — cluster CA + keys
- `/var/lib/ranched/k3s/server/cred` — credentials

**Mode 600.** This archive holds the cluster's root keys — anyone who can read it has full admin over the cluster.

### How it runs
`ops-tools/pull-k3s-backup.sh` (workstation cron 03:30 local):

1. Checks pi2 load1; skips if > 3 (AGENTS.md rule 2 — never add load to a busy control plane)
2. On pi2: Python `sqlite3` online-backup API → consistent `state.db` copy (pi2 has no `sqlite3` CLI)
3. `PRAGMA integrity_check` + row count verification
4. Copies `token`, `tls`, `cred`; tars + gzips to `/tmp/k3s-cp-backup.tar.gz` on pi2, mode 600
5. `getfile.py` pulls tarball + `.sha256` to workstation `out/backups/k3s/k3s-cp-backup-<date>.tar.gz`
6. Remote `/tmp` copy deleted from pi2
7. **NEW 2026-11-15:** `putfile.py` pushes the same archive to pi3 at `/var/lib/market-data/backups/k3s/k3s-cp-backup-<date>.tar.gz` (mode 600, keeps newest 7) as in-cluster fast-path copy
8. Workstation keeps 14; pi3 keeps 7; rotation deletes older

### Where copies live
| Copy | Location | Mode | Retention | Purpose |
|---|---|---|---|---|
| Primary DR | `out/backups/k3s/k3s-cp-backup-<date>.tar.gz` (workstation) | 600 | 14 | Off-cluster disaster recovery |
| Fast-path | `/var/lib/market-data/backups/k3s/k3s-cp-backup-<date>.tar.gz` (pi3) | 600 | 7 | In-cluster restore without waiting on workstation |

### Verify it ran
```bash
# Workstation copy
ls -lh out/backups/k3s/ | tail -5
cat out/logs/k3s-backup.log | tail -5

# Pi3 in-cluster copy
python3 ops-tools/runcmd.py pi3 pi3 'ls -lh /var/lib/market-data/backups/k3s/ | tail -5'
```

### Restore procedure (from either copy)

**Prerequisites:** quiet window, pi2 load < 3, spare SSD/SSD for drill first (restore **never rehearsed**).

```bash
# 0. Copy the archive to pi2 (from workstation or pi3)
# From workstation:
python3 ops-tools/putfile.py pi2 pi2 out/backups/k3s/k3s-cp-backup-<date>.tar.gz /tmp/k3s-cp-backup.tar.gz
# From pi3:
python3 ops-tools/runcmd.py pi3 pi3 'cat /var/lib/market-data/backups/k3s/k3s-cp-backup-<date>.tar.gz' > /tmp/k3s-cp-backup.tar.gz
python3 ops-tools/putfile.py pi2 pi2 /tmp/k3s-cp-backup.tar.gz /tmp/k3s-cp-backup.tar.gz

# 1. Stop k3s on pi2
python3 ops-tools/runcmd.py pi2 pi2 'echo @@PW@@ | sudo -S systemctl stop k3s'

# 2. Extract
python3 ops-tools/runcmd.py pi2 pi2 'tar -xzf /tmp/k3s-cp-backup.tar.gz -C /tmp'

# 3. Replace state.db
python3 ops-tools/runcmd.py pi2 pi2 'echo @@PW@@ | sudo -S cp /var/lib/ranched/k3s/server/db/state.db /tmp/state.db.broken 2>/dev/null; sudo rm -f /var/lib/ranched/k3s/server/db/state.db-{wal,shm}; sudo cp /tmp/k3s-cp-backup/state.db /var/lib/ranched/k3s/server/db/state.db'

# 4. Restore token + certs
python3 ops-tools/runcmd.py pi2 pi2 'echo @@PW@@ | sudo -S cp /tmp/k3s-cp-backup/token /var/lib/ranched/k3s/server/token; sudo cp -a /tmp/k3s-cp-backup/tls /tmp/k3s-cp-backup/cred /var/lib/ranched/k3s/server/'

# 5. Start k3s
python3 ops-tools/runcmd.py pi2 pi2 'echo @@PW@@ | sudo -S systemctl start k3s'
kubectl get nodes   # agents keep trusting the restored CA and reconnect
```

> ⚠ **Never rehearsed.** Drill this on a spare SD/SSD before relying on it in production.

---

## 3. TimescaleDB backup (pg-backup on pi3)

### What's backed up
Full TimescaleDB (`index_prices`, `fx_rates`, `bond_yields`, `interest_rates`, `news_sentiment`, `technical_indicators`, `capital_flows`, `intraday_prices`, `raw_headlines`, `pipeline_heartbeat`, `ticker_names`, screener layer, bot layer, data foundations, etc.) plus the `litellm` DB (best effort).

### How it runs
CronJob `pg-backup` (01:30 UTC node-local, pinned to pi3):
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
# Pi3 backup directory (should show today's dump, ~539 MB; 0-byte file = failed run)
python3 ops-tools/runcmd.py pi3 pi3 'ls -lh /var/lib/market-data/backups/ | tail -5'

# Workstation copy
ls -lh out/backups/ | tail -5
cat out/logs/pull-backups.log | tail -5
```

### Restore procedure (TimescaleDB)

```bash
# From any pod with the timescale image + Secret (restore-drill pattern)
kubectl -n market-data run restore-pod --rm -i --restart=Never \
  --image=timescale/timescaledb:2.30.1-pg16 \
  --env-from=secretRef/timescaledb-auth \
  -- sh -c 'pg_restore -h timescaledb -U postgres -d postgres -c /backups/marketdata-<date>.dump'

# Or from pi3 host directly:
python3 ops-tools/runcmd.py pi3 pi3 'echo @@PW@@ | sudo -S pg_restore -h timescaledb -U postgres -d postgres -c /var/lib/market-data/backups/marketdata-<date>.dump'
```

> Restore drill done 2026-10-12 (off-cluster) and re-run 2026-10-17 (in-cluster): 171 MB dump restored in 30 s / 289 s, row counts matched. RTO ≈ 5 min at current size.

---

## 4. Full cluster hardware & roles

| Node | Hardware | In k3s? | Role | Pinned workloads | If it dies |
|---|---|---|---|---|---|
| **pi0** | Pi 1 B+, 512 MB, SD | **No** | LAN DNS (Pi-hole + unbound), out-of-cluster watcher | none (not a k8s node) | LAN DNS gone; dead-man's switch silent; nodewatch alerts stop |
| **pi1** | Pi 3 B, 1 GB, 233 GB SSD | Worker | Telegram bot + marketview host; ingestion fallback | `telegram-bot`, `marketview`, `telegram-digest`, `telegram-alerts` | Bot stops answering; digest/alerts not sent; floating jobs land on pi3/orin2 |
| **pi2** | Pi 3 B+, 1 GB | **Control plane** | k3s server (API, scheduler, kine/state.db, CoreDNS, Traefik, metrics) | none (**cordoned**) | Whole cluster loses API; nothing new schedules; running pods survive |
| **pi3** | Pi 3 B, 1 GB, SSD | Worker | Grafana, backups (pg + k3s control-plane copy) | `grafana`, `pg-backup`, `restore-drill` | No dashboards; no nightly TimescaleDB dump; no k3s fast-path copy |
| **tx2** (`ahytx2-desktop`) | Jetson TX2, 7.7 GB, eMMC + 224 GB SSD | Worker (tainted `gpu=true`) | **The database**, NLP jobs, LLM gateway, image build host | `timescaledb-0`, `llm-gateway` (ns `llm`), `sentiment-scorer`, `headline-ner` | **Every pipeline write fails** (DB down); data safe on Retain PV |
| **orin1** | Jetson Orin Nano, 8 GB, 932 GB SSD, 1×GPU | Worker (tainted `gpu=orin`) | **STT/TTS voice layer only** — reserved, not deployed | `stt-service`, `tts-service` (manifests exist, not applied) | Voice I/O stays silent; text commands and marketview still work |
| **orin2** | Jetson Orin Nano, 8 GB, 476 GB SSD, 1×GPU | Worker (untainted) | **General ingestion pool** — runs all 31 floating CronJobs | none | Floating jobs reschedule to pi1/pi3 (images exist on both); no pinned workload lost |

**k3s versions:** server pi2 v1.36.4+k3s1; agents pi1/pi3 v1.36.4; orin1/orin2 v1.37.2; tx2 pinned v1.35.3+k3s1 (cgroup v1 ceiling).

**Labels/taints drift:** both Orins labelled `hardware=orin-cpu` (not `hardware=orin`); taint key is `gpu=orin` (not `hardware=orin`). Voice manifests select `hardware: orin` — **they will not schedule as written** (pending-tasks §1).

**TX2 GPU:** not exposed to Kubernetes (no GPU allocatable; device plugin only on orin1/orin2). TX2 GPU reached via GPU RuntimeClass only (smoke test path).

---

## 5. DR scenarios & recovery order

| Scenario | What fails | Recovery order |
|---|---|---|
| **pi2 dies (control plane)** | No API, no scheduling, CoreDNS gone | 1. Restore k3s state.db from pi3/workstation copy (§2) 2. Agents reconnect 3. Verify all 36 CronJobs |
| **TX2 SSD dies (DB host)** | All pipeline writes fail; market data at risk | 1. Replace SSD 2. Re-deploy timescaledb-0 on tx2 3. Restore TimescaleDB from pi3/workstation pg-dump (§3) 4. Re-run fetchers to backfill |
| **pi3 dies (Grafana + backups)** | No dashboards, no nightly dump, no k3s fast-path copy | 1. Workstation copy still valid for k3s restore 2. TimescaleDB workstation copy still valid 3. Replace pi3, re-deploy grafana + pg-backup |
| **Site-wide power loss** | All nodes down together; pi0 watcher silent | 1. Wait for mains 2. Nodes auto-recover (verified 2026-10-18) 3. Verify all CronJobs ran; check `pipeline_heartbeat` freshness |
| **orin2 dies (floating jobs)** | Floating CronJobs stop | None needed — scheduler reschedules to pi1/pi3 (images present on both) |

**Critical order:** Always restore control plane (k3s) **before** TimescaleDB, because fetchers need the API server to schedule, and TimescaleDB needs the cluster network to be reachable.

---

## 6. Known gaps & open items

| Item | Status | Owner action |
|---|---|---|
| Control-plane restore **never rehearsed** | Open | Drill on spare SSD/SSD before relying on it |
| Off-site backup copy | Open | No rclone/restic/aws/b2 on workstation; pick destination + encryption (AR-11) |
| TX2 SSH credential stale | Blocks §6 cycle + TX2 disk checks | Fix `TX2_PASS` in `.secrets.env`; re-verify `df -h / /mnt/tx2-ssd` |
| Orin nodes unmonitored | Open | Not in nodewatch, no node-stats-log, no SSH alias (AR-12 pattern never applied) |
| pi2 persistent journald | Open | pi2 has no persistent journal; reboots leave no trace (proposed, owner's call) |
| Voice layer not deployed | Open | orin1 reserved but manifests won't schedule (label/taint mismatch) |
| Workstation clock skew | Observed | Was ~10 h 50 m behind UTC mid-session; compute freshness against cluster/DB clock, not workstation |

---

## 7. Schedule summary (node-local, America/Los_Angeles on pi2)

| Time (node-local) | Job | Node | What it does |
|---|---|---|---|
| 01:30 | pg-backup | pi3 | TimescaleDB dump → pi3 SSD |
| 03:00 | pull-backups.py | workstation | Pull pg-dump to workstation |
| 03:30 | pull-k3s-backup.sh | workstation | Pull k3s state.db from pi2 → workstation + pi3 |
| 07:00 | rbi-fetcher | orin2 | India G-Secs / RBI rates |
| 08:00 IST | telegram-digest | pi1 | Daily digest (Asia/Kolkata, explicit TZ) |
| 11:15 | nse-fetcher | orin2 | NSE indices + FII/DII |
| 14:00 | fiidii-fetcher | orin2 | FII/DII flows |
| 22:30 | yfinance-fetcher | orin2 | US indices, stocks, FX |
| 23:00 | fx-fetcher | orin2 | FX pairs |
| 23:15 | yields-fetcher | orin2 | Bond yields |
| 23:20 | rates-fetcher | orin2 | FED funds (FRED) |
| 23:45 | news-fetcher | orin2 | RSS headlines → raw_headlines |
| :10 hourly | intraday-fetcher | orin2 | Intraday candles |
| 00:15 | sentiment-scorer | tx2 | FinBERT on today's headlines |
| 00:30 | ta-compute | orin2 | RSI/MACD/SMA/Bollinger |
| 00:45 | headline-ner | tx2 | Gazetteer entity extraction |
| 01:00 | news-embed | orin2 | Headline embeddings |
| 02:00 | ml1-anomalies + stock-metrics | orin2 | Anomaly detection + per-symbol metrics |
| 02:10 | stock-valuation | orin2 | 10-method fair-value panel |
| 02:15 | stock-screener | orin2 | Scores + ranks |
| 02:30 | stock-report | orin2 | Rendered markdown reports |
| 02:40 | prediction-tracker | orin2 | Write-once snapshots |
| 02:45 | telegram-alerts | pi1 | Grafana alert fan-out |
| 02:45 (Sat) | ml7b-per-symbol | orin2 | Per-symbol ML narration |
| 03:00 | ml3-volatility | orin2 | EWMA + GARCH volatility forecasts |
| 06:00 (Sat) | stock-fundamentals | orin2 | EDGAR/NSE fundamentals |
| 09:00 (Sun) | universe-refresh | orin2 | Wikipedia/NSE universe rebuild |
| 10:00 (Sun) | nse-fundamentals | orin2 | India results filings |
| 12:00 (Sun) | earnings-calendar | orin2 | Weekly earnings calendar |
| 12:30 (Sun) | accuracy-check | orin2 | Data-accuracy audit |

> Node-local schedules follow `America/Los_Angeles` on pi2: UTC−7 in summer / UTC−8 in winter. Every node-local job's UTC instant shifts an hour at the DST boundary (early November, early March). Only `telegram-digest` and `llm-narrative` use explicit `Asia/Kolkata` timeZone (DST-free).

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
python3 ops-tools/runcmd.py pi3 pi3 'ls -lh /var/lib/market-data/backups/k3s/ | tail -5'
ls -lh out/backups/ | tail -5
ls -lh out/backups/k3s/ | tail -5

# DB freshness (read-only)
PW=$(kubectl -n market-data get secret timescaledb-auth -o jsonpath='{.data.password}' | base64 -d)
kubectl -n market-data exec timescaledb-0 -- env PGPASSWORD="$PW" \
  psql -U postgres -d postgres -c "SELECT feed, last_check, fresh FROM pipeline_heartbeat ORDER BY feed;"

# Restore k3s from pi3 copy
python3 ops-tools/runcmd.py pi3 pi3 'cat /var/lib/market-data/backups/k3s/k3s-cp-backup-<date>.tar.gz' > /tmp/k3s-cp-backup.tar.gz
python3 ops-tools/putfile.py pi2 pi2 /tmp/k3s-cp-backup.tar.gz /tmp/k3s-cp-backup.tar.gz
# ... then follow §2 restore steps

# Restore TimescaleDB
kubectl -n market-data run restore-pod --rm -i --restart=Never \
  --image=timescale/timescaledb:2.30.1-pg16 \
  -- sh -c 'pg_restore -h timescaledb -U postgres -d postgres -c /backups/marketdata-<date>.dump'
```

---

## 9. Related documents

- `docs/node-roles.md` — per-node roles, pinned workloads, blast radius
- `docs/market-data-pipeline-operations-manual.md` — full operations manual (§9 backup & restore, §8 failure runbook)
- `docs/pending-tasks.md` — single task tracker (AR-10, AR-11, AR-12 items)
- `docs/AGENTS.md` — agent ground rules (no secrets/IPs in tracked files, no `crictl rmi --prune`, etc.)
- `ops-tools/pull-k3s-backup.sh` — control-plane backup script
- `ops-tools/pull-backups.py` — TimescaleDB dump pull to workstation
- `manifests/backup/cronjob.yaml` — pg-backup CronJob manifest
