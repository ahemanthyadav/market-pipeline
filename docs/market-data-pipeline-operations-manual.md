# Market Data Pipeline — Operations Manual & System Reference

> **Reconciled with `manifests/` 2026-11-08** (schedules/images checked by `ops-tools/workloads.py --check`; §6 cycle
> re-verified end-to-end; TX2 docker build store pruned to light/ml/litellm only, tarball archive pruned to 6 live images).
> **Last fully healthy cluster check: 2026-11-08** — all k8s nodes Ready (pi2 cordoned), 36 CronJobs live
> (`nse-fundamentals` suspended by design), TimescaleDB/Grafana/Telegram-bot all Running.
> **Recovery performed 2026-11-08:** restored `marketdata-data:v1` and `marketdata-light:v1` to pi1/pi3 containerd stores
> after all fetchers failed with `ErrImageNeverPull` and `pipeline_heartbeat` froze at 2026-11-04; forced backfill of every
> active fetcher; data current through Friday 2026-11-06 (markets closed weekend). See §8 for the full runbook entries.
> **Repo layout (2026-10-03):** everything lives in `projects/marketdata/` —
> `docs/` (project docs), `manifests/` (k8s), `ops-tools/` (SSH helpers),
> `validation/` (Grafana scripts + screenshots), `AGENTS.md` + `opencode.jsonc` +
> `.opencode/` (OpenCode: agents, commands, skills). Run all `manifests/…` /
> `ops-tools/…` commands from `marketdata/` as the working directory.
> Companion docs: `market-data-pipeline-plan.md` (design + build history),
> `homelab setup/SETUP.md` (Pi runbook + Pi passwords), `homelab
> setup/TX2_AI_Edge_Lab_Setup_and_Test_Plan.md` (TX2 bring-up gotchas).
> `manifests/` = k8s manifests; SSH helpers in `ops-tools/` — both relative to
> `projects/marketdata/` (this repo's root).

---

## 1. Architecture

```
                        ┌─────────────────────────────────────────────┐
                        │  SOURCES (internet)                         │
                        │  Yahoo Finance · NSE API · FRED · RSS feeds │
                        │  (FRED needs API key in Secret; rest keyless)│
                        └──────────────┬──────────────────────────────┘
                                       │ daily pulls (Pi workers)
                                       ▼
                        ┌─────────────────────────────────────────────┐
                        │  k3s CronJobs → TimescaleDB                 │
                        │  market-data namespace                      │
                        │  yfinance 22:30 · fx 23:00 · yields 23:15   │
                        │  rates 23:20 · news 23:45 · NSE 11:15       │
                        │  FII/DII 14:00 · TA 00:30 · backup 01:30    │
                        │  FDI watch monthly                          │
                        └──────────────┬──────────────────────────────┘
                                       ▼
  ┌─────────────────────┴───────────────────────────────────────────┐
  │ TimescaleDB 2.30.1 (StatefulSet, TX2, sda4 SSD)                │
  │ index_prices · fx_rates · bond_yields · interest_rates        │
  │ news_sentiment · technical_indicators · capital_flows         │
  │ raw_headlines (staging)                                       │
  └─────┬────────────────────────────────────────────┬────────────┘
        │ FinBERT scoring (TX2, 00:15)               │ dashboards (pi3)
        ▼                                            ▼
  news_sentiment (TX2 CPU, no GPU needed)    Grafana v12 (tailnet-only)
```

Data flow rule: fetchers only ever DELETE+INSERT their own (key, ts) rows —
re-runs and late corrections overwrite cleanly, never duplicate.

## 2. Inventory

### Nodes (all k3s Ready unless noted)

> **Addresses are not written in the docs.** `<pi2-ip>`, `<tx2-tailnet-ip>`, `<wan-ip>`
> etc. are placeholders; the real values live in the gitignored **`.hosts.env`** (repo
> root; template `.hosts.env.example`). `ops-tools/*.py` take node aliases
> (`pi0 pi1 pi2 pi3 tx2`) and resolve them from it; in your own shell:
> `set -a; source .hosts.env; set +a`, then e.g. `ssh pi2@$PI2_IP`.

| Node | Hardware | LAN IP | Tailscale IP | Role |
|---|---|---|---|---|
| pi0 | Pi 1 B+, 512 MB | <pi0-ip> | <pi0-tailnet-ip> (joined 2026-10-11) | Pi-hole v6.4.3 + unbound (DNS) |
| pi1 | Pi 3 B, 1 GB, 233 GB SSD | <pi1-ip> | <pi1-tailnet-ip> | k3s worker (ingestion pool) |
| pi2 | Pi 3 B+, 1 GB | <pi2-ip> | <pi2-tailnet-ip> | k3s control-plane — FRAGILE, see §8 |
| pi3 | Pi 3 B, 1 GB | <pi3-ip> | <pi3-tailnet-ip> | k3s worker; Grafana + pg-backup pinned here |
| ahytx2-desktop | Jetson TX2, 7.7 GB | <tx2-ip> (router DHCP reservation, confirmed 2026-10-03) | <tx2-tailnet-ip> | GPU/tainted worker; DB host |
| orin1 | Orin Nano, 8 GB | <orin1-ip> | — | k3s worker, tainted `gpu=orin:NoSchedule` (STT/TTS voice only — no market-data images) |
| orin2 | Orin Nano, 8 GB | <orin2-ip> | — | k3s worker (untainted — receives floating market-data jobs) |

- Per-node jobs, pinned workloads and blast radius: see `node-roles.md` (2026-10-11). Live check 2026-10-11: pi2 is
  **cordoned** (`SchedulingDisabled`) since 2026-10-10 06:11 UTC (taint `timeAdded`; set during the outage recovery — reason
  inferred, not recorded). Keep it cordoned.
- k3s: server v1.36.4+k3s1 (pi2); agents v1.36.4 on Pis, **pinned v1.35.3+k3s1 on TX2**.
- Admin machine in these examples: `<admin-host>` (<admin-tailnet-ip>), kubectl v1.36.5
  at `~/.local/bin`, kubeconfig `~/.kube/pi-cluster.yaml`.

### TX2 fixed configuration (do not lose)

- Agent flags: `--disable-apiserver-lb --kubelet-arg=fail-cgroupv1=false
  --kubelet-arg=cgroup-driver=cgroupfs --node-ip <tx2-ip>` (unit:
  `/etc/systemd/system/k3s-agent.service`; always re-check parsed ExecStart
  with `systemctl show` after editing — a dangling continuation line once
  nearly broke a restart).
- Labels `hardware=gpu`, `nvidia.com/gpu.present=true`; taint `gpu=true:NoSchedule`.
- containerd fragment
  `/var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.d/nvidia.toml`
  (nvidia runtime + `SystemdCgroup=false` for nvidia AND runc — kubelet and
  containerd drivers must match or `kubectl exec` breaks on systemd-237).
- iptables wrappers `/sbin/iptables-restore` + `/sbin/ip6tables-restore`
  backed by `/sbin/xtables-multi.orig` — re-verify after ANY apt work on the TX2.
- L4T R32.7.5 / JetPack 4.6.1 / CUDA 10.2 (host); kernel 4.9.337-tegra (cgroup v1).
- Storage: 28 GB eMMC (OS only since 2026-10-10 — 54% used; was 79% after the 2026-10-03 cleanup;
  CUDA/cuDNN/TensorRT libs in /usr are ~10 GB of that) + 224 GB USB SSD `sda4` →
  fixed fstab mount `/mnt/tx2-ssd` (by UUID, `nofail`; backup
  `/etc/fstab.bak-20260929`). **Docker data-root moved to `/mnt/tx2-ssd/docker`
  (2026-10-03, `/etc/docker/daemon.json`)** — image builds no longer touch eMMC
  at all; golden sentiment image migrated intact, build smoke-tested.
- **Docker must start AFTER the SSD mount** (found 2026-10-10): `docker.service`
  had no ordering on `/mnt/tx2-ssd`, and at the 2026-10-09 boot it started 2.5 s
  before the mount — it ran on the empty directory underneath, every build died
  at the first `RUN` with `failed to update store for object type
  *libnetwork.endpointCnt: Key not found in store`, and its tag index
  overwrote the real one (older `local/*` images on the SSD are now untagged
  `<none>`; layers intact, sentiment tarball still at
  `/mnt/tx2-ssd/sentiment-fetcher-v1.tar.gz`). Fix: drop-in
  `/etc/systemd/system/docker.service.d/10-ssd-data-root.conf`
  (`RequiresMountsFor=/mnt/tx2-ssd`) + `systemctl restart docker`. Image
  tarballs now go to `/mnt/tx2-ssd/image-tarballs/`, not the eMMC home dir.
- **k3s containerd store bind-mounted from the SSD (2026-10-10; verified same day —
  agent Ready, timescaledb Running, sentiment-scorer Completed on GPU,
  `kubectl exec` + psql OK):** `/var/lib/rancher/k3s/agent/containerd` ←
  `/mnt/tx2-ssd/k3s-containerd` (fstab bind line, backup `/etc/fstab.bak-20261010`);
  drop-in `/etc/systemd/system/k3s-agent.service.d/10-ssd-containerd.conf`
  (`RequiresMountsFor=` — the agent refuses to start without the store mounted;
  the unit file and its flags are untouched). The rsync'd copy matched the source
  (49,022 files, zero diffs). The eMMC copy and 19 stale image tarballs in
  `/home/ahytx2` were then deleted: **eMMC 84% → 54%** (14 G of 28 G). The TX2 now
  cannot run any pod without the USB SSD. To revert: stop agent, `umount`, remove
  the fstab line + drop-in, rsync the store back.

### Pi agents

- pi1/pi3 units also carry `--disable-apiserver-lb` (direct to pi2:6443, no
  flaky local-LB hop). Patched in place; stock k3s reinstalls would revert it.
- **Power (2026-10-10):** Pi 3 / 3B+ need a 5.1 V / ≥ 2.5 A supply and a short, thick cable. The USB-SATA
  SSD draws from the **same 5 V rail** as the board (micro-USB → polyfuse → rail → board + USB ports), so
  its bursts sag the Pi's input (firmware flags undervolt ≈ 4.65 V). The SSD is also the **boot** drive:
  to take it off the rail keep its data path direct (powered SSD enclosure, or separate-power Y-cable /
  data-only adapter); a hub in the boot path is unverified on Pi 3 / 3B+ — test on pi3 or a spare, never
  first on pi2. Triage ladder: skill `node-power-health`; plan: pending-tasks §1.
  **Current state (2026-10-17):** every Pi now has its own dedicated supply — the shared multiport block that caused the
  2026-10-09→10 outage is gone (rewire ~2026-10-12 02:30 UTC; pi1's supply replaced 2026-10-14). pi0/pi2/pi3 PASS the 24 h
  acceptance rule; pi1 import stress test clean 2026-10-14 06:19–06:21 UTC (`0x0` throughout, 0 dmesg undervoltage
  events); pi1 24 h acceptance re-trial pending — run `bash ops-tools/power-review.sh --since 2026-10-14T05:39:00Z`,
  verdict due ≥ 2026-10-15 05:40 UTC. Until acceptance: no bulk imports to pi1.

## 3. Workloads (namespace `market-data`)

> **Which node runs which job** (pinned vs floating CronJobs, host-level cron per node, last-run times):
> `node-roles.md` §3 (verified live 2026-10-11).

### Scheduled jobs — **schedules are NODE-LOCAL PDT, not UTC** (verified 2026-10-04:
the "02:00-02:30" screener chain fired at 09:00-09:30 UTC = 02:00 PDT). k3s's cron
controller uses server-local time when no CronJob timezone is set. Every "UTC"
time written in these docs has actually been firing at PDT — functionally fine
(each fetch still lands after its market close; the staggering order holds), so
schedules are left as-is; do not "fix" them without re-validating everything.

**Safeguards (review AR-1) — in the repo manifests since 2026-10-11, APPLIED to the cluster 2026-10-12 (to all 23 then-existing CronJobs, still suspended at the time; every later CronJob carries them — CI test).** Every CronJob now carries
`concurrencyPolicy: Forbid`, `startingDeadlineSeconds` (1 h for jobs whose next run recovers the data — their pulls cover 2–5
days; 10 min for hourly `intraday-fetcher`; 6 h for latest-snapshot feeds `fiidii`/`news`/`rbi` and the weekly jobs, where late
beats skipped; 24 h for monthly `fdi-watch`), `activeDeadlineSeconds` (3–10× measured runtime: 300 s intraday, 600 s fast
fetchers, 900 `rates`/`sentiment`/`universe-refresh`, 1200 `stock-metrics`/`stock-report`, 1800 `pg-backup`/`headline-ner`,
3600 `stock-screener`, 7200 `stock-prices`, 14400 `stock-fundamentals`) and `ttlSecondsAfterFinished: 604800` (7 days — long
enough to read a failed Job's logs). Evidence: on 2026-10-10 `intraday-fetcher` and `rbi-fetcher` ran 91,368 s (25 h) and
`sentiment-scorer` 9,128 s because nothing capped them. `headline-ner` (1800), `stock-screener` (3600), `stock-report` and
the two Telegram jobs have no clean measured runtime — their values are estimates: if one trips, raise it, no data is lost.
Applied with `kubectl apply` after a `kubectl diff` review; it did not change `suspend`. With a start deadline set, un-suspending fires only runs due inside the window (no catch-up storm).

| CronJob | Schedule | Image (node-local, Never pull) | Writes to |
|---|---|---|---|
| nse-fetcher | 15 11 * * * | local/marketdata-data:v1 (commits per calendar year) | index_prices (11 Indian indices: 9 via Yahoo incl. ^CRSLDX Nifty 500 + ^CNXAUTO/^CNXFMCG via NSE-direct indicesHistory since 2026-10-26 — the API caps 70 records/call, the fetcher paginates; MIDCAP150 pre-2019 history backfilled from NSE, Yahoo owns 2019-01-14→) |
| fiidii-fetcher | 0 14 * * * | local/marketdata-light:v1 | capital_flows (FII/DII net) |
| factors-fetcher | 0 11 2 * * | local/marketdata-light:v1 | factor_returns (US Ken French 5F+MOM, IN IIM-A four-factor — G-20; first mdjob/job_runs adopter) |
| membership-history | 30 11 2 * * | local/marketdata-data:v1 | index_membership (EV-1, ADR 0025: point-in-time S&P 500 intervals from Wikipedia; full idempotent rebuild) |
| stock-valuation | 10 2 * * * | local/marketdata-data:v1 | stock_valuations + sector_metrics (ADR 0026: 10-method intrinsic panel per symbol + daily sector aggregates; descriptive only) |
| prediction-tracker | 40 2 * * * | local/marketdata-data:v1 | prediction_snapshots + prediction_outcomes (schema 18: write-once daily claims, resolved at 30/91/182d vs adjusted returns, benchmark-relative) |
| ml7b-per-symbol | 45 2 * * 6 | local/marketdata-light:v1 | ML-7b per-symbol narration for shortlist (~40 names), content-hash cached; writes llm_narratives kind=per_symbol |
| earnings-calendar | 0 12 * * 0 | local/marketdata-data:v1 | earnings_calendar (schema 19: weekly yfinance refresh of upcoming earnings dates, all active constituents) |
| accuracy-check | 30 12 * * 0 | local/marketdata-data:v1 | stock_reports `__accuracy__` (DQ-5: valuation internals recomputed in SQL, sector medians, membership drift, Stooq second-source price sample) |
| research-watcher | 0 13 * * 1 | local/marketdata-light:v1 | research_items (ADR 0031: weekly paper collector — JF/JFE peer-reviewed feeds + NBER + arXiv; venue-tier + keyword scoring; append-only, never edits the curated Strategy Lab tiers) |
| yfinance-fetcher | 30 22 * * * | local/marketdata-data:v1 (commits per calendar year) | index_prices (29 tickers — `^RUI` Russell 1000 added 2026-10-10; **history from 1985** backfilled 2026-10-10 for all 38 index tickers: 292,387 rows, 299 chunks — 262 weekly for 2021-10 → 2026-10 plus one-year chunks for everything created since `set_chunk_time_interval('index_prices', '365 days')`; see `yfinance/backfill-job.yaml`) |
| fx-fetcher | 0 23 * * * | local/marketdata-data:v1 | fx_rates (10 pairs) |
| yields-fetcher | 15 23 * * * | local/marketdata-data:v1 | bond_yields (^TNX/^FVX/^TYX/^IRX) |
| rates-fetcher | 20 23 * * * | local/marketdata-data:v1 | interest_rates DFF + bond_yields DGS2/10/30 |
| news-fetcher | 45 23 * * * | local/marketdata-light:v1 | raw_headlines (RSS staging) |
| sentiment-scorer | 15 0 * * * | local/marketdata-ml:v1 (TX2 only) | news_sentiment |
| ta-compute | 30 0 * * * | local/marketdata-data:v1 | technical_indicators |
| pg-backup | 30 1 * * * | timescale/timescaledb:2.30.1-pg16 (pi3) | /var/lib/market-data/backups on pi3 |
| pipeline-health | 45 1 * * * | local/marketdata-light:v1 | pipeline_heartbeat (feed freshness, 14 feeds incl. rbi; weekend-safe 96 h on stock prices/scores since v5) |
| intraday-fetcher | 10 * * * * | local/marketdata-data:v1 | fx_rates (hourly FX) + intraday_prices (GC/SI/CL/DXY) |
| universe-refresh | 0 9 * * 0 | local/marketdata-data:v1 | universe_constituents + ticker_names (998 symbols) |
| stock-prices | 30 23 * * * | local/marketdata-data:v1 | stock_prices (998 symbols, 5d window) |
| stock-fundamentals | 0 6 * * 6 | local/marketdata-data:v1 | stock_fundamentals (EDGAR US + yfinance NSE, resumable) |
| stock-metrics | 0 2 * * * | local/marketdata-data:v1 | stock_metrics_daily |
| stock-screener | 15 2 * * * | local/marketdata-data:v1 | stock_scores + screening_runs (v8: house conn pattern, both universes — see §8) |
| stock-report | 30 2 * * * | local/marketdata-data:v1 | stock_reports (top-25/universe + __digest__; v8: today's shortlist only) |
| nse-fundamentals | 0 10 * * 0 | local/marketdata-data:v1 | financial_facts `nse_cons`/`nse_sa` — India results filings (G-6, ADR 0021); **suspended until the first backfill completes** |
| rbi-fetcher | 0 7 * * * | local/marketdata-light:v1 | interest_rates (7 IN_* policy rates) + bond_yields (5 G-Sec tenors + 3 T-bills) + pipeline_heartbeat self-row 'rbi' |
| headline-ner | 45 0 * * * | local/marketdata-light:v1 (TX2 only) | headline_entities + headline_events (gazetteer extraction over raw_headlines, ADR 0010) |
| fdi-watch | 0 9 1 * * | local/marketdata-light:v1 | logs only (manual INSERT follows) |
| telegram-digest | 0 8 * * * — **timeZone: Asia/Kolkata**, the only explicit-TZ job (ADR 0009) | local/telegram-digest:v39 (pinned pi1) | Telegram digest (read-only; no DB write) |
| telegram-alerts | 45 2 * * * (node-local, after the screener chain) | local/telegram-digest:v39, `args: alerts.py` (pinned pi1) | Telegram change alerts (read-only): screener top-20 entries/exits, watchlist moves/crossings/news events; silent when nothing changed (added 2026-10-10) |
| llm-narrative | 40 7 * * * (Asia/Kolkata) | local/marketdata-light:v1 | llm_narratives — ML-7a AI market note via the shared llm-gateway (ADR 0022); digest shows it only if every number checks out |
| news-embed | 0 1 * * * | local/marketdata-light:v1 | headline_embeddings — ML-2: embeds new headlines via the gateway `embed` capability, groups near-duplicates into stories (ADR 0024) |
| ml1-anomalies | 0 2 * * * | local/marketdata-data:v1 | data_anomalies — ML-1: return/volume spikes, zero-volume bars, feed row-count + freshness drops |
| ml3-volatility | 0 3 * * * | local/marketdata-data:v1 | vol_forecasts — ML-3: EWMA + GARCH(1,1) + GJR-GARCH volatility forecasts |

### Always-on / one-shot resources

- StatefulSet `timescaledb` (1×, TX2): 1 Gi req / 2 Gi lim RAM; PGDATA on PV.
- PV `timescaledb-tx2-ssd` 30 Gi, hostPath `/mnt/tx2-ssd/pgdata`, nodeAffinity
  TX2, ReclaimPolicy **Retain** (deleting a PVC never deletes data).
- Service `timescaledb` (headless): `timescaledb.market-data.svc.cluster.local:5432`.
- RuntimeClass `nvidia` (TX2 GPU smoke-test path; sentiment uses CPU, no class needed).
- Secrets: `timescaledb-auth` (postgres pw), `fred-api` (FRED key), `grafana-admin`,
  `telegram-bot` (bot token + chat id — applied out-of-band per ADR 0009, no
  manifest file).
- ConfigMaps: `timescaledb-schema`, `grafana-provisioning`, `grafana-dashboards`.
- Grafana Deployment (1×, pi3) + Service :3000; dashboards: Overview, Indices
  (ticker variable), FX-Commodities-Yields (incl. India yields panel, added
  2026-10-09), Sentiment-News, Capital-Flows, **Stock Screener** (added
  2026-10-09: universe variable, top-25 table, median-composite trend, 5
  top-10-by-factor panels).
  Alerting (2026-10-09, ADR 0009): Telegram contact point (Secret
  `telegram-bot`; chatid rendered in at apply time by
  `ops-tools/apply-grafana-provisioning.sh` — F29 — token env-expanded), policy tree → `telegram` receiver, 3 rules every 10m
  (stale feed, VIX>25, FII<−3000 Rs cr). NOTE: Grafana exits on any
  alerting-provisioning error and the section is all-or-nothing per file —
  a bad alerting.yaml crash-loops the pod (finds F29–F32).
- Telegram two-way listener (2026-10-09, Tier 2): Deployment `telegram-bot`
  (1×, pi1), `local/telegram-digest:v8` (since 2026-10-10: stock/watchlist/
  screener/world commands, telegram-bot doc §1a) with `args: bot.py` — the ONLY
  getUpdates consumer (Telegram 409s a second poller; the digest CronJob and
  Grafana only sendMessage). Multi-user (ADR 0012, v8): answers the owner + `bot_users`
  members, each with their own watchlist/digest/alerts; logs+ignores everyone else (owner gets one access notice per
  unknown chat per day; `/adduser`, `/rmuser`, `/users` are owner-only — telegram-bot doc "Managing users"). Commands: /status /vix /nifty /sensex /gold /fii
  /scores /digest /help — all read-only SELECTs, connection per query (F26).
  Backlog drained on start; offset in-memory (commands are idempotent).
- One-shot Jobs (kept as manifests, run on demand): `backfill-*-1y` (history
  seeding), `timescaledb-schema`, `timescaledb-write-test`, `restore-drill`.
  **Re-running the schema Job:** after editing `schema-configmap.yaml`, `kubectl apply` it, then run the Job under a NEW name
  (e.g. `sed '8s/name: timescaledb-schema$/name: schema-<tag>/' manifests/timescaledb/schema-job.yaml | kubectl apply -f -`;
  replace only line 8 — the ConfigMap reference further down must keep its name). The permission config denies deleting
  anything named `timescaledb…` (rule 5), so the old Job cannot be deleted and re-created. Schema 09 (multi-user bot) was applied
  this way on 2026-10-12 as `schema-multiuser-09`; a first attempt (`timescaledb-schema-09`, ConfigMap name rewritten by a too-broad
   **Schema 23 — ML tables (2026-10-31):** added `app.ml_runs`, `app.data_anomalies`,
   `app.vol_forecasts`, `app.regime_labels`, `app.peer_clusters`, `app.model_scores`,
   `app.llm_calls`, `app.llm_outputs` (all additive, idempotent CREATE IF NOT EXISTS).
   Applied as `schema-ml-23` via the same re-running procedure. Grants `pipeline_rw`
   write access on `app.*` (AR-8); `grafana_ro`/`telegram_app` keep SELECT-only via
   default privileges.

  sed) never started and was suspended, not deleted.
- Intraday (2026-10-03): `intraday-fetcher` hourly at :10 — yfinance 1h×2d
  candles; FX → `fx_rates` (hourly rows, coexists with daily), gold/silver/
  oil/DXY → `intraday_prices` (separate hypertable — `index_prices` stays
  pure-daily because ta-compute iterates every ticker over all rows).
  `ticker_names` reference table (46 rows) backs the named ticker dropdown.

### 3b. Externally-managed workload: marketview (2026-10-26)

The read-only web explorer (`local/marketview:v4`, Deployment + Service `marketview`,
pinned pi1, 192Mi limit) lives in the SIBLING REPO `../marketview` (ADR 0027) with its
own manifests and tests — `workloads.py --check` does not track it. DB access:
`grafana_ro`. Access: `kubectl -n market-data port-forward svc/marketview 8080:8080`.
Rebuild = marketview repo code change → tag bump → §6 cycle → import on pi1 only.
Its own runbook (deploy, rollback, recovery): `../marketview/docs/OPERATIONS.md`.
Remote: private GitHub repo `marketview` (pushed 2026-10-26).
Grafana dashboards (provisioned in `manifests/grafana/dashboards.yaml`, 11 as of
2026-10-26): overview · indices · markets · sentiment · flows · screener · derived ·
**valuation · sectors · dossier · factors** (the last four added with ADR 0026).

## 4. Data sources & feed catalog

| Source | Method | Status 2026-10-03 | Quirks found |
|---|---|---|---|
| Yahoo indices/FX/yields/futures | yfinance batch | ✅ live | `FTSE.MI` died → `FTSEMIB.MI`; `^CNXAUTO`/`^CNXFMCG` dead (ghost quote only); `^CRSLDX` (Nifty 500) RESTORED 2026-10-09 after live probe |
| NSE indices | yfinance validated symbols | ✅ 8/8 live | gaps above; jugaad-data not needed yet |
| FRED (DFF, DGS2/10/30) | fredapi + key in Secret | ✅ live | key verified 2026-10-03 (DFF 3.88) |
| News RSS | feedparser (6 feeds: ET, BS, LiveMint, BusinessLine, CNBC, MarketWatch) | ✅ live 2026-10-12 | Moneycontrol ×2 dead (302 to a login-consent wall for every user-agent) and Reuters dead (`feeds.reuters.com` no longer resolves) — replaced via the CronJob's `FEEDS` env override, no image rebuild; the script's `DEFAULT_FEEDS` is stale until the next rebuild |
| NSE FII/DII | fiidiiTradeReact JSON + session | ✅ live | API returns bare list, not `{data:…}` envelope |
| India fundamentals (G-6, ADR 0021) | NSE results filings: `/api/corporates-financial-results` list (2005–2024; archive HTML pages 2005–11, `/api/corporates-financial-results-data` JSON 2012–24) + `/api/integrated-filing-results` XBRL (2025→) | 🔄 backfill running 2026-10-18 | Session-cookie pattern (home page may 403 — the API still answers); 1.3 s pacing, session refresh every 300 calls, back-off on 401/403/429. Values in ₹ lakhs (×10⁵) except XBRL (₹). Revenue = `re_gro_inc` (net sales + other operating income); banks = total income; old pages print `0.00` for missing figures; missing filing date → period end + 60 d. Log: `NSE-FUNDAMENTALS-OK symbols= warn= filings= rows= partial=`; `MAX_MINUTES` stops cleanly with the cursor kept. |
| Stock prices/fundamentals (screener layer) | yfinance per-universe + EDGAR + yfinance stmts | ✅ live (2026-10-09) | **`stock_prices.ts` = 00:00 UTC on the trade date for EVERY row (normalised 2026-10-13, DQ-4)**; Yahoo back-adjusts splits/spin-offs but we refresh only 5 days — **since `screener:v10` (2026-10-14) the daily job pulls `actions=True` and, when a symbol shows a non-zero `Stock Splits` in the 5-day window, re-fetches and rewrites that symbol's full history (`SPLIT-RESYNC <sym> <date>x<ratio> rows=N` in the log; cap `SPLIT_RESYNC_MAX`=25; `PRICES-OK … splits_resynced=N`)**; **since `screener:v11` (2026-10-18) every split and dividend in the 5-day window is also upserted into `corporate_actions` (splits → `ratio`, dividends → `amount`; a dividend never triggers a resync; a failed side write logs `WARN <sym> … failed` and the prices still count). Since v12: `adj_close` = OUR total-return close (never Yahoo's `Adj Close`), recomputed for a symbol on a dividend in the window or a full rewrite (log: `PRICES-OK … anomalies=N adj_updated=N`); before overwriting, the window is checked against the DB — >40 % jumps and restated history (>0.5 %, the 2 newest bars exempt) go to `price_anomalies`, and restated symbols resync even without a split flag (`SPLIT-RESYNC <sym> restated`). Full re-derivation: Job `backfill-corporate-actions` (resumable). Screener reads EDGAR `financial_facts` since v13 (`FUND_SOURCE=facts`, stale series fall back to yfinance; `DRY_RUN=1` prints the shortlist without writing)**; the weekly `ops-tools/audit-price-adjustments.py` stays as the safety net (CTVA repaired 2026-10-13); `Ticker.history()` returns tz-aware local midnights, so `trade_ts()` forces 00:00 UTC on every write path (ADR 0013); Yahoo pre-open "today" bars (partial OHLC, ~0 volume) land for a handful of tickers each run; self-healing — the next run DELETE+INSERTs the final bar, but same-day metrics/scores can key to the partial bar. `DUMMYHEG.NS` dead (WARN-tolerant). |
| RBI/G-Secs | official RBI mobile "Current Rates" widget (lxml scrape) | ✅ live (2026-10-09, `rbi-fetcher`) | benchmark SECURITY names rotate — matched by maturity-year→tenor bucket; page hides a stale date in an HTML comment (lxml text_content drops it); values carry '#'/'*' markers |
| Headline intelligence | gazetteer extraction over raw_headlines (`headline-ner`, ADR 0010) | ✅ live (2026-10-09) | deterministic ticker_names matching; common-word first-tokens stoplisted (measured FPs: "live/target/realty"); 16-event keyword taxonomy; events link the single company mentioned |
| FDI monthly | watch + manual INSERT | 🔶 reporter only | DPIIT PDFs; 5-min quarterly task (template in job log) |
| TIC mirror stat | — | deferred | — |

## 5. Database reference

**DB facts (2026-10-18):** pod QoS `Guaranteed` (1 CPU / 2 Gi) with a `pg_isready` readiness probe (AR-4);
`stock_prices`/`index_prices` on 1-year chunks (old weekly chunks merged — see §8 "Slow queries"); roles
`postgres` (jobs), `grafana_ro` (SELECT, read-only), `telegram_app` (SELECT + `watchlist`/`bot_users`).
For read-only ad-hoc work prefer `grafana_ro` (password in Secret `grafana-db-ro`) — it cannot write by accident.

### Connect (password from `timescaledb-auth` Secret)

```bash
export KUBECONFIG=~/.kube/pi-cluster.yaml
PW=$(kubectl -n market-data get secret timescaledb-auth -o jsonpath='{.data.password}' | base64 -d)
```

**Option A — no local psql (runs inside the DB pod):**
```bash
# Row counts per table (health check)
kubectl -n market-data exec timescaledb-0 -- env PGPASSWORD="$PW" \
  psql -U postgres -d postgres -c "
SELECT 'index_prices', count(*) FROM index_prices UNION ALL
SELECT 'fx_rates', count(*) FROM fx_rates UNION ALL
SELECT 'news_sentiment', count(*) FROM news_sentiment UNION ALL
SELECT 'technical_indicators', count(*) FROM technical_indicators UNION ALL
SELECT 'capital_flows', count(*) FROM capital_flows;"

# Latest Nifty 50 close
kubectl -n market-data exec timescaledb-0 -- env PGPASSWORD="$PW" \
  psql -U postgres -d postgres -c "SELECT ts, close FROM index_prices WHERE ticker='^NSEI' ORDER BY ts DESC LIMIT 5;"

# 7-day sentiment trend
kubectl -n market-data exec timescaledb-0 -- env PGPASSWORD="$PW" \
  psql -U postgres -d postgres -c "
SELECT date_trunc('day', ts)::date AS day, round(avg(sentiment_score)::numeric,3)
FROM news_sentiment WHERE ts > now() - interval '14 days' GROUP BY 1 ORDER BY 1;"

# Latest FII/DII net flows (Rs cr)
kubectl -n market-data exec timescaledb-0 -- env PGPASSWORD="$PW" \
  psql -U postgres -d postgres -c "
SELECT flow_type, amount FROM capital_flows WHERE direction='net'
AND ts = (SELECT max(ts) FROM capital_flows);"
```

**Option B — local psql / Python (port-forward first, then connect to localhost):**
```bash
# Terminal 1: hold this open
kubectl -n market-data port-forward timescaledb-0 5432:5432

# Terminal 2: use any local tool
psql -h localhost -U postgres -d postgres -c "SELECT count(*) FROM index_prices;"
python3 -c "
import psycopg
conn = psycopg.connect(host='localhost', user='postgres', password='$PW', dbname='postgres')
import pandas as pd
print(pd.read_sql('SELECT ts, close FROM index_prices WHERE ticker=\'^NSEI\' ORDER BY ts DESC LIMIT 5', conn))
"
```

Health queries (copy-paste into either option above):
```sql
-- Row counts per table
SELECT 'index_prices', count(*) FROM index_prices UNION ALL
SELECT 'fx_rates', count(*) FROM fx_rates UNION ALL
SELECT 'news_sentiment', count(*) FROM news_sentiment UNION ALL
SELECT 'technical_indicators', count(*) FROM technical_indicators UNION ALL
SELECT 'capital_flows', count(*) FROM capital_flows;

-- 7-day sentiment trend
SELECT date_trunc('day', ts)::date AS day, round(avg(sentiment_score)::numeric,3)
FROM news_sentiment WHERE ts > now() - interval '14 days' GROUP BY 1 ORDER BY 1;

-- Latest FII/DII net flows (Rs cr)
SELECT flow_type, amount FROM capital_flows WHERE direction='net'
AND ts = (SELECT max(ts) FROM capital_flows);
```

## 6. Image pipeline (no registry — by design)

**Why no registry:** This is a homelab on CGNAT — no inbound ports, no external
DNS. The TX2 (aarch64) builds natively; Pi workers (also aarch64) just need the
tarball. `Never` pull policy makes missing images immediately visible.

**Credentials in these commands (2026-10-10):** `ops-tools/*.py` pick the right
password themselves from the gitignored `.secrets.env` (PI_PASS for the Pis, TX2_PASS
for the TX2). In a remote command, write sudo as `echo @@PW@@ | sudo -S …` — the
tool swaps in the shell-quoted host password at run time. Never type a password on
the command line; `SSH_PASS` in the environment still works as an override. Other `@@KEY@@`
tokens (see §10) work the same way; an unknown key is left literal with a stderr warning.
**Use a token only in a command that CONSUMES the value — never in one whose output is shown
(`echo`, `cat`): that would print the secret.** Covered by `tests/test_creds.py` (quoting
round-trips; a hostile value cannot inject a command).

**`runcmd.py` has a 120 s channel timeout** — any remote command that can exceed ~2 min
(docker builds from a cold cache, pi2 imports, backfills) must launch with
`nohup … >/tmp/x.log 2>&1 &` and be polled with `tail /tmp/x.log`; the TIMEOUT error from a
long foreground command does NOT mean the remote command failed (it keeps running).

**Full cycle — e.g., updating any feed in the consolidated data image (verified end-to-end for
the fx fetcher import, 2026-10-31; the step-by-step wrapper lives in `docs/deployment-guide.md`):**

```bash
# 1. Edit code in manifests/images/data/ (or light/ or ml/) — all scripts for that
#    image profile live there. Bump nothing: the image tag is v1; code changes go
#    into the same image and are re-built as a tarball.
#    (telegram: cronjob.yaml has TWO jobs + deployment.yaml — bump all three;
#     then python3 ops-tools/workloads.py --check to catch the doc tables too)

# 2. Ship the build context to a FRESH dir on the TX2 (a stale leftover file
#    from an earlier build silently ships old code):
python3 ops-tools/runcmd.py tx2 ahytx2 'rm -rf /home/ahytx2/md-build && mkdir -p /home/ahytx2/md-build && echo DIR-OK'
for f in Dockerfile requirements.lock yfinance-fetcher.py; do   # everything the Dockerfile COPYs
  python3 ops-tools/putfile.py tx2 ahytx2 manifests/images/data/$f /home/ahytx2/md-build/$f
done

# 3. Build on TX2 (native ARM64, no QEMU). Warm cache ≈ seconds; cold pip
#    layer can exceed the 120 s channel limit → nohup + poll (note above):
python3 ops-tools/runcmd.py tx2 ahytx2 \
  'echo @@PW@@ | sudo -S docker build -t local/marketdata-data:v1 /home/ahytx2/md-build 2>&1 | tail -2'

# 3b. Verify the image carries the code you think it does (build-before-commit
#     race: telegram v33 was built minutes before 36e6529 landed and shipped a
#     stale stocks.py/bot.py — caught 2026-10-30 by this check; the tag was
#     burned, never deployed). md5sum every COPY'd file in-image vs the repo:
python3 ops-tools/runcmd.py tx2 ahytx2 \
  'echo @@PW@@ | sudo -S docker run --rm local/marketdata-data:v1 md5sum /app/fx-fetcher.py 2>/dev/null'
md5sum manifests/images/data/fx-fetcher.py   # must match line for line

# 4. Save the tarball STRAIGHT INTO the archive (restore source if a node's
#    containerd store is lost — §8 prune incident), plus a home-dir copy to pull:
python3 ops-tools/runcmd.py tx2 ahytx2 \
  'echo @@PW@@ | sudo -S sh -c "docker save local/marketdata-data:v1 | gzip > /mnt/tx2-ssd/image-tarballs/marketdata-data-v1.tar.gz && cp /mnt/tx2-ssd/image-tarballs/marketdata-data-v1.tar.gz /home/ahytx2/marketdata-data-v1.tar.gz && chown ahytx2:ahytx2 /home/ahytx2/marketdata-data-v1.tar.gz" && ls -lh /home/ahytx2/marketdata-data-v1.tar.gz'
python3 ops-tools/runcmd.py tx2 ahytx2 \
  'cd /mnt/tx2-ssd/image-tarballs && echo @@PW@@ | sudo -S sh -c "sha256sum *.tar.gz > SHA256SUMS.all" && grep marketdata-data SHA256SUMS.all'

# 5. Pull the tarball to the admin machine:
python3 ops-tools/getfile.py tx2 ahytx2 \
  /home/ahytx2/marketdata-data-v1.tar.gz /tmp/marketdata-data-v1.tar.gz

# 6. Distribute — ONLY to the nodes the workload can schedule on:
#    · node-pinned images (telegram-digest, marketview → pi1): that node only
#    · TX2-only images (sentiment, headline-ner, …): skip this step entirely
#    · unpinned CronJobs: all Pi workers (pi1/pi2/pi3)
for h in pi1 pi3; do   # data image runs on pi1/pi3; light runs on TX2 too; ml is TX2-only
  python3 ops-tools/putfile.py $h $h /tmp/marketdata-data-v1.tar.gz /home/$h/marketdata-data-v1.tar.gz
  python3 ops-tools/runcmd.py $h $h "echo @@PW@@ | sudo -S /usr/local/bin/k3s ctr -n k8s.io images import /home/$h/marketdata-data-v1.tar.gz 2>&1 | tail -1 && rm /home/$h/marketdata-data-v1.tar.gz"
done
# pi1/pi3 imports block ~20-90 s (fine); pi2 MUST run detached or it wedges the session:
# python3 ops-tools/runcmd.py pi2 pi2 \
#   "echo @@PW@@ | sudo -S bash -c 'nohup /usr/local/bin/k3s ctr -n k8s.io images import /home/pi2/marketdata-data-v1.tar.gz >/tmp/yf-import.log 2>&1 &' && echo LAUNCHED"
# Poll: python3 ops-tools/runcmd.py pi2 pi2 "tail -2 /tmp/yf-import.log"
# Verify the import landed before applying anything:
python3 ops-tools/runcmd.py pi1 pi1 'echo @@PW@@ | sudo -S /usr/local/bin/k3s ctr -n k8s.io images ls | grep marketdata-data:v1 | head -1'

# 7. Apply + validate (cluster-mutation pre-flight first: pi2 load < 2, see §7 item 5):
kubectl apply -f manifests/ingestion/fx/cronjob.yaml
# Always-on images (telegram bot): also apply deployment.yaml — the tag change
# rolls the pod; a SAME-tag rebuild rolls nothing (imagePullPolicy: Never) and
# is why every code change needs a bump.
# Then the canary-validate skill checklist: verify the LIVE spec (jsonpath the
# image — a sed'd yaml is not a deploy), --from=cronjob canary, poll-capture
# logs before the pod vanishes, check the script's OK-marker:
kubectl -n market-data get cronjob fx-fetcher -o jsonpath='{.spec.jobTemplate.spec.template.spec.containers[0].image}{"\n"}'
kubectl create job -n market-data fx-validate --from=cronjob/fx-fetcher
kubectl logs -n market-data job/fx-validate --tail=10
# Expect: "fetched 10/10 pairs" + "wrote 10 rows into fx_rates"
kubectl -n market-data delete job fx-validate

# 8. Trust the DATABASE, not the exit code (silent-empty parsers exist):
export KUBECONFIG=~/.kube/pi-cluster.yaml
PW=$(kubectl -n market-data get secret timescaledb-auth -o jsonpath='{.data.password}' | base64 -d)
kubectl -n market-data exec timescaledb-0 -- env PGPASSWORD="$PW" \
  psql -U postgres -d postgres -c "SELECT count(*), count(DISTINCT pair) FROM fx_rates;"
```

**Image tarball store (2026-10-18; pruned to live images 2026-10-31):** `/mnt/tx2-ssd/image-tarballs/` on the TX2 holds a gzip
tarball of EVERY image a manifest references, checksums in `SHA256SUMS.all` — the restore source if a node's containerd store is
lost (incident §8). Step 4 above keeps it current; never skip the SHA256SUMS regeneration. **Post-AR-5 the archive holds only the
live images** — `marketdata-light:v1`, `marketdata-data:v1`, `marketdata-ml:v1`, `litellm-database:1.106.0`, `marketview:v12`,
`telegram-digest:v37` (6 tarballs, 1.4 GB, all `sha256sum -c` OK at prune time; the ~76 historical tags were removed after
verifying their checksums, and `SHA256SUMS.pi-only-images` went with them). Historical tags are no longer kept: the repo +
`requirements.lock` can rebuild any of them, and the live-image tarballs are what a store loss actually needs.

**TX2 docker build cache (2026-10-31):** the build store now holds exactly `local/marketdata-light:v1`, `local/marketdata-ml:v1`
and `local/litellm-database:1.106.0` — every pre-AR-5 per-fetcher tag, the old marketview/telegram tags, the 24 dangling build
layers and the base images (`python:3.11-slim`, `python:3.12-slim`, `ghcr.io/berriai/litellm-database:main-stable`) were removed
one tag at a time by name (7.5 GB → 3.1 GB; SSD 21 % → 14 %). Consequence: the next `marketdata-data` or litellm rebuild rebuilds
from a re-pulled base, and `marketdata-data:v1` now exists on TX2 only as a tarball plus pi1/pi3's containerd stores. The k3s
containerd store on TX2 was NOT touched — it still holds light + ml + litellm + timescaledb + system images, which is all TX2 can
schedule (only `headline-ner` and `sentiment-scorer` tolerate the `gpu=true:NoSchedule` taint). Removal used `docker rmi <name>`,
never a prune-all.

**Dependency locking:** After any build, dump the versions:
```bash
python3 ops-tools/runcmd.py tx2 ahytx2 \
  'echo @@PW@@ | sudo -S docker run --rm local/marketdata-data:v1 pip freeze 2>/dev/null | grep -Ei "^(yfinance|pandas|psycopg|numpy|requests)"'
```
Paste output into `manifests/images/data/requirements.lock` for reproducible rebuilds.

### Emergency: restore missing images to a node (2026-11-08 — all fetchers `ErrImageNeverPull`)

**Trigger:** every fetcher pod shows `ErrImageNeverPull` and `pipeline_heartbeat.last_check` is frozen days old.
This happens when a node's containerd store loses images (TX2 10-31 prune) or a new worker joins without them
(orin1/orin2 joined 2026-11-06 with zero `local/*` images). With `imagePullPolicy: Never`, k3s can never re-pull;
images must be imported from tarballs.

**Symptoms:** all CronJobs fail within seconds; `pipeline-health` stops writing `pipeline_heartbeat`;
Grafana stale-feed rule fires; backlog of unfilled data grows.

**Recovery (copy-paste as a block):**
```bash
export KUBECONFIG=~/.kube/pi-cluster.yaml

# 1. Confirm which images are missing on which node:
python3 ops-tools/runcmd.py pi1  pi1  "echo @@PW@@ | sudo -S /usr/local/bin/k3s ctr -n k8s.io images ls | grep -E 'marketdata|telegram'"
python3 ops-tools/runcmd.py pi3  pi3  "echo @@PW@@ | sudo -S /usr/local/bin/k3s ctr -n k8s.io images ls | grep -E 'marketdata|telegram'"
python3 ops-tools/runcmd.py tx2  ahytx2 "echo @@PW@@ | sudo -S /usr/local/bin/k3s ctr -n k8s.io images ls | grep -E 'marketdata|telegram'"
# A node with NO marketdata/telegram lines is missing everything.

# 2. Pull the missing tarball(s) from TX2 to the admin machine:
python3 ops-tools/getfile.py tx2 ahytx2 /home/ahytx2/marketdata-data-v1.tar.gz /tmp/marketdata-data-v1.tar.gz
python3 ops-tools/getfile.py tx2 ahytx2 /home/ahytx2/marketdata-light-v1.tar.gz /tmp/marketdata-light-v1.tar.gz

# 3. Distribute to each affected node and import (ONE TAG AT A TIME — NEVER prune-all):
for h in pi1 pi3; do
  python3 ops-tools/putfile.py $h $h /tmp/marketdata-data-v1.tar.gz /home/$h/marketdata-data-v1.tar.gz
  python3 ops-tools/runcmd.py $h $h "echo @@PW@@ | sudo -S /usr/local/bin/k3s ctr -n k8s.io images import /home/$h/marketdata-data-v1.tar.gz 2>&1 | tail -1"
  python3 ops-tools/runcmd.py $h $h "echo @@PW@@ | sudo -S rm /home/$h/marketdata-data-v1.tar.gz"
done
# pi2: must run detached (nohup) + poll — a foreground import wedges the session.
# orin1: SKIP — tainted `gpu=orin:NoSchedule`, STT/TTS voice only, no market-data images.
# orin2: import only if uncordoned and will receive floating jobs; find its SSH username first.

# 4. Verify imports landed:
python3 ops-tools/runcmd.py pi1 pi1 "echo @@PW@@ | sudo -S /usr/local/bin/k3s ctr -n k8s.io images ls | grep marketdata-data:v1 | head -1"
python3 ops-tools/runcmd.py pi3 pi3 "echo @@PW@@ | sudo -S /usr/local/bin/k3s ctr -n k8s.io images ls | grep marketdata-data:v1 | head -1"

# 5. Validate with one-off jobs (floating jobs land on pi1/pi3 once images are present):
kubectl create job -n market-data intraday-validate --from=cronjob/intraday-fetcher
kubectl wait --for=condition=complete job/intraday-validate -n market-data --timeout=120s
kubectl logs -n market-data job/intraday-validate --tail=10   # expect: fetched N/M symbols
kubectl delete job intraday-validate -n market-data

# 6. Confirm heartbeat advanced:
PW=$(kubectl -n market-data get secret timescaledb-auth -o jsonpath='{.data.password}' | base64 -d)
kubectl -n market-data exec timescaledb-0 -- env PGPASSWORD="$PW" \
  psql -U postgres -d postgres -At -c "SELECT feed, last_check, fresh FROM pipeline_heartbeat ORDER BY last_check DESC LIMIT 5;"
# Expect last_check within the last hour and fresh=true for active feeds.

# 7. Regenerate checksums on TX2 (rule 6):
python3 ops-tools/runcmd.py tx2 ahytx2 "echo @@PW@@ | sudo -S sh -c 'cd /mnt/tx2-ssd/image-tarballs && sha256sum *.tar.gz > SHA256SUMS.all'"

# 8. If orin2 was restored, uncordon it so the scheduler can use it again:
kubectl uncordon orin2
# Leave orin1 cordoned-by-taint (never un-taint — STT/TTS only).

**Never:** `crictl rmi --prune` / `ctr images prune` (deletes every CronJob image; `Never` pull cannot recover).
**Never:** remove tags with prune — one by one by name only.
**Never:** import market-data images to orin1 (tainted).

### 6b. Backfill rate-limit strategy (2026-11-08)

When backfilling multiple fetchers at once (e.g. after an image outage), yfinance and NSE
rate-limit per-IP. The order below avoids hitting limits on the slower feeds by doing
the fast fetches first, then the slow ones with gaps. All one-shot Jobs land on pi1/pi3
(orin2 cordoned unless its images are restored).

**Order (each is `kubectl create job --from=cronjob/<name>` → poll → check logs → delete):**

| # | Job | Rate-limit risk | Gap after |
|---|-----|----------------|-----------|
| 1 | fx-fetcher | low (10 pairs) | none |
| 2 | yields-fetcher | low (4 series) | none |
| 3 | rates-fetcher | low (4 series) | none |
| 4 | rbi-fetcher | low (RBI scrape) | none |
| 5 | nse-fetcher | medium (11 indices, paginated) | 2 min |
| 6 | stock-prices | high (2248 symbols, 5d window) | 5 min |
| 7 | yfinance-fetcher | high (32 index tickers) | 5 min |
| 8 | news-fetcher | low (RSS) | none |
| 9 | sentiment-scorer | medium (HF model download on cold start) | none |
| 10 | ta-compute | none (reads DB only) | none |
| 11 | stock-metrics | none (reads DB only) | none |
| 12 | stock-fundamentals | **highest** (2251 symbols, EDGAR + yfinance, resumable) | 10 min + monitor WARNs |
| 13 | stock-screener | none (reads DB only) | none |
| 14 | stock-report | none | none |
| 15 | stock-valuation | none | none |

**If NSE fundamentals hits "unavailable" on every filing** (as happened 2026-11-08 with ABREL.NS):
- Cancel the Job (`kubectl delete job backfill-nse-fund`).
- Wait 15–30 min, then retry a single-symbol manual run to confirm the rate limit lifted.
- The fetcher keeps a cursor; re-runs resume where they left off (no duplicates).

**If stock-fundamentals times out** (>10 min with no progress):
- It processes 2251 symbols sequentially on pi3 (1 GB RAM). Let it run — it's resumable.
- Do NOT launch another fundamentals Job in parallel (would double yfinance calls and compound rate limits).
- Check progress with `kubectl logs -f -n market-data -l job-name=backfill-fund`.

**Skip list (rate-limit safe to defer):**
- `nse-fundamentals` (suspended by design until DG-03 backfill completes — weekly Sun 10:00)
- `earnings-calendar` (weekly Sun 12:00)
- `ml1-anomalies`, `ml3-volatility` (daily 02:00/03:00 — skip if resource-constrained; retry next day)

**Verification after backfill:**
```bash
PW=$(kubectl -n market-data get secret timescaledb-auth -o jsonpath='{.data.password}' | base64 -d)
kubectl -n market-data exec timescaledb-0 -- env PGPASSWORD="$PW" \
  psql -U postgres -d postgres -At -c "SELECT feed, last_check, fresh FROM pipeline_heartbeat ORDER BY feed;"
# All feeds should show fresh=true with last_check within 24 h.
```

## 7. Daily operations checklist

```bash
export KUBECONFIG=~/.kube/pi-cluster.yaml

# 1. Cluster health — all 4 nodes Ready?
kubectl get nodes
# Expected: pi0 (not k3s), pi1/pi2/pi3/ahytx2-desktop all Ready

# 2. CronJob schedules present?
kubectl get cronjobs -n market-data
# Should show 36 CronJobs: nse-fetcher, fiidii-fetcher, yfinance-fetcher, … nse-fundamentals,
# fx-fetcher, yields-fetcher, rates-fetcher, news-fetcher, sentiment-scorer,
# ta-compute, pg-backup, pipeline-health, intraday-fetcher, fdi-watch,
# universe-refresh, stock-prices, stock-fundamentals, stock-metrics,
# stock-screener, stock-report, rbi-fetcher, headline-ner, telegram-digest,
# telegram-alerts, stock-valuation, prediction-tracker, earnings-calendar,
# accuracy-check, ml1-anomalies, ml3-volatility, ml7b-per-symbol,
# research-watcher, llm-narrative, news-embed, membership-history, factors-fetcher
# (SUSPEND=False on all — a True left over from §8 recovery means someone
# forgot to unsuspend)

# 3. Recent job runs — all Complete (not Failed)?
kubectl get jobs -n market-data | tail -12
# Look at STATUS column: should be 'Complete' for each recent run

# 4. Check last run logs for WARNs (yfinance example — replace name):
kubectl get jobs -n market-data | grep yfinance-fetcher | head -1 | awk '{print $1}' | xargs -I{} kubectl logs -n market-data job/{} --tail=10
# Scans for 'WARN' lines (feed deaths, stale symbols, partial data)

# 5. Heartbeat freshness — all feeds `fresh=true`, `last_check` within 24 h?
PW=$(kubectl -n market-data get secret timescaledb-auth -o jsonpath='{.data.password}' | base64 -d)
kubectl -n market-data exec timescaledb-0 -- env PGPASSWORD="$PW" \
  psql -U postgres -d postgres -At -c "SELECT feed, last_check, fresh FROM pipeline_heartbeat ORDER BY last_check DESC LIMIT 10;"
# If last_check is days old → all fetchers may be failing with ErrImageNeverPull (§8).
# If fresh=false on a feed → that fetcher's last run failed or hasn't run yet.

# 6. pi2 load (canary — investigate if sustained > 3):
python3 ops-tools/runcmd.py pi2 pi2 'uptime; free -m | head -2'
# If load > 3 sustained: stop all agents, cool pi2, restart one-by-one (see §8)
# Mutations (kubectl apply, rollouts, canary jobs) want load < 2 first —
# cluster-mutation skill. A hot-but-responsive pi2 (load ~3, swapping) after
# pod churn usually cools in minutes; wait rather than apply into it.

# 7. Backup freshness (should show today's dump):
python3 ops-tools/runcmd.py pi3 pi3 'ls -lh /var/lib/market-data/backups/ | tail -3'
# Expect: today's dump, ~165 MB since 2026-10-10 (was ~300-500K on 2026-10-03); a 0-byte file = a failed run

# 8. TX2 disk (alert if eMMC > 85%):
python3 ops-tools/runcmd.py tx2 ahytx2 'df -h / /mnt/tx2-ssd | tail -2'
# / (eMMC) alert above 85%; /mnt/tx2-ssd (sda4) has 140G+ free

# 9. Node stats (ADR 0011) — pull every node's CSV, eyeball the flags:
bash ops-tools/collect-node-stats.sh && tail -1 out/node-stats/*.csv
# Power acceptance verdict over a window (exit 0 = PASS; default window starts 2026-10-12T02:59Z):
bash ops-tools/power-review.sh --since 2026-10-12T02:59:00Z
# Red flags: throttle_hex != 0x0 on a Pi (power/thermal — bit0/1/2 = uv/
# cap/throttle NOW, bits 16-19 = since boot), k3s_state != active,
# temp_c > 70, swap_used_mb climbing, db_procs=0 on tx2 (DB down)

# 10. Repo gates — before ANY push, and after any manifest or doc change:
python3 ops-tools/workloads.py --check   # doc tables ↔ manifests (schedules, image tags, counts)
bash tests/run-all.sh                    # full unit suite; run whole, never piped through tail/head
```

**Grafana dashboards:**
```bash
kubectl -n market-data port-forward svc/grafana 3000:3000
# Open http://localhost:3000
# User: admin | Password: GRAFANA_ADMIN_PASSWORD in .secrets.env
# 11 provisioned dashboards (manifests/grafana/dashboards.yaml, counted 2026-10-30):
# Overview, Indices (named ticker variable — "Nifty 50 (^NSEI)" labels via
# ticker_names), Markets, Sentiment, Flows, Screener, Derived Metrics,
# Valuation, Sectors, Dossier, Factors
```
**⚠️ GRAFANA UI "NO DATA" FIX (required on first load):**
1. **Time picker (top-right) → "Last 90 days" / "All time" → Click Apply**
2. **Each panel: click ⋮ menu → ⟳ Refresh** (or dashboard ⟳ refresh button)
3. **Hard refresh: Ctrl+Shift+R** (clears cached "No data" state)

> **Why:** Grafana panels cache "No data" state and **do not auto-requery** when time range changes. Manual refresh is required. API queries work perfectly (validated).

## 8. Failure recovery runbook (copy-paste commands)

### Power acceptance trial (2026-10-12 02:59 → 10-13 03:51 UTC) — FINAL: pi0/pi2/pi3 PASS, pi1 FAIL
Rule and tool: `bash ops-tools/power-review.sh` (no live `throttle_hex` bit `0x1/0x2/0x4`, no new since-boot undervolt bit, no sample
gap > 15 min; heat is a WARN). Final 24 h review run 2026-10-13 03:51 UTC (299 samples per node, no gaps); verdict also in `pending-tasks.md` §1.

| Node | Final (02:59→03:51 UTC next day, 299 samples) | `dmesg` undervoltage events (since boot) | Note |
|---|---|---|---|
| pi0 | PASS (max 38 °C, load 1.13) | 0 | `0x0` |
| pi1 | **FAIL** — 4 live samples `0x50005` (04:35, 04:50, 09:10 UTC on 10-12; **03:10 UTC 10-13 at load 0.58**) | **36** (up ~1 day) | the first two during image imports; the others at near-idle with no transfer → its own supply/cable (or board) |
| pi2 | PASS (max 56 °C, load 2.19) | 0 | `0x80000` = heat since boot only |
| pi3 | PASS (max 52 °C, load 1.83) | 0 | held through the 170 MB `pg-backup` write at 08:30 UTC |

pi1 failed: swap-test ladder on pi1 (spare PSU + cable first; skill `node-power-health` §4), no bulk
imports/transfers to pi1 meanwhile. Never swap boot SSDs between nodes.

**2026-10-14 update:** pi1 hung ~02:02 UTC (last stats row `0x50000`), was power-cycled and rebooted 05:39 UTC; the owner reports it now has its
own supply (swap time/cable not recorded). Re-trial: `bash ops-tools/power-review.sh --since 2026-10-14T05:39:00Z`, verdict due ≥ 2026-10-15 05:40 UTC.
First read (06:11 UTC, idle): `0x0`, 0 undervoltage events — not evidence yet.

**Hardware fix recorded COMPLETE 2026-10-14** (owner confirmed; pending-tasks §1). Read at 06:49 UTC over
2026-10-14T05:39:00Z → 06:49:21Z (14 samples/node, no gaps) — **all four PASS**: pi0 `0x0` (36.9 °C, load 0.96) ·
pi1 `0x0` throughout (50.5 °C, load 1.17) · pi2 `0x80000` heat-only, WARN at 60.1 °C (load 2.32) · pi3 `0x0` (50.5 °C, load 1.93).
`dmesg` undervoltage since boot: **pi0 0 · pi1 0 · pi2 0 · pi3 0**. Positive but short — the 24 h verdict is still the acceptance.

**FINAL ACCEPTANCE 2026-10-20 04:12 UTC — PASS on all four nodes; the power item is CLOSED.** `power-review.sh --since 2026-10-18T06:20:00Z`
(~46 h window, 532–542 samples/node): 0 live hits on every node, last hex `0x0` everywhere; `dmesg` undervoltage since boot
pi0 0 · pi1 0 · pi2 0 · pi3 0 — pi1 under the full nightly CronJob load throughout. Heat WARNs only (pi1 66.6 °C, pi2 62.8 °C,
pi3 73.1 °C max): heat, not power, is the remaining hardware item (pending-tasks §1). The "no bulk transfers to pi1" caution is lifted.
Note: pi2 rebooted again ~2026-10-20 01:56 UTC, cause unknown (0 undervolt events, no persistent journal); healthy since.

**Persistent journald enabled on pi1 + pi2, 2026-10-20 (owner-approved)** so the next hang leaves a trace: drop-in
`/etc/systemd/journald.conf.d/90-persistent.conf` (`Storage=persistent`, `SystemMaxUse=200M`, `SystemKeepFree=1G`) +
`install -d -g systemd-journal -m 2755 /var/log/journal/<machine-id>`, restart `systemd-journald` then `systemd-journal-flush`.
**Gotcha: Raspberry Pi OS ships `/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf` (`Storage=volatile`), and drop-ins
apply in filename order across /etc and /usr/lib — a `10-*.conf` loses to it; the override must sort after `40-`.** An empty
`/var/log/journal` directory alone does nothing on RPi OS for the same reason. Verified: pi1 18 MB / pi2 8.1 MB flushed to disk,
`journalctl --list-boots` reads them, load unchanged. After the next mystery reboot: `journalctl -b -1 -e` on the node.
Rollback: delete the drop-in, restart journald.

### pi2 wedged ~16 h + live undervoltage found (2026-10-09→10) — full post-mortem
**Root cause (first catch of the node-stats logger, ADR 0011):** pi2 ran
with LIVE undervoltage + throttle flags (`vcgencmd get_throttled` = 0xd0005
at 62 °C; uv/throttle/soft-temp "since boot" bits all set; pi0 showed past
flags 0x50000 too). Throttled CPU → slow API → agent retry storms → 1 GB
swap-thrash → kine stall. **Check the Pis' PSU/cables before blaming
software.** pi2 also idles a desktop session (dbus/mpris) and
`netfilter-persistent` failed on its boot — hygiene items, not blockers.

**Timeline (UTC):** wedged from ~14:23 Oct 9 (agents saw 503 → timeouts);
TX2 agent retried ~15 h then EXITED failed 05:35 Oct 10 (agents do NOT
retry forever — gotcha below); DB — hence every pipeline write — down the
whole window (data safe, PV Retain). Whole-cluster reboots ~05:35 UTC
re-triggered the storm: agents auto-start against the cold server → k3s
swap peak 867 M → systemd SIGKILL → failed. pi2 alone rebooted again
06:22 UTC.

**SUSPEND-FIRST when only pi2 is up.** With no agents, the controller
catchups every missed job ONTO pi2 (fx/intraday/yields landed there within
4 min of the API answering; the 998-symbol stock-prices was minutes away):
```bash
for j in $(kubectl -n market-data get cronjobs -o name); do
  kubectl -n market-data patch "$j" -p '{"spec":{"suspend":true}}'; done
```
Suspend persists in etcd across k3s restarts — afterwards, stop/start k3s
is SAFE from catchup storms. If the workstation path flaps under load, run
the same loop ON pi2: `sudo /usr/local/bin/k3s kubectl ...` (localhost
answers when the NIC path doesn't).

**Agent gotcha:** k3s-agents that exhaust startup retries EXIT and stay
`failed` (NRestarts=0) — after any long control-plane outage each agent
needs a manual `systemctl start k3s-agent` (pi3 → pi1 → TX2, one at a
time, verify Ready between). Do NOT reboot the whole cluster to "fix"
them: boot auto-start = the storm again. Power-cycle pi2 ALONE.

**Stale Jobs to delete after recovery** (created mid-outage, pods never
ran, sit ACTIVE forever): the 2026-10-09/10 `nse-fetcher-*` (12 h) and
`telegram-digest-*` (4 h) Jobs, plus that day's failed catchup Jobs.

### pi2 wedged (SSH/API timeout, ping alive) — happened 2026-09-29
**Cause:** Agent registration/retry bursts against 1 GB RAM → swap-thrash → etcd/kine stalls.

```bash
# 1. Stop ALL agents to quiet retry storm
python3 ops-tools/runcmd.py tx2 ahytx2 'echo @@PW@@ | sudo -S systemctl stop k3s-agent'
python3 ops-tools/runcmd.py pi1 pi1 "echo @@PW@@ | sudo -S systemctl stop k3s-agent"
python3 ops-tools/runcmd.py pi3 pi3 "echo @@PW@@ | sudo -S systemctl stop k3s-agent"

# 2. Wait ~10 min — if pi2 still not responding to SSH, PHYSICAL POWER-CYCLE pi2
#    (pull power, wait 10s, plug back in)

# 3. Wait for pi2 to boot (USB-SSD boot takes 3-5 min). Verify calm:
python3 ops-tools/runcmd.py pi2 pi2 'uptime; free -m | head -2'
# Target: load average < 2

# 4. Restart agents ONE AT A TIME, verify each before next:
python3 ops-tools/runcmd.py pi3 pi3 "echo @@PW@@ | sudo -S systemctl start --no-block k3s-agent"
# wait 2 min, verify:
export KUBECONFIG=~/.kube/pi-cluster.yaml; kubectl get nodes | grep pi3

python3 ops-tools/runcmd.py pi1 pi1 "echo @@PW@@ | sudo -S systemctl start --no-block k3s-agent"
# wait 2 min, verify pi1 Ready

python3 ops-tools/runcmd.py tx2 ahytx2 'echo @@PW@@ | sudo -S systemctl start --no-block k3s-agent'
# wait 2 min, verify TX2 Ready

# 5. Final cluster check:
kubectl get nodes
# All 4 should be Ready. If any stuck activating >5 min: stop it, wait for pi2 calm, retry.
```

### CronJob still runs the OLD image after a code change (happened 2026-10-04)
**Cause:** sed-bumping the tag in `cronjobs.yaml` is not enough — the LIVE CronJob
object keeps the old spec until `kubectl apply` runs. The report job was
re-"validated" three times against a stale v1 image while the fix sat in v6.
```bash
# Always verify the LIVE tag, not the file:
kubectl -n market-data get cronjob <name> -o jsonpath='{.spec.jobTemplate.spec.template.spec.containers[0].image}'
# Then apply + re-validate:
kubectl apply -f manifests/stock-screener/cronjobs.yaml
kubectl create job -n market-data <name>-validate --from=cronjob/<name>
```

### DB errors "out of shared memory" / jobs die with "connection is closed" (2026-10-04)
**Cause 1 (fixed):** Postgres default `max_locks_per_transaction=64` exhausted under
concurrent hypertable access (EDGAR ingest + counts + reads). Fixed via StatefulSet
`args: ["postgres", "-c", "max_locks_per_transaction=256"]` — **args, NOT command**
(command replaces docker-entrypoint.sh and crash-loops the DB; forced pod deletion
recovers it). Verify: `SHOW max_locks_per_transaction` = 256.
**Cause 2 (pattern):** long-lived implicit transactions get dropped by the pod→DB
path during multi-minute fetches. House rule for all fetcher/scorer scripts:
`conn.autocommit=True` for reads, one `with conn.transaction():` per write unit,
reconnect-and-retry once on `OperationalError`. Health job also sets
`max_parallel_workers_per_gather=0` (parallel count workers multiply lock use).
**Recurrence (2026-10-05→09, fixed in screener v8):** `stock-screener` died
every night at the NIFTY500→SP500 boundary — `stock_scores` got 494 rows, then
`psycopg.OperationalError: the connection is closed` (line 105, first SP500
query), exit 1. The screener was the one script still on the old single-implicit-
transaction layout; the crash was invisible in the chain because metrics/report
still produced NIFTY-only output (reports ran "Complete"). v8 moved it onto the
house pattern (per-universe leg function + one retry). Diagnostic shortcut that
found it: `kubectl create job --from=cronjob/stock-screener` + poll-capture
logs — failed job pods vanish in ~1 min on k8s 1.36, so don't rely on
`kubectl logs job/…` after the fact.

### Agent stuck in `activating` + `Waiting to retrieve agent configuration`
```bash
# Check its pod/logs on the worker:
python3 ops-tools/runcmd.py pi1 pi1 'journalctl -u k3s-agent --since "10 minutes ago" | tail -20'
# If looping >5 min: stop it, cool pi2, retry once calm.
python3 ops-tools/runcmd.py pi1 pi1 "echo @@PW@@ | sudo -S systemctl stop k3s-agent"
# wait for pi2 calm (load <2), then start
python3 ops-tools/runcmd.py pi1 pi1 "echo @@PW@@ | sudo -S systemctl start --no-block k3s-agent"
```

### SSH "Authentication failed" on pi2 that was NOT a wrong password (2026-10-09)

Symptom: `ops-tools/runcmd.py` intermittently failed on pi2 with
`CONNECT FAIL: Authentication failed` while pi1/pi3 worked — looked exactly
like a password change. Evidence it was not: pi2's `journalctl -u ssh` showed
**zero "Failed password" events**, only `Connection closed by authenticating
user pi2 … [preauth]` (client-side aborts), interleaved with successful
`Accepted password` logins. Cause: `runcmd.py` used `auth_timeout=10`; pi2's
PAM auth under load sometimes exceeds that, and paramiko raises its generic
"Authentication failed" on timeout. Fix: ops-tools now uses 30s
connect/banner/auth timeouts. **Rule: diagnose SSH auth failures from the
SERVER journal, never from the client's error text.** kubectl debug node/pi2
(busybox + `chroot /host journalctl …`) is the rescue path when SSH itself is
down. Also: retry with backoff before concluding anything — the flap looked
clustered.

### `ErrImageNeverPull`
```bash
# Identify which node is missing the image:
kubectl get pods -n market-data -o wide | grep -E 'ErrImageNeverPull|ImagePullBackOff'
# Note the NODE column, then import there:
python3 ops-tools/runcmd.py <node: pi1|pi2|pi3|tx2> <user> "echo @@PW@@ | sudo -S /usr/local/bin/k3s ctr -n k8s.io images import /home/<user>/<image>.tar.gz"
# Note: must use `-n k8s.io` namespace (k3s default). pi2: run detached (nohup) + poll.
```

### Editing a multi-key ConfigMap YAML (dashboards-style) — the no-trailing-newline glue (2026-10-09)

Appending a new key to a ConfigMap file whose last line has **no trailing
newline** glues the new key onto the previous key's block-scalar VALUE —
`kubectl apply` succeeds, the new key doesn't exist, and the previous key
(e.g. `flows.json`) ships CORRUPTED content (Grafana keeps the old dashboard
only until its next re-provision). Fix pattern: open the file with Python,
split on `\n`, insert a standalone `  key: |-` line, and validate BOTH JSONs
before applying. Always verify the applied object, not just the file:

```bash
kubectl get configmap <name> -o json | python3 -c \
  "import json,sys; d=json.load(sys.stdin); [json.loads(v) for v in d['data'].values()]; print('all valid')"
```

### NSE/Yahoo symbol deaths
```bash
# Check job logs for WARN lines:
kubectl get jobs -n market-data | grep <job-name> | head -1 | awk '{print $1}' | xargs -I{} kubectl logs -n market-data job/{} --tail=20 | grep -i warn
# Confirm symbol via Yahoo search API:
curl -s "https://query2.finance.yahoo.com/v1/finance/search?q=<symbol>&quotesCount=5" | python3 -c "import json,sys; d=json.load(sys.stdin); [print(q['symbol'], '|', q.get('shortname','')) for q in d['quotes']]"
# Fix the symbol in the fetcher script, bump version, rebuild, redistribute (see §6).
```

### `kubectl exec` fails on TX2 pods
```bash
# Verify cgroupfs alignment on TX2:
python3 ops-tools/runcmd.py tx2 ahytx2 'echo @@PW@@ | sudo -S /usr/local/bin/k3s crictl info 2>/dev/null | grep -i "SystemdCgroup"'
# Must show "SystemdCgroup": false for BOTH runc and nvidia runtimes.
# Also check kubelet driver:
journalctl -u k3s-agent --since "1 hour ago" | grep -i "cgroup driver"
# Must show cgroupDriver="cgroupfs". If mismatch: fix both sides (see §2 TX2 config).
```

### iptables wrappers after TX2 apt work
```bash
# Quick check:
python3 ops-tools/runcmd.py tx2 ahytx2 'head -1 /sbin/iptables-restore'
# Must output "#!/bin/bash" and the wrapper must exec /sbin/xtables-multi.orig
# If missing: re-create from backup or see §2 TX2 fixed configuration.
```

### Grafana panels show "No data" but the DB has rows (happened 2026-10-03)
**Cause:** postgres datasource provisioning had `database: postgres` at the TOP LEVEL of
the yaml, but Grafana 12's postgres plugin only reads `jsonData.database`. Backend
queries still work (curl `/api/ds/query` returns data — misleading!), while the browser
plugin refuses to fire panel queries at all (console: "Postgres requires a default
database..."). Dashboards also lacked a `time` property, defaulting to Last 6h — daily
data at midnight timestamps renders empty either way.
```bash
# Check from a browser console (F12) — the tell-tale error:
#   "You do not currently have a default database configured for this data source"
# Fix (in manifests/grafana/provisioning.yaml):
#   jsonData:
#     database: postgres   # <- must be INSIDE jsonData, not top-level
# Then: bash ops-tools/apply-grafana-provisioning.sh && rollout restart grafana
# All 5 dashboards also now ship "time": {"from": "now-90d", "to": "now"}.
```

### k3s-server died under peak storm
```bash
# Check if API is down but agents are quiet:
kubectl get nodes  # if TLS timeout, pi2 is the bottleneck
# Check pi2 directly:
python3 ops-tools/runcmd.py pi2 pi2 'systemctl status k3s'
# If dead (exit 1) but Restart=always: it should self-restart. If stuck:
python3 ops-tools/runcmd.py pi2 pi2 'echo @@PW@@ | sudo -S systemctl restart k3s'
# Then verify agents re-register one-by-one (pi3 → pi1 → TX2).
```

### Slow queries on a hypertable = chunk count (found 2026-10-18)
`stock_prices` had 3,382 seven-day chunks back to 1961: `EXPLAIN ANALYZE` of an open-ended per-symbol read
showed **34 s planning, 0.2 s execution**. Fixed by 1-year chunks for new data (`06e-chunk-intervals.sql`) and
merging the old ones per year (`ops-tools/sql/2026-10-18-merge-weekly-chunks.sql`, after a verified dump;
merge_chunks is a PROCEDURE → `CALL`, chunk list passed via psql `\gset`): 107 chunks, 34 ms planning,
rows identical. Check: `SELECT hypertable_name, count(*) FROM timescaledb_information.chunks GROUP BY 1;`
and keep hot-path queries bounded in `ts` (AGENTS rule 9).

### NEVER `crictl rmi --prune` (or `ctr images prune`) on a node — incident 2026-10-18
With no registry, `crictl rmi --prune` deletes **every image not used by a running container** — i.e. every
CronJob image, which `imagePullPolicy: Never` can never re-pull. On 2026-10-18 it wiped all `local/*` images on pi3
and 6 on pi1 (fdi-watch, fiidii, fx, nse, rbi, yfinance) before a timeout stopped it. Recovered in ~30 min with no
failed job: pi3 cordoned at once (floating jobs went to pi1, which still had the screener/health images), images
re-sourced from the TX2 tarballs, `ctr images export` on pi1 and on pi2 (still holds old copies), checksummed at
each hop, imported, two pinned test pods run on pi3, then uncordoned.
```bash
# Remove ONE named old tag (safe) — and nothing else:
python3 ops-tools/runcmd.py pi3 pi3 'echo @@PW@@ | sudo -S /usr/local/bin/k3s ctr -n k8s.io images rm docker.io/local/screener:v8'
# Check before/after against what the manifests need:
kubectl -n market-data get cronjob,deploy -o jsonpath='{range .items[*]}{.spec.jobTemplate.spec.template.spec.containers[0].image}{.spec.template.spec.containers[0].image}{"\n"}{end}' | sort -u
# Emergency: an image is missing on a node → cordon that node first, then restore (export from a node that
# has it: ctr -n k8s.io images export f.tar docker.io/local/<img> → getfile → putfile → ctr import).
```
Also: `ps`/`pgrep -af` on a host while a `runcmd.py` command runs shows the expanded `@@PW@@` — never print them.

## 9. Backup & restore (copy-paste commands)

### Control-plane datastore (pi2) — daily second copy since 2026-10-18
k3s runs kine on SQLite (`/var/lib/rancher/k3s/server/db/state.db`), so `k3s etcd-snapshot` does not apply.
`ops-tools/pull-k3s-backup.sh` takes a consistent copy with Python's sqlite3 online-backup API (pi2 has no
`sqlite3` CLI), checks `PRAGMA integrity_check`, adds the server `token`, `server/tls` (CA + keys) and `server/cred`, pulls it to
`out/backups/k3s/k3s-cp-backup-<date>.tar.gz` (mode 600 — **contains the join token and the cluster CA private keys**), verifies sha256,
deletes the remote copy, keeps 14. Skips (exit 5) if pi2 load1 > 3. Workstation cron 03:30 local →
`out/logs/k3s-backup.log`; `pull-backups.py --prune-local 7` at 03:00 → `out/logs/pull-backups.log`.
**Restore (pi2 disk/datastore lost — untested drill, quiet window, agents stay up):**
```bash
# 1. on pi2 (same hostname/IP; k3s installed):  sudo systemctl stop k3s
# 2. putfile the archive, then:  tar -xzf k3s-cp-backup-<date>.tar.gz -C /tmp
# 3. sudo cp /var/lib/rancher/k3s/server/db/state.db /tmp/state.db.broken 2>/dev/null; \
#    sudo rm -f /var/lib/rancher/k3s/server/db/state.db-{wal,shm}; \
#    sudo cp /tmp/k3s-cp-backup/state.db /var/lib/rancher/k3s/server/db/state.db
# 4. same token + certs: sudo cp /tmp/k3s-cp-backup/token /var/lib/rancher/k3s/server/token; \
#    sudo cp -a /tmp/k3s-cp-backup/tls /tmp/k3s-cp-backup/cred /var/lib/rancher/k3s/server/
# 5. sudo systemctl start k3s → kubectl get nodes (agents keep trusting the restored CA and reconnect).
#    Never rehearsed — do a drill on a spare SD/SSD before relying on it.
```

### Verify backup ran (daily 01:30 UTC)
```bash
# Check pi3's backup directory (should show today's dump, ~165 MB; a 0-byte file = a failed run):
python3 ops-tools/runcmd.py pi3 pi3 'ls -lh /var/lib/market-data/backups/ | tail -5'
# Output should show marketdata-2026-10-03.dump with recent timestamp
```

### Manual backup (on demand)
```bash
# Trigger the CronJob immediately as a one-shot Job:
kubectl create job -n market-data pgbackup-manual --from=cronjob/pg-backup
# Wait ~30s:
kubectl get job -n market-data pgbackup-manual
kubectl logs -n market-data job/pgbackup-manual --tail=10
# Expect: BACKUP-OK + dump filename
```

### Restore drill (practice before you need it)

**Reusable timed drill (measured 2026-10-03):** `manifests/timescaledb/restore-drill-job.yaml`
restores the newest dump into a throwaway `drill_restore` DB, compares row counts, drops it.
```bash
kubectl apply -f manifests/timescaledb/restore-drill-job.yaml
kubectl logs -n market-data job/restore-drill --tail=5
# Measured 2026-10-03: RESTORE-DRILL-OK, pg_restore 2s @ 388K dump, live=drill=9076 rows.
# Re-measured 2026-10-12 on the real 171 MB dump (off-cluster, local timescale/timescaledb:2.30.1-pg16 container):
#   pg_restore 30 s, 0 errors, 12 hypertables / 3,703 chunks; 12/13 tables identical to live (6.34 M stock_prices rows);
#   the 13th differs by exactly the 3 rows universe-refresh added after the dump. Procedure: create DB → CREATE EXTENSION
#   timescaledb → SELECT timescaledb_pre_restore() → pg_restore (SERIAL — `-j` fails on the catalog foreign keys) →
#   SELECT timescaledb_post_restore(). The in-cluster restore-drill-job.yaml is unchanged and still the quarterly check.
# Pre-migration dump pattern (used 2026-10-13 before the DQ-4 rewrite): `kubectl -n market-data create job pg-backup-premig-<tag> --from=cronjob/pg-backup`
#   (same script, same-day file name — the scheduled run overwrites it atomically, so pull a copy first; its Job is TTL-cleaned after 7 days).
#   The restore drill was NOT re-run after that data rewrite (no schema change); re-run it at the next quarterly slot.
# Re-run 2026-10-17 in-cluster (restore-drill-job.yaml on pi3, 164.6 MB 10-17 dump): RESTORE-DRILL-OK, pg_restore 289 s,
#   index_prices live=drill=292,577. In-cluster RTO ≈ 5 min at this size (vs 30 s off-cluster on the workstation).
# Control-plane copy (2026-10-18, one-off; see pending-tasks AR-10): out/backups/k3s/k3s-cp-backup-<date>.tar.gz = state.db
#   (sqlite online-backup API, integrity ok) + server token. Mode 600 — it holds the cluster join token.
# Second copy: python3 ops-tools/pull-backups.py   (checksummed pull to out/backups; exit 2 if the newest verified dump is > 36 h old)
# Re-run quarterly, or after any schema/backup change.
```

**Manual restore (older procedure, still valid):**
```bash
# 1. Pick a dump file (from step 1 above, e.g., marketdata-2026-10-01.dump)
# 2. Create a temporary restore pod on pi3 (mounts backup dir + DB service):
kubectl run -n market-data restore-drill --rm -i --tty --image=timescale/timescaledb:2.30.1-pg16 -- \
  pg_restore -h timescaledb -U postgres -d postgres -c /backups/marketdata-2026-10-01.dump
# If you get "role postgres already exists" or similar, that's fine — -c cleans first.
# Verify restored data:
kubectl -n market-data exec timescaledb-0 -- env PGPASSWORD="$PW" \
  psql -U postgres -d postgres -c "SELECT count(*) FROM index_prices;"
```

### What the automated CronJob does (for reference)
- Schedule: `30 1 * * *` (01:30 UTC)
- Runs on pi3 (`nodeSelector: kubernetes.io/hostname=pi3`)
- Image: `timescale/timescaledb:2.30.1-pg16` (cached, no pull)
- Runs `pg_dump -Fc` to a temp name, verifies it (size ≥ half the previous dump and ≥ 100 KB, `pg_restore -l` readable
  with ≥ 100 TOC entries, `index_prices` data present), then atomically `mv`s it to
  `/backups/marketdata-$(date -u +%F).dump` and writes a `.sha256` sidecar. `concurrencyPolicy: Forbid`.
- Prunes only readable dumps beyond the newest 7; empty files are always removed; unreadable non-empty files are kept
  and logged (`WARN: unreadable`). A failed run leaves every existing dump untouched (`BACKUP-FAIL: <reason>`).
- **Manifest rewritten 2026-10-11 (AR-11); APPLIED + canaried ~23:40 UTC 10-11** (live script verified 2026-10-12; first dump
  171 MB with sidecar, checksum OK; CronJob still suspended). Test: `bash tests/test-pg-backup.sh` (21 checks, docker + a real dump in `out/backups/`).
- Writes to `/var/lib/market-data/backups/` on pi3's USB SSD (hostPath, survives pod restarts)

**Known flaws of the LIVE script (verified on pi3, 2026-10-10 — review AR-11; first four fixed in the repo manifest 2026-10-11, pending apply):**
- The newest file, `marketdata-2026-10-10.dump`, is **0 bytes** — a failed run during the outage. `pg_dump -f`
  leaves the empty file; `set -e` then skips pruning, so one failure is harmless.
- Pruning counts files by mtime and does not care if they are empty: after a run of failed days followed by a
  success, empty files crowd good dumps out of the 7-file window (after 7 consecutive failures no good dump
  survives).
- Nothing verifies a dump (no size floor, no `pg_restore -l`), and the write is not atomic.
- Dumps are now ~165 MB each (8 files = 792 MB; pi3's SSD has ~208 GB free), so the 388 KB / 2 s drill
  figures are stale.
- **Fix (manifest-only, no image change — apply when the cluster is calm):** dump to a temp name → require a size
  floor and a readable `pg_restore -l` → atomic `mv` → prune only verified dumps, keep ≥ 7 verified; copy each
  verified dump to a second node (pull-based, checksummed) and off-site; alert if the newest verified copy is
  > 36 h old; re-run the restore drill on a real dump.

### Retain safety
- The PV `timescaledb-tx2-ssd` has `persistentVolumeReclaimPolicy: Retain`.
- Data lives on TX2's sda4 USB SSD (independent from eMMC).
- Even `kubectl delete statefulset timescaledb -n market-data` followed by
  `kubectl delete pvc pgdata-timescaledb-0 -n market-data` **cannot delete the data files** on sda4 — they remain on disk until you manually wipe them.

## 9b. LLM layer (2026-10-18 — ADR 0022/0023; gateway repo `../llm-gateway`)
```bash
# Is today's AI note there, and did it pass the number check?
kubectl -n market-data exec timescaledb-0 -- env PGPASSWORD="$PW" psql -U postgres -d postgres \
  -c "SELECT ts, status, model, attempts, left(detail,80) FROM llm_narratives ORDER BY ts DESC LIMIT 5;"
# Run it now (idempotent per IST day):  kubectl -n market-data create job llm-narrative-now --from=cronjob/llm-narrative
# Ship a change to narrative.py / bakeoff.py:  ops-tools/apply-llm.sh
# Gateway health / real calls:  ../llm-gateway/ops/smoke.sh [--live]
```
`unavailable` = gateway/provider problem (the digest just omits the note); `invalid` = the model's numbers did not match
twice (never shown). Turn features off without a rebuild: `DIGEST_NARRATIVE=0` (CronJob telegram-digest),
`BOT_AI_RESOLVE=0` (Deployment telegram-bot).

Headline embeddings (ML-2, ADR 0024): `kubectl -n market-data create job news-embed-now --from=cronjob/news-embed`;
check `SELECT count(*), count(DISTINCT model) FROM headline_embeddings;`. Embeddings use `disable_fallbacks`
(vectors are model-specific); if the gateway's embed primary ever changes, new rows store the new model name and
old/new rows never compare — re-embed a day with the job if search looks thin. Ship script changes:
`ops-tools/apply-llm.sh` (ships narrative.py, bakeoff.py, factcheck.py, news_embed.py in one ConfigMap). Project key: Secret `llm-gateway-key` (rotate with
`../llm-gateway/ops/new-project.sh marketdata market-data fast-json,general,embed 10 0 --rotate`).

## 10. Security & secrets

- **One local secrets file: `.secrets.env`** (repo root; gitignored, mode 600; the
  committed `.secrets.env.example` lists the keys). It holds `PI_PASS`, `TX2_PASS`,
  `PGPASSWORD`, `GRAFANA_ADMIN_PASSWORD`, `FRED_API_KEY`, plus the out-of-band
  `PIHOLE_ADMIN_PASSWORD` / `TELEGRAM_*` / `LLM_*` (planned, ML-7). `ops-tools/*.py` read it automatically
  (host-aware); for your own shell: `set -a; source .secrets.env; set +a`.
  **Docs never carry values** — commands use the `@@PW@@` token (see §6), any
  `@@KEY@@` from `.secrets.env` (e.g. `@@TELEGRAM_BOT_TOKEN@@` for a direct
  getMe/sendMessage channel check), or the variable names above.
- **DB role Secrets (AR-8, 2026-10-18), created out-of-band, not in any file:** `grafana-db-ro` and
  `telegram-db` (keys `username`, `password`). Recreate/rotate one without printing it:
  ```bash
  P=$(python3 -c "import secrets,string;print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(32)))")
  kubectl -n market-data create secret generic grafana-db-ro --from-literal=username=grafana_ro \
    --from-literal=password="$P" --dry-run=client -o yaml | kubectl apply -f -
  printf "ALTER ROLE grafana_ro LOGIN PASSWORD '%s';\n" "$P" | kubectl -n market-data exec -i timescaledb-0 -- \
    env PGPASSWORD="$PW" psql -U postgres -d postgres -q; unset P
  # then restart the consumer (grafana: apply-grafana-provisioning.sh + rollout restart; bot: rollout restart)
  ```
  Grants are code (`10-roles.sql`); new tables are covered by default privileges.
- Secret manifests (`manifests/timescaledb|grafana|ingestion/rates/secret.yaml`) are
  gitignored too; `secret.yaml.example` files show their shape. Live values also
  live in the cluster. Credential rotation is not a current priority.
- Agents are denied reading `.secrets.env` (`opencode.jsonc`); never paste its
  contents into chat.
- Everything (Grafana, DB) is tailnet-only; CGNAT means no inbound ports exist
  by construction. Never expose Grafana publicly without auth + TLS work.
- Images contain zero secrets (all via env/secretKeyRef) — safe to rebuild freely.

## 11. Maintenance calendar

- **Daily (automated):** all CronJobs (§3); glance at §7 checklist.
- **Monthly (1st, automated + 5 min human):** `fdi-watch` logs DPIIT links →
  manual INSERT into `capital_flows` (template in the job log).
- **As needed:** rebuild/redistribute images on code changes (§6); prune TX2
  docker cruft if eMMC >85% (keep sentiment image + slim bases); rerun 1y
  backfills only if history is ever lost.
- **Repo hygiene (git, since 2026-10-10):** one-time `git config core.hooksPath .githooks` enables the
  pre-commit guard (gitleaks + exact local secret/address values + private-IP + forbidden-file checks).
  Before a push: `python3 ops-tools/workloads.py --check` (docs vs manifests), `bash tests/test-precommit-hook.sh`,
  `bash tests/test-nodewatch.sh`, `python3 tests/test_creds.py`.
- **Out-of-cluster watcher:** `ops-tools/nodewatch.sh` (ping every node + TCP-probe the API; Telegram message
  only on state change, 6 h reminders). Built + tested 2026-10-10. **Deployed on pi0 2026-10-11** (script in `/usr/local/bin`, `/etc/nodewatch.hosts` +
  `/etc/nodewatch.secrets` mode 600, root cron `*/5`, delivery test HTTP 200). Order matters: an empty/absent credentials
  file makes the script record a state change as handled without sending it, so enable cron only after the secrets file
  exists and a delivery test succeeds; `TELEGRAM_CHAT_ID` must be numeric. Install on pi0 (outside
  k3s): copy the script, create `/etc/nodewatch.hosts` (PI0_IP…TX2_IP) and `/etc/nodewatch.secrets` (mode 600:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID), cron `*/5 * * * *`. Dry-run anywhere: `bash ops-tools/nodewatch.sh --dry-run`.
- **Never without care:** TX2 k3s upgrade past v1.35 (cgroup v1 ceiling →
  control plane capped ~v1.38); TX2 apt upgrades (wrappers, §8); JetPack 5
  reflash (would fix cgroup/nftables properly — recovery-mode, full revalidation).

## 12. Known gaps & TODOs

1. ~~RBI G-Secs/repo rate — no stable feed~~ ✅ closed 2026-10-09: `rbi-fetcher`
   scrapes the official RBI mobile "Current Rates" widget daily (07:00 PDT);
   7 policy rates + 5 G-Sec tenor buckets + 3 T-bill cut-offs land in
   `interest_rates`/`bond_yields` (IN_* keys). Writes its own
   `pipeline_heartbeat` row ('rbi'); the health job's hard-coded FEEDS list
   adopts it at the next natural health-image rebuild. Note: the G-Sec rows
   carry the page's EOD as-on date (T-1), policy/T-bill rows the fetch date.
2. Nifty 500 / Auto / FMCG — **split 2026-10-09**: Nifty 500 restored via
   `^CRSLDX` (nse-fetcher v3, probed 21 valid bars fresh through the day).
   ^CNXAUTO / ^CNXFMCG / NIFTYAUTO.NS / NIFTYFMCG.NS all confirmed dead on
   Yahoo (current-quote ghost only, no history) — NSE direct (session-cookie
   API, like fiidii-fetcher) or jugaad-data are the only remaining avenues.
3. ~~Grafana alert rules not configured~~ ✅ 4 rules live (2026-10-12): stale feed, VIX > 30, FII < −5000 Rs cr, 7-day sentiment < −0.15,
   all to the Telegram contact point (telegram-bot doc §3.3). Known noise: the stale-feed rule counts every `fresh=false` heartbeat row, so it
   keeps firing while `stock_fundamentals` (weekly, next full pass Sat 10-17) and `stock_scores` are stale — it clears only after the next
   daily `pipeline-health` run (08:45 UTC) following the data. Re-tune FII/sentiment at ~60 days of history.
4. ~~TX2 LAN IP still DHCP~~ ✅ resolved 2026-10-03: router DHCP reservation
   (user-confirmed); host stays DHCP client by design.
5. news_sentiment / raw_headlines have NO unique constraints yet (INSERT-only
   writers; add with ON CONFLICT at the next scorer/fetcher image rebuild).
6. ~~pi0 not on tailnet~~ ✅ joined 2026-10-11 (tailscale 1.104.1 from the Raspbian apt repo;
   `tailscale up --accept-dns=false --accept-routes=false --hostname=pi0` so resolv.conf on the LAN's DNS host is untouched;
   node key expires 2027-04-09). Verified from pi1 over the tailnet: ping 31 ms, Pi-hole admin HTTP 200. **Not done:**
   DNS over the tailnet — Pi-hole `dns.listeningMode` is `LOCAL`, a query from pi1 over the tailnet timed out; ad-block DNS
   on the move also needs the tailnet nameserver set in the admin console. Left unchanged on purpose (changing the
   listening mode on a host behind a router with a stale port-forward needs the CGNAT check, §12.9, first).
   Router UDP 51820 forward leftover from WireGuard era is still open.
7. Longhorn/OpenEBS + Loki: deliberately declined (pi2 RAM ceiling).
8. ~~`rbi-fetcher:v1` image NOT yet imported on pi2~~ ✅ resolved 2026-10-09
   (same session): the "SSH Authentication failed" on pi2 was NEVER a wrong
   password — the server journal showed zero "Failed password" events. It was
   `runcmd.py`'s 10s `auth_timeout` aborting pi2's slow-under-load PAM auth
   (paramiko raises a generic "Authentication failed"). ops-tools now uses
   30s timeouts; rbi-fetcher:v1 imported + crictl-verified on pi2. Lesson in
   §8; do not diagnose SSH auth from the client error text alone.
9. **CGNAT claim unverified (2026-10-10).** The finds/architecture/decision-log conclusion
   ("CGNAT, no inbound ports by construction") rested on router-WAN ≠ externally-seen IP.
   Re-measured 2026-10-10: the address this LAN currently egresses with EQUALS the
   router WAN IP — the signature of a normal public address. Single check, one workstation
   (no VPN/exit node visible). **Re-measured 2026-10-20 (two IP-echo services, values
   compared programmatically, never printed): egress still equals the stored router
   WAN_IP, 10 days apart; the old EXTERNAL_SEEN_IP (the value the original CGNAT
   conclusion rested on) matches neither.** Working conclusion: NOT CGNAT — "no inbound
   ports by construction" is false. **UDP 51820 forward: RESOLVED 2026-10-20 — the owner
   checked the router admin panel and the port-forwarding page is EMPTY (no rules at all),
   so the stale WireGuard forward the old docs described no longer exists; nothing was
   reachable.** With no forwards and no other inbound path, the practical posture ("tailnet
   only, nothing listens from the internet") holds even without CGNAT. Final confirmation of
   the address question itself remains a glance at the router WAN page or a phone-off-Wi-Fi
   check. Real addresses: local `.hosts.env` only.

---

## Appendix A — Quick Reference (most-used one-liners)

```bash
# Full daily health check (copy-paste as a block):
export KUBECONFIG=~/.kube/pi-cluster.yaml
echo "=== NODES ===" && kubectl get nodes
echo "=== CRONJOBS ===" && kubectl get cronjobs -n market-data
echo "=== RECENT JOBS ===" && kubectl get jobs -n market-data | tail -10
echo "=== pi2 LOAD ===" && python3 ops-tools/runcmd.py pi2 pi2 'uptime; free -m | head -2'
echo "=== BACKUP ===" && python3 ops-tools/runcmd.py pi3 pi3 'ls -lh /var/lib/market-data/backups/ | tail -3'
echo "=== TX2 DISK ===" && python3 ops-tools/runcmd.py tx2 ahytx2 'df -h / /mnt/tx2-ssd | tail -2'

# Quick DB row counts:
export KUBECONFIG=~/.kube/pi-cluster.yaml
PW=$(kubectl -n market-data get secret timescaledb-auth -o jsonpath='{.data.password}' | base64 -d)
kubectl -n market-data exec timescaledb-0 -- env PGPASSWORD="$PW" psql -U postgres -d postgres -c "SELECT 'index_prices', count(*) FROM index_prices UNION ALL SELECT 'fx_rates', count(*) FROM fx_rates UNION ALL SELECT 'news_sentiment', count(*) FROM news_sentiment;"

# Grafana access:
kubectl -n market-data port-forward svc/grafana 3000:3000
# → http://localhost:3000 (admin / <GRAFANA_ADMIN_PASSWORD from .secrets.env>)

# Rebuild + redistribute a fetcher (yfinance example):
# 1. Edit script + bump tag in cronjob.yaml
# 2. Build on TX2 + save tarball
# 3. Distribute to pi1/pi2/pi3 + ctr import
# 4. kubectl apply cronjob.yaml + kubectl create job --from=cronjob + check logs + DB rows

# TX2 disk cleanup if eMMC > 85%:
python3 ops-tools/runcmd.py tx2 ahytx2 'echo @@PW@@ | sudo -S docker system prune -f && echo @@PW@@ | sudo -S docker builder prune -f && df -h /'

# Restart single agent (e.g., pi1):
python3 ops-tools/runcmd.py pi1 pi1 "echo @@PW@@ | sudo -S systemctl restart k3s-agent"

# Grafana "No data" fix:
# 1. Time picker (top-right) → "Last 90 days" / "All time" → Apply
# 2. Panel menu (⋮) → Refresh (⟳) to force re-query
# Panels cache "No data" state and don't auto-requery on URL time params
```

---

## Quick Start: See Data in Grafana (30 seconds)

```bash
# 1. Start port-forward
kubectl -n market-data port-forward svc/grafana 3000:3000

# 2. Open http://localhost:3000
#    User: admin
#    Password: <GRAFANA_ADMIN_PASSWORD from .secrets.env>

# 3. Fix "No data" (REQUIRED on first load):
#    a) Top-right time picker → "Last 90 days" → Apply
#    b) Each panel: ⋮ menu → ⟳ Refresh  (or top nav ⟳ dashboard refresh)
#    c) Hard refresh: Ctrl+Shift+R
```

---

## Appendix B — File locations (source of truth)

| Artifact | Path |
|---|---|
| **This index** | `marketdata/docs/README.md` — purpose of every doc + reading order |
| Design/plan | `marketdata/docs/market-data-pipeline-plan.md` |
| This manual | `marketdata/docs/market-data-pipeline-operations-manual.md` |
| Architecture reference | `marketdata/docs/market-data-architecture.md` |
| Hardening roadmap | `marketdata/docs/market-data-pipeline-architecture-roadmap.md` |
| Finds & project ideas | `marketdata/docs/market-data-finds-and-projects.md` |
| Stock screener plan (Nifty 500 + S&P 500) — v1 build order | `marketdata/docs/stock-screener-implementation-plan.md` |
| Fundamental analysis target architecture (v2+) | `marketdata/docs/nifty500_sp500_fundamental_analysis_implementation_plan.md` |
| Pi runbook (passwords, boot) | `piCluster/homelab setup/SETUP.md` |
| TX2 bring-up gotchas | `piCluster/homelab setup/TX2_AI_Edge_Lab_Setup_and_Test_Plan.md` |
| All manifests | `marketdata/manifests/` (namespace, DB, ingestion, intraday, health, sentiment, TA, Grafana, backup) |
| SSH helpers | `marketdata/ops-tools/` (`runcmd.py`, `putfile.py`, `getfile.py`) |
| Grafana validation scripts | `marketdata/validation/grafana/` (gitignored — hold the admin pw; never commit) |
| Dashboard screenshots | `marketdata/validation/screenshots/` |
| Real IP addresses (LAN / tailnet / WAN) | `marketdata/.hosts.env` (gitignored) — template: `.hosts.env.example` |
| All local secrets (Pi/TX2/DB/Grafana/FRED) | `marketdata/.secrets.env` (gitignored, mode 600) — template: `.secrets.env.example` |
| FRED API key | in cluster Secret `fred-api` (not in repo) |
| OpenCode setup guide | `piCluster/opencode-mcp-and-skills.md` (workspace root, above `projects/`) |
| OpenCode project setup | `marketdata/opencode.jsonc` (MCP + permissions), `marketdata/AGENTS.md`, `marketdata/.opencode/` (agents, commands, skills) |
