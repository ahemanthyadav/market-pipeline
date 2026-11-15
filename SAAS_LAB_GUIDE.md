# SaaS Lab Guide — running the market-data pipeline as a service

> **What this guide is:** a hands-on, session-oriented runbook for operating the
> `marketdata` market-data pipeline as a self-hosted SaaS-style service on a
> k3s homelab (Pi workers + Jetson TX2/Orin Nano). It covers bring-up, daily
> operation, deploys, and recovery — without ever recording credentials or real
> IP addresses.
>
> **What this guide is not:** a multi-tenant SaaS product spec. This is a
> single-owner lab deployment: one TimescaleDB instance, 36 CronJobs, a Telegram
> bot, Grafana, and (optionally) a marketview web explorer. "SaaS" here means
> "run it like a service you operate daily," not "sell access to it."

## Ground rules (read first — from AGENTS.md)

1. **Work from `marketdata/`.** Export `KUBECONFIG=~/.kube/pi-cluster.yaml` before any kubectl in a new shell.
2. **pi2 is fragile** (1 GB control plane). Never restart more than one agent at a time; if load > 3 sustained, follow ops manual §8 cool-down first.
3. **TX2 fixed config is load-bearing.** Never "clean up" or apt-upgrade without §8 re-verification.
4. **Data invariants.** Fetchers DELETE+INSERT only their own `(key, ts)` rows — re-runs are idempotent. `index_prices` stays pure-daily (intraday → `intraday_prices`). Timestamps are UTC; `stock_prices.ts` is `00:00 UTC` on the trade date.
5. **PV `timescaledb-tx2-ssd` is Retain** — deleting the PVC keeps data on sda4, but never delete the StatefulSet/PVC/PV casually.
6. **No registry.** Images are `local/*` with `imagePullPolicy: Never`, built on TX2, distributed as tarballs via `ops-tools/`. Any fetcher code change = tag bump + full §6 cycle + tarball to `/mnt/tx2-ssd/image-tarballs/` with `SHA256SUMS.all` regenerated. **Never `crictl rmi --prune` / `ctr images prune`.** Remove old tags one by one, by name.
7. **No secrets or IPs in tracked files.** The only local store is gitignored `.secrets.env` (template `.secrets.env.example`); `ops-tools/*.py` read it themselves and remote commands use the `@@PW@@` token — never write a password on a command line; never echo/cat a secret. Agents may not read/edit `.secrets.env`.
8. **No IP addresses in tracked files.** Use node names (`pi0..pi3`, `tx2`) or placeholders; real addresses live only in gitignored `.hosts.env`.
9. **Hypertable queries are bounded in time** — always add a `ts >=` bound in hot paths. Applications read the `v_*` contract views, which carry the bound.
10. **Never print a secret** — `ps`/`pgrep -af` on a host while `runcmd.py` runs shows the expanded `@@PW@@`.

## Before any push (every session)

```bash
git config core.hooksPath .githooks        # one-time per clone
python3 ops-tools/workloads.py --check     # doc tables agree with manifests
bash tests/run-all.sh                      # every test_*.py / test-*.sh
```

Conventional commits; push over SSH. `git log --oneline -5` shows the current style (`fix(ci): ...`, `feat(telegram): ...`).

---

## 0. Architecture at a glance

| Node | Role | Runs |
|---|---|---|
| pi0 | DNS only (not in k8s) | Pi-hole v6 + unbound; nodewatch out-of-band watcher |
| pi1 | Worker — ingestion pool + Telegram | telegram-bot (pinned), telegram-digest (08:00 IST), telegram-alerts (02:45), floating ingestion CronJobs |
| pi2 | Control plane (cordoned, 1 GB — fragile) | k3s API, scheduler, datastore; **no workloads** |
| pi3 | Worker — Grafana + backups | Grafana (pinned), pg-backup (01:30), floating CronJobs |
| tx2 | GPU/tainted + DB host | TimescaleDB (Guaranteed QoS), sentiment-scorer, headline-ner, llm-gateway; image build host |
| orin-nano | Voice-only inference (GPU/CPU, tainted `hardware=orin`) | stt-service (Whisper, :8000), tts-service (Piper, :8001) — ClusterIP only |

**Security boundaries:** `postgres` (jobs/schema), `grafana_ro` (SELECT only), `telegram_app` (SELECT + watchlist/bot_users writes). No registry — images built natively on TX2 and tarball-distributed.

Read the full map: `docs/node-roles.md`, `docs/system-architecture-visual.md`.

---

## 1. First-time bring-up (lab install)

### 1.1 On the admin workstation

```bash
cd /home/ahy/Documents/passion_projects/projects/marketdata   # the marketdata repo
export KUBECONFIG=~/.kube/pi-cluster.yaml
```

Copy the templates (never commit the filled-in files):

```bash
cp .secrets.env.example .secrets.env          # chmod 600 it; fill in values
cp .hosts.env.example .hosts.env              # fill in node/tailnet aliases
```

`ops-tools/*.py` resolve `pi0..pi3`, `tx2` from `.hosts.env` and expand `@@KEY@@` from `.secrets.env`.

### 1.2 Verify cluster connectivity

```bash
kubectl get nodes
# expect: pi0 NotReady (not a k8s node), pi1/pi3 Ready, pi2 Ready + cordoned, tx2 Ready + tainted, orin-nano Ready + tainted
kubectl -n market-data get cronjobs           # 36 CronJobs expected; none suspended except nse-fundamentals (by design)
```

If a node isn't Ready, see ops manual §8 before proceeding.

---

## 2. Daily operation (the service rhythm)

The pipeline runs on node-local PDT schedules (ADR 0002); `telegram-digest` uses Asia/Kolkata.

| Time (PDT) | Job | Node | What it does |
|---|---|---|---|
| 07:00 | rbi-fetcher | pi1/pi3 | India G-Secs / RBI rates |
| 11:15 | nse-fetcher | pi1/pi3 | NSE indices + FII/DII |
| 14:00 | fiidii-fetcher | pi1/pi3 | FII/DII flows |
| 22:30 | yfinance-fetcher | pi1/pi3 | US indices, stocks, FX |
| 23:00 | fx-fetcher | pi1/pi3 | FX pairs |
| 23:15 | yields-fetcher | pi1/pi3 | bond yields |
| 23:20 | rates-fetcher | pi1/pi3 | FED funds (FRED) |
| 23:45 | news-fetcher | pi1/pi3 | RSS headlines → raw_headlines |
| :10 hourly | intraday-fetcher | pi1/pi3 | intraday candles |
| 00:15 | sentiment-scorer | tx2 | FinBERT on today's headlines |
| 00:30 | ta-compute | pi1/pi3 | RSI/MACD/SMA/Bollinger |
| 00:45 | headline-ner | tx2 | gazetteer entity extraction |
| 01:00 | news-embed | tx2 | headline embeddings |
| 01:30 | pg-backup | pi3 | daily dump → pi3 SSD |
| 01:45 | pipeline-health | pi1/pi3 | heartbeat freshness upsert |
| 02:00 | stock-metrics | pi1/pi3 | per-symbol metrics |
| 02:10 | stock-valuation | pi1/pi3 | 10-method fair-value panel |
| 02:15 | stock-screener | pi1/pi3 | scores + ranks |
| 02:30 | stock-report | pi1/pi3 | rendered markdown reports |
| 02:40 | prediction-tracker | pi1/pi3 | write-once snapshots |
| 02:45 | telegram-alerts | pi1 | Grafana alert fan-out |
| 07:40 IST | llm-narrative | pi1/pi3 | daily AI market note (via llm-gateway) |
| 08:00 IST | telegram-digest | pi1 | daily digest to owner + allow-list |
| Sun 09:00 | universe-refresh | pi1/pi3 | Wikipedia/NSE universe rebuild |
| Sat 06:00 | stock-fundamentals | pi3 | EDGAR/NSE fundamentals |

### 2.1 Morning health check (< 5 min)

```bash
export KUBECONFIG=~/.kube/pi-cluster.yaml

# 1. pi2 load gate (< 2 target; > 3 = stop, ops manual §8)
python3 ops-tools/runcmd.py pi2 pi2 'uptime; free -m | head -2'

# 2. All CronJobs scheduled, none stuck
kubectl -n market-data get cronjobs

# 3. DB heartbeat fresh (14 feeds)
kubectl -n market-data exec timescaledb-0 -- env PGPASSWORD="$PW" \
  psql -U postgres -d postgres -c "SELECT feed, fresh, last_data_ts FROM pipeline_heartbeat ORDER BY 1;"

# 4. Latest backup present
kubectl -n market-data get cronjob pg-backup -o jsonpath='{.status.lastScheduleTime}{"\n"}'
```

$PW comes from `.secrets.env` via `@@PW@@` — never type it:
```bash
PW=$(kubectl -n market-data get secret timescaledb-auth -o jsonpath='{.data.password}' | base64 -d)
```

### 2.2 The Telegram channel (the service UI)

- `/stock <symbol>` — quote card with fair-value line, 52-w range, percentile scores
- `/top` — composite screener table
- `/sectors <universe>` — sector medians + fair-value gap
- `/regime` — breadth / benchmark trend / factor momentum traffic light
- `/earnings` — watchlist + core top-25, next 14 days
- `/scorecard` — prediction snapshot counts + decile excess
- `/sp500` — S&P 500 in depth (median P/E/P/B, market cap)
- Digest arrives 08:00 IST to allow-listed chats; Grafana alerts fan out via the same bot.

Voice (optional, orin-nano): STT/TTS services are ClusterIP-only on `:8000`/`:8001` — wired into the bot for voice input/output when the owner enables it.

---

## 3. Deploying a change (the §6 image cycle)

No registry: images are `local/<name>:vN`, built on TX2, tarball-distributed, `imagePullPolicy: Never`. **A same-tag rebuild rolls nothing — always bump the tag.**

### 3.1 Pre-flight (every deploy)

```bash
export KUBECONFIG=~/.kube/pi-cluster.yaml
python3 ops-tools/workloads.py --check
bash tests/run-all.sh
python3 ops-tools/runcmd.py pi2 pi2 'uptime; free -m | head -2'   # pi2 load < 2
```

### 3.2 Code change → live (condensed from ops manual §6)

1. **Bump the tag everywhere** the image is referenced: cronjob/deployment yamls **and** doc tables (`workloads.py --check` catches stragglers). Telegram is three references: `cronjob.yaml` ×2 + `deployment.yaml`.
2. **Fresh build dir on the TX2**, ship exactly what the Dockerfile COPYs (`putfile.py`) — stale leftovers silently ship old code.
3. **Build natively on TX2:** `docker build -t local/<name>:vN`. Warm cache ≈ seconds; a cold pip layer can exceed the 120 s runcmd channel timeout → `nohup` + poll (a TIMEOUT ≠ build died).
4. **Verify image contents** — md5sum every COPY'd file in-image against repo HEAD (this burns a tag before it ever runs; the v33 burn taught this).
5. **Archive:** `docker save \| gzip` → `/mnt/tx2-ssd/image-tarballs/<name>-vN.tar.gz`, regenerate `SHA256SUMS.all`.
6. **Distribute only where it can schedule:** pinned images → pinned node only; TX2-tainted images skip distribution; unpinned CronJobs → pi1/pi3. `putfile.py` → `k3s ctr -n k8s.io images import`. Verify with `ctr images ls \| grep <tag>` before applying.
7. **Clean up:** remove home-dir tarball copies and the TX2 build dir, and each node's tarball after import (the archive copy stays).

### 3.3 Apply + validate

```bash
kubectl apply -f manifests/<dir>/cronjob.yaml
kubectl -n market-data get cronjob <name> -o jsonpath='{.spec.jobTemplate.spec.template.spec.containers[0].image}{"\n"}'
```

- **A sed'd yaml is not a deploy** — jsonpath the live object before believing anything.
- CronJobs: `kubectl create job <name>-validate --from=cronjob/<name>`, poll-capture logs (failed pods vanish in ~1 min), check the script's OK-marker, then **trust the database, not the exit code** (row counts / `pipeline_heartbeat`), then delete the job.
- Always-on (telegram-bot, marketview): tag change rolls the pod — `rollout status`, then read the new pod's logs.
- **Telegram caveat:** no digest/alerts canary (fans real messages off-schedule). Validation = in-image hash check + clean bot roll + owner exercising a changed `/command`; next scheduled digest is the production proof.

### 3.4 Rollback

The previous image is still imported on the node (and archived on TX2): revert the tag in the manifest, `kubectl apply`, verify live spec. For a misbehaving CronJob mid-investigation: `kubectl -n market-data patch cronjob <name> -p '{"spec":{"suspend":true}}` — remember to unsuspend.

---

## 4. Voice service (orin-nano) — the newest server

`manifests/voice/stt_server.py` and `tts_server.py` are real FastAPI services:

- `stt-service` — faster-whisper HTTP server (`tiny`/`base` + CUDA), `:8000`, `local/stt-service:v1`
- `tts-service` — Piper HTTP server, `:8001`, `local/tts-service:v1`

Both deployments pin to `hardware: orin` via `nodeSelector` + `hardware=orin:NoSchedule` toleration, so **no other CronJob ever lands on orin-nano**. Exposed inside the cluster only as ClusterIP Services (`stt-service.market-data.svc.cluster.local:8000`, `tts-service...:8001`).

**Operating notes:**
- Whisper `tiny` + CUDA on a 4 GB Orin Nano is tight — 8 GB preferred; watch GPU/system shared RAM.
- Do not co-locate other GPU workloads (e.g. future RAG) without re-checking RAM.
- Voice models + CUDA context live in the container image, built on TX2 per §3 and tarball-distributed to orin-nano.
- If you edit either `*_server.py`, you must bump `local/stt-service:v1` / `local/tts-service:v1` and run the full §3 cycle (tag bump → build → verify → tarball → SHA256SUMS → import → apply).

---

## 5. Database access (read-only by default)

```bash
export KUBECONFIG=~/.kube/pi-cluster.yaml
PW=$(kubectl -n market-data get secret timescaledb-auth -o jsonpath='{.data.password}' | base64 -d)
kubectl -n market-data exec timescaledb-0 -- env PGPASSWORD="$PW" \
  psql -U postgres -d postgres -c "<READ-ONLY SQL>"
```

Non-SELECT SQL: propose first, get approval before running. Schema source of truth is `manifests/timescaledb/schema-configmap.yaml` (applied via `schema-job.yaml`, never ad-hoc psql DDL).

Key tables: `index_prices`, `fx_rates`, `bond_yields`, `interest_rates`, `news_sentiment`, `technical_indicators`, `capital_flows`, `intraday_prices`, `raw_headlines`, `pipeline_heartbeat`, `ticker_names`; screener layer: `universe_constituents`, `stock_prices`, `stock_fundamentals`, `stock_metrics_daily`, `stock_scores`, `stock_reports`, `screening_runs`, `job_state`; bot layer: `watchlist`, `bot_users`; data foundations: `corporate_actions`, `price_anomalies`, `financial_facts`, `universe_membership_snapshots`, `factor_returns`, `job_runs`, `llm_narratives`, `headline_embeddings`.

Always read the `v_*` contract views (ADR 0032) — they carry the `ts >=` bound. Open-ended per-symbol reads open every chunk (measured 34 s planning on 3,382 chunks before the 1-year chunk merge).

---

## 6. Failure recovery (ops manual §8 copy-paste patterns)

| Symptom | First command |
|---|---|
| pi2 wedged (API/SSH hang) | physical power-cycle of pi2; rejoin agents one at a time on a calm server |
| `ErrImageNeverPull` on a fetcher | image missing on that node — import the tarball, verify `ctr images ls`, do not `--prune` |
| `pipeline_heartbeat` frozen | run the fetcher job manually (`kubectl create job --from=cronjob/<name>`), check row counts |
| Backup stale | `kubectl -n market-data get cronjob pg-backup`; check pi3 SSD dump + `.sha256` sidecar |
| pi1 undervolt / throttle | it's on its own supply since 2026-10-14; run `bash ops-tools/power-review.sh --since <ts>`; no bulk imports until 24 h acceptance passes |

Never `crictl rmi --prune` / `ctr images prune` — deletes every CronJob image and `Never` pull cannot recover. Remove old tags one by one, by name.

---

## 7. Backups & restore (ops manual §9)

- Daily `pg-backup` CronJob (01:30) → pi3 SSD hostPath, 7-day rotation, `.sha256` sidecar, validated `BACKUP-OK`.
- Second copy pulled to the workstation with `ops-tools/pull-backups.py`; off-site copy is open work.
- `timescaledb` PV is Retain — deleting the PVC keeps data on sda4; never delete the StatefulSet/PVC/PV casually.
- Restore drill: `restore-drill` Job runs on pi3; backups live on pi3's SSD.

---

## 8. Docs discipline

- Update `docs/*` whenever you verify or change something; keep "Last verified" dates true.
- Mark completed work ✅ in `docs/pending-tasks.md` (the single task tracker) and update the owning doc in the same session — tracker and docs must never disagree.
- Feed quirks / symbol deaths → ops manual §4 plus the fetcher script header.
- New failure modes → ops manual §8 (copy-paste fix commands).
- Long-shelf-life decisions → ADR in `docs/adr/` via the `adr` skill (33 ADRs; 0004 no-registry, 0013 stock_prices ts, 0032 DB contract are load-bearing).
- Never commit credentials or IPs. Secrets live only in gitignored `.secrets.env`; real addresses only in gitignored `.hosts.env`.

---

## 9. Where to go next

- Start of any session: `AGENTS.md` → `docs/pending-tasks.md` → `docs/node-roles.md`
- Understanding the system: `docs/market-data-architecture.md` + `docs/system-architecture-visual.md`
- Operating it: `docs/market-data-pipeline-operations-manual.md`
- Deploying: `docs/deployment-guide.md`
- The "why": `docs/market-data-pipeline-plan.md`
