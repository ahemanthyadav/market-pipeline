# Node roles — who runs what, and who is responsible for what

> **Purpose:** one place that answers "what does each machine do, what breaks if it dies, and what must I
> never do to it". Detail stays in the linked docs; this file is the index per node.
> **Last verified: 2026-11-15** — live `kubectl`: 6 k3s nodes Ready (pi2 cordoned), **36 CronJobs, none suspended**;
> 30 of the 31 jobs started in the last 26 h completed `Succeeded` (0 failures; `nse-fundamentals` still `Running` at
> the check — long weekly job); `pipeline_heartbeat` 16/16 feeds `fresh`; pi3 backup `marketdata-2026-11-15.dump`
> present with `.sha256` sidecar; per-host `uptime`/`free`/`vcgencmd` on pi0/pi1/pi2/pi3; image lists read from
> containerd (`ctr -n k8s.io images ls`) on pi1/pi3 and from `node.status.images` on orin1/orin2.
> **Not re-verified 2026-11-15:** TX2 fixed config and TX2 eMMC/disk (SSH auth failed from this workstation — §5),
> per-Pi thermal detail, pi1 24 h power acceptance.
> The 2026-10-20 pi2 findings below still stand (reboot ~01:56 UTC, cause unknown, no journal trace; case removed
> 04:34 UTC, baseline 51.5 °C; persistent journald enabled on pi1+pi2).
> No IP addresses here (AGENTS.md rule 8): node names only; real values live in the gitignored `.hosts.env`.

## 1. At a glance

| Node | Hardware | In k3s? | Primary job | Pinned workloads | If it dies |
|---|---|---|---|---|---|
| **pi0** | Pi 1 B+, 512 MB, SD | **No** | LAN DNS (Pi-hole + unbound), out-of-cluster watcher | none (not a k8s node) | LAN ad-block DNS gone; the dead-man's switch goes silent |
| **pi1** | Pi 3 B, 1 GB, 233 GB SSD | Worker | Telegram bot + marketview host; general ingestion pool | `telegram-bot`, `telegram-digest`, `telegram-alerts`, `marketview` | Bot stops answering; digest/alerts not sent; unpinned jobs land on pi3/orin2. Power supply replaced 2026-10-14; 24h acceptance PASSED 2026-10-20 — item CLOSED (tracker §1). |
| **pi2** | Pi 3 B+, 1 GB | **Control plane** | k3s server (API, scheduler, datastore, CoreDNS, Traefik) | none (**cordoned**) | Whole cluster loses its API; nothing new schedules (see §4) |
| **pi3** | Pi 3 B, 1 GB, SSD | Worker | Grafana, backups, general ingestion pool | `grafana`, `pg-backup`, `restore-drill` (Job) | No dashboards, no nightly dump; unpinned jobs land on pi1/orin2 |
| **tx2** (`ahytx2-desktop`) | Jetson TX2, 7.7 GB, eMMC + 224 GB SSD | Worker (tainted `gpu=true`) | **The database**, NLP jobs, LLM gateway, image build host | `timescaledb`, `llm-gateway` (ns `llm`), `sentiment-scorer`, `headline-ner` | **Every pipeline write fails** (DB down); data is safe on the Retain PV |
| **orin1** | Jetson Orin Nano, 8 GB, 932 GB SSD, 1×GPU | Worker (tainted `gpu=orin`) | **STT/TTS voice layer only** — reserved, not yet deployed | `stt-service` (Whisper), `tts-service` (Piper) — *manifests exist, deployments not applied* | Voice input/output stays silent; Telegram text commands and marketview still work |
| **orin2** | Jetson Orin Nano, 8 GB, 476 GB SSD, 1×GPU | Worker (untainted) | **General ingestion pool** — where the floating CronJobs actually run | none (takes floating jobs) | Floating jobs reschedule to pi1/pi3; no pinned workload lost |

## 2. Per node

### pi0 — DNS + watcher (not a cluster node)
- **Does:** Pi-hole v6 + unbound for the LAN (DNS on :53, admin web on :80/:443); Tailscale (joined 2026-10-11,
  `--accept-dns=false --accept-routes=false` so it never rewrites its own resolver).
- **Host jobs (root cron, every 5 min):**
  - `/usr/local/bin/node-stats-log.sh` → `/var/log/node-stats/stats.csv` (ADR 0011). Fixed 2026-10-11 — the crontab was empty.
  - `/usr/local/bin/nodewatch.sh` → pings the cluster hosts + TCP-probes the k3s API, Telegram message only on state change
    (6 h reminder). Deployed 2026-10-11; uses its own dedicated bot; secrets in `/etc/nodewatch.secrets` (mode 600).
    It is the only alert path that does not live inside the cluster it watches. **As of 2026-11-15 the watch list still
    covers the original five hosts — orin1/orin2 were added 2026-11-05 and are not being pinged.** Tracker §1.
- **Not responsible for:** anything in k3s. It does not run an agent.
- **Watch:** 512 MB RAM — keep it lean (no extra daemons). Past throttle flags `0x50000` seen before 2026-10-11;
  `0x0` since the reboot; 0 undervoltage events in `dmesg` at 08:41 UTC 2026-10-12 (up ~6 h). Power: on its own supply since ~02:30 UTC 2026-10-12 (was the shared multiport charger; tracker §1).
- **Live 2026-11-15:** up 28 d, load 1.57/0.74/0.48 (highest relative load of any host — it is the LAN's DNS for
  every device), 426 MiB RAM (269 MiB available), Pi-hole v6 listening on :53 (UDP+TCP v4).
- **Open:** DNS over the tailnet (Pi-hole `listeningMode` is `LOCAL`), dedicated PSU, router UDP 51820 forward.

### pi1 — Telegram + marketview + ingestion worker
- **Pinned (nodeSelector `kubernetes.io/hostname: pi1`):** `telegram-bot` Deployment (long-poll listener, owner + `bot_users`
  allow-list — ADR 0012), `marketview` Deployment (web app — ADR 0027, lives in the sibling repo `../marketview`),
  CronJobs `telegram-digest` (08:00 IST — `timeZone: Asia/Kolkata`) and `telegram-alerts` (02:45).
  Pinned so image imports are needed on one node only.
- **Unpinned jobs:** eligible for any of the **31 floating CronJobs** (§3.1) — **only if the image is imported on pi1** (no registry).
  Since 2026-11-13 the scheduler prefers **orin2** for floating work (it holds the newest `local/*` images and has the most
  headroom), so pi1 now mostly runs its pinned workloads; it remains a valid fallback if orin2 is out.
  `telegram-digest:v42` (2026-11-12) and `marketview:v14` on pi1 only (pinned).
- **Images on the node (2026-11-15, authoritative `ctr -n k8s.io images ls`):** pi1 holds the **full set** — `marketdata-data`
  v1–v8, `marketdata-light:v1`, `marketview` v1–v14, `screener` v10–v18 + v20–v28, `telegram-digest` v8–v22 + v34–v42,
  `yfinance-fetcher` v5/v6, `nse-fetcher` v4/v7, `fx-fetcher:v1`, `intraday-fetcher:v1`, `rates-fetcher:v1`,
  `yields-fetcher:v1`, `ta-compute:v1`, `news-fetcher:v1`, `rbi-fetcher:v1`, `fiidii-fetcher:v2`, `factors-fetcher:v1`,
  `fdi-watch:v1`, `research-fetcher:v1`, `pipeline-health` v4–v6.
  Old tags are kept for rollback — remove **one by one, by name**; never `--prune`
  (AGENTS.md rule 6; the 2026-10-18 prune incident).
  ⚠ **Use `ctr -n k8s.io images ls`, not the kubelet's `node.status.images`** — the kubelet list is an incomplete
  view (on 2026-11-15 it showed pi1 as *missing* `telegram-digest:v42` and `marketdata-light:v1`, both of which are
  present in containerd and in daily use).
- **Host jobs:** node-stats logger — root cron added 2026-10-11 (the crontab was empty; AR-12).
- **Live 2026-11-15:** up 28 d 13 h, load 0.73/0.64/0.62, 905 MiB RAM (378 MiB available), `throttled=0x0`,
  `/` at 8 % (17 G of 229 G).
- **Power (2026-10-14):** pi1 was the only Pi failing the power review (live undervolt + throttle `0x50005`, 36 dmesg events). Supply replaced 2026-10-14 (~02:30 UTC). Import stress test
  2026-10-14 06:19–06:21 UTC (116 MB screener:v10 tarball push + ctr import, load peaked 2.78): **`0x0` throughout, 0 dmesg undervoltage events**. 24 h acceptance PASSED 2026-10-20 — item CLOSED (tracker §1). Bulk imports on pi1 are now permitted, though it remains the tightest-RAM worker.
- **Also holds:** a leftover `screener:v9` tarball in the SSH user's home (tracker §1). Planned second copy of DB dumps (AR-11) — not done.
- **Watch:** accumulates undervolt events; was NotReady during the 2026-10-10 wedge. Agent runs with
  `--disable-apiserver-lb` (direct to the control plane).

### pi2 — control plane (fragile)
- **Does:** k3s server — API on :6443, scheduler, controllers, datastore (kine/`state.db`), CoreDNS,
  Traefik, metrics-server, local-path-provisioner.
  **Control-plane backup exists** (not "none"): first copy 2026-10-18 via Python's `sqlite3` online-backup API
  (pi2 has no `sqlite3` CLI), `PRAGMA integrity_check` ok, 2,169 kine rows, archive pulled to
  `out/backups/k3s/` (gitignored, mode 600 — **holds the cluster join token**), sha256 verified both ends;
  **recurring** since 2026-10-18 via `ops-tools/pull-k3s-backup.sh` (workstation cron 03:30 local, load gate
  skips if pi2 load1 > 3, keeps 14), restore steps in ops manual §9.
  ⚠ **The restore has never been rehearsed** — drill it on a spare SSD before relying on it (AR-10).
- **Scheduling:** **cordoned (`SchedulingDisabled`)** — no workload pods run on it. The `node.kubernetes.io/unschedulable`
  taint carries `timeAdded` **2026-10-10 06:11:25 UTC**, i.e. during the 2026-10-09→10 outage recovery (agents being
  rejoined), so it was most likely set then to keep workloads off the 1 GB control plane. The *reason* is inferred from
  timing, not recorded; confirm with the owner. Keep it cordoned: 1 GB RAM, and it has wedged under load twice.
- **Rules (AGENTS.md rule 2):** never restart more than one agent at a time; if load > 3 sustained, follow ops manual §8
  cool-down first. This node's health gates everything else.
- **Power:** the node that showed live undervolt + throttle (`0xd0005`) and wedged ~16 h on 2026-10-09→10. On its own supply since ~02:30 UTC 2026-10-12. Six+ reboots under the shared multiport block on 10-10 were closed with the power fix 2026-10-14. Power review PASS 2026-10-12 02:59→08:41 UTC (0 live hits, max 56 °C, max load 1.74, 0 `dmesg` undervoltage events). **Rebooted again ~2026-10-20 01:56 UTC, cause unknown, 0 undervolt events, no persistent journal trace.** Final 24 h power review PASS 2026-10-20 04:12 UTC (~46 h, 532–542 samples/node, all four nodes PASS).
- **Heat:** flag `0x80000` (soft-temp since boot, no undervolt). Max 69.3 °C observed on 10-18 power review. **Owner removed pi2's case 2026-10-20 04:34 UTC; baseline right after: 51.5 °C, `0x0`, load 0.6.** Heat item still open for airflow/heatsink consideration on pi1/pi2/pi3.
- **Host jobs:** node-stats logger — root cron added 2026-10-11 (crontab was empty; AR-12). Throttle flags `0xd0000` on
  2026-10-11 (undervolt, throttle and soft-temp limit since boot, none live); 133 MB RAM available, 77 MB swap used.
  **Persistent journald enabled on pi2 2026-10-20 (200 MB cap, ops manual §8) so future hangs leave a trace.**
- **Live 2026-11-15:** up 10 d 5 h (rebooted 2026-11-05), load 0.50/0.56/0.59, `throttled=0x80000` (soft-temp flag only —
  no undervolt), 905 MiB RAM with **751 MiB used / 153 MiB available**, `/` 233 GB. `America/Los_Angeles` TZ — **the
  CronJob controller's timezone, which sets every node-local schedule** (see §3.1).
- **Watch:** desktop session idling on the control plane (ADR 0011 finding); SSH auth can be slow under load —
  `runcmd.py` uses 30 s timeouts for that reason (ops manual §12.8).

### pi3 — Grafana + backups + ingestion worker
- **Pinned:** `grafana` Deployment (tailnet-only; dashboards + the Telegram alert rules), CronJob `pg-backup`
  (01:30; dumps to a hostPath on pi3's SSD), the `restore-drill` Job (backups live on pi3's SSD).
- **Unpinned jobs:** a valid landing zone for all 31 floating CronJobs (§3.1) as long as the image is imported here.
  Since 2026-11-13 the scheduler prefers **orin2** for floating work; pi3 keeps the Grafana + backup roles and holds
  the rollback image set (`marketdata-data` v1–v8, `marketdata-light:v1`, `telegram-digest` v34/v37/v42, `timescaledb`).
- **Responsibility:** the node-side copy of the DB dumps. `pg-backup` ran on schedule 2026-11-15 01:50 PST
  (`marketdata-2026-11-15.dump`, 539 MiB, `.sha256` sidecar at 01:52) — 28 dump files retained on the SSD.
  A second copy is pulled to the workstation with `ops-tools/pull-backups.py`; an off-site copy is still open (tracker §1, AR-11).
  Also holds the in-cluster **k3s control-plane backup** (`k3s-cp-backup-<date>.tar.gz`, mode 600, keeps 7) pushed
  nightly by `ops-tools/pull-k3s-backup.sh` — a fast-path restore landing zone independent of the workstation copy.
- **Host jobs:** node-stats logger — was broken (empty `stats.csv`, empty crontab); root cron added 2026-10-11 (AR-12).
  Throttle flags `0x50000` (since boot, none live) on 2026-10-11.
- **Power:** its own supply since ~02:30 UTC 2026-10-12; review PASS — `0x0`, 0 `dmesg` undervoltage events, and it held through the
  first scheduled 170 MB `pg-backup` write at 08:30 UTC (the trigger that coincided with its network drop on 10-11) (tracker §1).
- **Live 2026-11-15:** up 28 d 13 h, load 0.37/0.42/0.43, 905 MiB RAM (464 MiB available), `throttled=0x0`,
  `/` at 9 % (20 G of 229 G).

### tx2 — database, NLP, gateway, build host
- **llm-gateway (2026-10-18):** the shared LLM gateway pod (namespace `llm`, ~650 MiB, its own `litellm` DB on the same TimescaleDB) — see `../llm-gateway`.
- **DB pod (2026-10-18):** QoS `Guaranteed` (1 CPU / 2 Gi requests = limits) + readiness probe (AR-4) — the kubelet
  evicts sentiment/NER pods before the database under memory pressure. Holds the image tarball store for every live image.
- **Pinned:** `timescaledb-0` StatefulSet (PV `timescaledb-tx2-ssd` on `sda4`, **Retain** — never delete casually),
  `llm-gateway` Deployment, CronJobs `sentiment-scorer` (00:15, FinBERT on CPU) and `headline-ner` (00:45, gazetteer
  matching → `headline_entities` and `headline_events`). Also the GPU smoke-test Pod (manual).
- **Scheduling:** label `hardware=gpu`, taint `gpu=true:NoSchedule` — only jobs that tolerate it land here, so unpinned
  CronJobs never do.
- **Build host:** all `local/*` images are built here (Docker, data-root on the SSD) and shipped as tarballs to the Pis
  (`/mnt/tx2-ssd/image-tarballs/`; the §6 cycle, `/rebuild`).
- **Fixed config — do not "clean up"** (ops manual §2): agent flags, containerd fragment, `/sbin/*tables-restore` wrappers,
  k3s pinned `v1.35.3+k3s1` (cgroup v1 ceiling), Docker `RequiresMountsFor=/mnt/tx2-ssd`, k3s containerd bind-mounted from
  the SSD. The node cannot run any pod without the USB SSD. Re-verify after ANY apt work.
- **Host jobs:** node-stats logger (also records `db_procs`, the DB-up signal) — root cron added 2026-10-11 (crontab was
  empty; AR-12).
- **Watch:** eMMC alert at > 85 % (**60 % after the 2026-10-10 move** — verified 2026-11-15 from inside `timescaledb-0`; `/dev/mmcblk0p1` 15.5 G used of 27.4 G, ~60 %); DB + 4 Gi scorer share 7.7 GB RAM. SSD (`/dev/sda4`, pgdata) at 16 % (23 G of 157 G).
- **GPU is NOT exposed to Kubernetes here:** the node carries the GPU-present label but advertises **no GPU
  allocatable capacity** — the NVIDIA device plugin runs only on orin1/orin2. TX2's GPU is reached through the GPU
  RuntimeClass path (GPU smoke test), not via device-plugin resource requests. Do not write a manifest that requests
  the NVIDIA GPU resource with `nodeSelector: hardware=gpu` — it will never schedule.
- **2026-11-15:** SSH from the admin workstation **failed authentication** (`runcmd.py tx2`), so TX2 disk/eMMC %, load and
  the fixed-config checks were NOT re-verified today. The DB itself is healthy (`timescaledb-0` Running, 16/16 heartbeat
  feeds fresh) — this is a credential gap, not a TX2 outage. Tracker §1.

### orin1 — STT/TTS voice inference (reserved, not yet running)

- **Does:** reserved for `stt-service` (Whisper `tiny`/`base` via faster-whisper with CUDA) and `tts-service` (Piper),
  exposed inside the cluster only via ClusterIP Services (`stt-service.market-data.svc.cluster.local:8000`,
  `tts-service...:8001`).
- **Labels/taints (live):** `hardware=orin-cpu`, `tier=saas`, 1× GPU allocatable, taint **`gpu=orin:NoSchedule`**.
- **Status 2026-11-15: the voice layer is NOT deployed.** `stt-service`/`tts-service` have no Deployment in the
  cluster, so orin1 currently runs **nothing but kube-system pods** (the NVIDIA device plugin + the Traefik service LB).
- **Live 2026-11-15:** k3s-agent active, Ready, v1.37.2+k3s1, up 10 d 12 h, load 0.02, 854 MiB peak (k3s agent),
  GPU present but idle — no CUDA processes, no GPU memory in use. Running a desktop session (Firefox, GNOME shell,
  Xorg) in addition to k3s — the node doubles as a GUI workstation. **Passwordless SSH now works from the admin
  workstation via `ssh orin1`.** Joined the tailnet 2026-11-05; Tailscale IP reachable.
- **⚠ Selector/label mismatch — the manifests would not schedule.** `manifests/voice/stt-service-deployment.yaml` and
  `manifests/voice/tts-service-deployment.yaml` select `nodeSelector: {hardware: orin}` and tolerate
  `hardware=orin:NoSchedule`, but **both live Orin nodes are labelled `hardware=orin-cpu`** and the taint key is
  `gpu=orin`. Applying the voice manifests as written leaves the pods `Pending` forever. Fix the selector/taint in the
  manifests (or the node labels) before deploying — tracker §1.
- **Not responsible for:** TimescaleDB (stays on TX2), ingestion fetchers, Grafana. Orin is voice-only by design.
- **Resources:** Whisper `tiny` + CUDA on a 4 GB Orin Nano is workable but tight; 8 GB preferred. Voice models and CUDA
  context live in the container image, built on TX2 per §6 and distributed as tarballs.
- **Watch:** GPU memory is shared with system RAM on Orin Nano — do not co-locate other GPU workloads without re-checking RAM.

### orin2 — general ingestion worker (untainted)

- **Does:** the node the **floating market-data CronJobs actually run on** since 2026-11-13. Live 2026-11-15: ~49 pods
  scheduled here — every floating CronJob (`fx`, `yields`, `rates`, `stock-prices`, `news`, `ta-compute`,
  `pipeline-health`, `stock-metrics`, `stock-screener`, `stock-report`, `stock-valuation`, `prediction-tracker`,
  `ml1-anomalies`, `ml3-volatility`, `ml7b-per-symbol`, `llm-narrative`, `news-embed`, `nse-fetcher`,
  `stock-fundamentals`, `rbi-fetcher`, `fiidii-fetcher`, `intraday-fetcher`, `universe-refresh`, …).
  **One job currently Running:** `nse-fundamentals-29912760-5974f` (~3 h, weekly NSE fundamentals fetcher); all other
  market-data pods are `Completed`. **Orin2 holds NO market data** — no PVC or hostPath mount on either Orin node;
  jobs write only to TimescaleDB on tx2. **Not a single point of failure:** every floating job's image
  (`marketdata-data:v8`, `marketdata-light:v1`) is imported on pi1 and pi3 as well as orin2, so if orin2 dies the
  scheduler reschedules onto the Pis — no data lost, no availability gap (the Orins ran nothing before 2026-11-13).
  The gap is *observability*: orin2 isn't pinged by nodewatch and has no node-stats logger host cron.
- **Labels/taints (live):** `hardware=orin-cpu`, `tier=saas`, 1× GPU allocatable, **no taint** — which is exactly why
  it takes floating work (the `gpu=orin` taint on orin1 keeps voice pods off it and vice-versa).
- **Pinned:** none. Every pinned workload (tx2 GPU jobs, pi1 Telegram, pi3 Grafana/backup) stays where it is.
- **Image obligation (2026-11-13):** orin2 needs `marketdata-data:v8` + `marketdata-light:v1` imported. **Every future
  data/light image cycle must import on orin2 too** — pi1 and pi3 already hold both images, so a cycle that misses
  orin2 leaves floating jobs able to fall back to the Pis but orin2 itself would start failing with `ErrImageNeverPull`
  (`imagePullPolicy: Never`, no registry — AGENTS.md rule 6).
- **Capacity:** 6 CPU / 7.6 GB RAM / 476 GB SSD — the most headroom of any worker, which is why it wins floating jobs.
- **Live 2026-11-15:** k3s-agent active, Ready, v1.37.2+k3s1, up 10 d 3 h, load 0.06, 3.3 GiB peak (k3s agent),
  GPU present but idle — no CUDA processes, no GPU memory in use. **Passwordless SSH now works from the admin
  workstation via `ssh orin2`.** Joined the tailnet 2026-11-05; Tailscale IP reachable.
- **Not yet covered by ops tooling:** `ORIN1_IP`/`ORIN2_IP` aliases now exist in `.hosts.env` (added 2026-11-15),
  but no `_creds.py` host password slot yet, so `ops-tools/runcmd.py` still cannot reach orin1/orin2 — no
  node-stats logger host cron, no `vcgencmd`/throttle check, no image-import helper via that tool.
  Direct `ssh orin1` / `ssh orin2` works passwordless from the admin workstation. Tracker §1.

## 3. Every scheduled job, by node

Two kinds of scheduled work exist: **Kubernetes CronJobs** (36, all in namespace `market-data`) and **host-level cron**
(on the machines themselves, outside k3s). *Verified live 2026-11-15.*

**Schedule timezone — read this before changing a cron expression.** All schedules except two are **node-local**, i.e. they
are evaluated in the **control plane's timezone** (`pi2` = `America/Los_Angeles`). The observed offset on 2026-11-15 was
**UTC−8 (PST)** — `0 23 * * *` fired at 07:00 UTC, `15 0 * * *` at 08:15 UTC. Because the controller follows
`America/Los_Angeles`, the **UTC instant of every node-local job shifts by one hour across the DST boundary**
(UTC−7 in summer, UTC−8 in winter) — a job scheduled "at 23:00 local" fires at 06:00 UTC in summer and 07:00 UTC in
winter. **Never assume a node-local cron expression is UTC.** The only two jobs with an explicit `timeZone` are
`telegram-digest` and `llm-narrative` (`Asia/Kolkata`, ADR 0009) — those two are DST-free and hour-stable.
`status.lastScheduleTime` is always reported in **UTC**.

**All 36 CronJobs are unsuspended** (`spec.suspend: false` on every one, verified 2026-11-15) — including
`nse-fundamentals`, whose "suspended until the first backfill completes" note in the ops manual §3 is **stale**.

### 3.1 Kubernetes CronJobs — which node runs each

A CronJob is *pinned* when its manifest has a `nodeSelector`; otherwise it floats and the scheduler picks a worker that
has the image. **5 pinned + 31 floating.** Images are node-local (`imagePullPolicy: Never`, no registry — AGENTS.md
rule 6), so a floating job only works on a node that has imported its image.

| Node | CronJobs it runs | How |
|---|---|---|
| **tx2** | `sentiment-scorer` `15 0 * * *`, `headline-ner` `45 0 * * *` | pinned (`hardware: gpu` + toleration) |
| **pi3** | `pg-backup` `30 1 * * *` | pinned (`kubernetes.io/hostname: pi3`; dumps live on its SSD) |
| **pi1** | `telegram-digest` `0 8 * * *` (IST), `telegram-alerts` `45 2 * * *` | pinned (`pi1`) |
| **orin2** | the 31 floating jobs below | untainted worker holding the newest `local/*` images — since 2026-11-13 the scheduler sends floating work here |
| **pi1 or pi3** | fallback for any floating job | valid landing zones (image must be imported) |
| **orin1** | none | tainted `gpu=orin` — reserved for the voice layer, which is not deployed |
| **pi2** | none | cordoned control plane |
| **pi0** | none | not a k3s node |

Pinned jobs, last scheduled (UTC, 2026-11-15): sentiment-scorer 08:15 · headline-ner 08:45 · pg-backup 09:30
(`BACKUP-OK`, dump `marketdata-2026-11-15.dump` 539 MiB) · telegram-digest 02:30 · telegram-alerts 10:45.

**Floating jobs (31).** "Seen on" = the node of the retained pods. Every floating job's pods are on **orin2** as of
2026-11-15. Before 2026-11-13 they mostly landed on pi1/pi3; the orin2 import (data v7 + light v1) moved the bulk
there, and orin2's 6 CPU / 7.6 GB headroom now wins the scheduler's choice. (`telegram-digest` is listed for
completeness — it is the one pinned job in this table.)

| CronJob | Schedule (node-local) | Last scheduled (UTC, 2026-11-15) | Seen on |
|---|---|---|---|
| yfinance-fetcher | `30 22 * * *` | 11-15 06:30 | orin2 |
| fx-fetcher | `0 23 * * *` | 11-15 07:00 | orin2 |
| yields-fetcher | `15 23 * * *` | 11-15 07:15 | orin2 |
| rates-fetcher | `20 23 * * *` | 11-15 07:20 | orin2 |
| stock-prices | `30 23 * * *` | 11-15 07:30 | orin2 |
| news-fetcher | `45 23 * * *` | 11-15 07:45 | orin2 |
| rbi-fetcher | `0 7 * * *` | 11-15 15:00 | orin2 |
| nse-fetcher | `15 11 * * *` | 11-15 19:15 | orin2 |
| nse-fundamentals | `0 10 * * 0` | 11-15 18:00 | orin2 |
| fiidii-fetcher | `0 14 * * *` | 11-14 22:00 | orin2 |
| ta-compute | `30 0 * * *` | 11-15 08:30 | orin2 |
| news-embed | `0 1 * * *` | 11-15 09:00 | orin2 |
| pipeline-health | `45 1 * * *` | 11-15 09:45 | orin2 |
| ml1-anomalies | `0 2 * * *` | 11-15 10:00 | orin2 |
| stock-metrics | `0 2 * * *` | 11-15 10:00 | orin2 |
| stock-valuation | `10 2 * * *` | 11-15 10:10 | orin2 |
| stock-screener | `15 2 * * *` | 11-15 10:15 | orin2 |
| stock-report | `30 2 * * *` | 11-15 10:30 | orin2 |
| ml7b-per-symbol | `45 2 * * 6` | 11-14 10:45 | orin2 |
| prediction-tracker | `40 2 * * *` | 11-15 10:40 | orin2 |
| ml3-volatility | `0 3 * * *` | 11-15 11:00 | orin2 |
| llm-narrative | `40 7 * * *` (Asia/Kolkata) | 11-15 02:10 | orin2 |
| telegram-digest | `0 8 * * *` (Asia/Kolkata) | 11-15 02:30 | **pi1** (pinned) |
| accuracy-check | `30 12 * * 0` | 11-08 20:30 | pi3 (last run; next due Sun) |
| earnings-calendar | `0 12 * * 0` | 11-08 20:00 | pi3 (last run; next due Sun) |
| research-watcher | `0 13 * * 1` | 11-09 21:00 | pi3 (last run; next due Mon) |
| universe-refresh | `0 9 * * 0` | 11-15 17:00 | orin2 |
| intraday-fetcher | `10 * * * *` (hourly) | 11-15 19:10 | orin2 |
| factors-fetcher | `0 11 2 * *` | 11-02 19:00 | orin2 |
| membership-history | `30 11 2 * *` | 11-02 19:30 | orin2 |
| fdi-watch | `0 9 1 * *` (monthly) | 11-01 17:00 | orin2 |
| stock-fundamentals | `0 6 * * 6` | 11-14 14:00 | orin2 |

The weekly/monthly jobs (`accuracy-check`, `earnings-calendar`, `research-watcher`, `universe-refresh`, `factors-fetcher`,
`membership-history`, `fdi-watch`, `ml7b-per-symbol`, `stock-fundamentals`) only run on their weekday — a stale
`lastScheduleTime` on those is expected, not a missed run. Use `startingDeadlineSeconds` (already set on every job)
rather than the schedule age to spot a genuinely missed run.

Non-CronJob workloads: `timescaledb` StatefulSet (tx2), `llm-gateway` Deployment (tx2, namespace `llm`),
`grafana` Deployment (pi3), `telegram-bot` Deployment (pi1), `marketview` Deployment (pi1).
One-off Jobs (manual): `restore-drill` (pi3), `timescaledb-schema` (floating), backfills (floating), GPU smoke test (tx2).

### 3.2 Host-level cron and timers — outside k3s (root crontab unless noted)

| Node | Scheduled work |
|---|---|
| **pi0** | root cron `*/5`: `node-stats-log.sh` (stats CSV) and `nodewatch.sh` (liveness watcher → Telegram). `/etc/cron.d/pihole`: weekly `pihole updateGravity` (Sun, early morning) + daily log flush at 00:00. `/etc/cron.d/e2scrub_all`. |
| **pi1** | root cron `*/5`: `node-stats-log.sh`. `/etc/cron.d/e2scrub_all`. |
| **pi2** | root cron `*/5`: `node-stats-log.sh`. `/etc/cron.d/e2scrub_all`. |
| **pi3** | root cron `*/5`: `node-stats-log.sh`. `/etc/cron.d/e2scrub_all`. |
| **tx2** | root cron `*/5`: `node-stats-log.sh`. `/etc/cron.d/anacron` (+ Ubuntu daily/weekly: apport, update-notifier, etc.). |
| **orin1 / orin2** | **none yet** — no node-stats logger, no host cron. Joined 2026-11-05; the AR-12 pattern was never applied to them. Tracker §1. |

All five Pis/TX2 also run the distro's daily jobs (logrotate — which rotates `stats.csv` weekly — man-db, dpkg) and systemd
timers (apt-daily, fstrim, e2scrub, tmpfiles). **Unattended upgrades are not installed** (checked on tx2 and pi2): the
apt timers only refresh package lists, so nothing upgrades itself — consistent with AGENTS.md rule 3.
**Root-crontab history:** until 2026-10-11 every host's root crontab was empty (the stats logger had never been
scheduled); fixed that day on all five (ADR 0011 amendment).

## 4. Dependencies and blast radius

- **DB (tx2) down** → every fetcher, the screener chain, Grafana panels, the bot's data commands all fail; data is safe.
- **Control plane (pi2) down** → no API, no scheduling, CoreDNS gone; running pods keep running but nothing recovers.
- **pi3 down** → no dashboards, no nightly dump, no restore drill.
- **pi0 down** → LAN DNS gone *and* the out-of-cluster alert path is silent (this is why a second watcher host is an open question — AR-12).
- **orin2 down** → the 31 floating CronJobs reschedule to pi1/pi3 — **only if those nodes hold the image**; otherwise
  they fail with `ErrImageNeverPull` until the tarball is imported. No pinned workload is lost.
- **orin1 down** → no impact today — the voice layer is not deployed (it only holds kube-system pods).
- **Shared charger:** pi1/pi2/pi3 dropped together on 2026-10-10 while pi0 stayed up — the power supply is a single
  point of failure across all of them (tracker §1).
- **No registry:** an image missing on a node gives `ErrImageNeverPull`; keep the "which node has which image" list true.
- **Single image source of truth is now three nodes** (pi1, pi3, orin2). Every data/light image cycle must be imported on
  **all three**, or floating jobs start failing with `ErrImageNeverPull` on whichever node the scheduler picks.

## 5. Current state (live check 2026-11-15)

- **Nodes (2026-11-15):** 6 k3s nodes **Ready** — pi1, pi2 (cordoned), pi3, tx2, orin1, orin2; pi0 up but not a k3s node.
  Versions: server pi2 v1.36.4+k3s1; agents pi1/pi3 v1.36.4, orin1/orin2 v1.37.2, tx2 pinned v1.35.3+k3s1 (cgroup v1).
- **CronJobs: all 36 active, none suspended**, no failed or pending pods. **30 of the 31 jobs started in the last 26 h
  completed `Succeeded` with 0 failures; `nse-fundamentals` (started 18:00 UTC) was still `Running` at the check** — it
  is a long weekly filings job, not a stall.
- **Data freshness:** `pipeline_heartbeat` — **16/16 feeds `fresh`** (fx, yields, fred_rates, fiidii_flows, intraday, rbi,
  news_staging, sentiment, nse_indices, stock_prices, stock_scores, stock_fundamentals, ta_indicators, yfinance_batch,
  llm_narrative, llm_gateway). `pipeline-health` last ran 2026-11-15 09:45 UTC.
- **Running always-on pods:** `timescaledb-0` (tx2), `grafana` (pi3), `telegram-bot` (pi1), `marketview` (pi1),
  `llm-gateway` (tx2, ns `llm`).
- **Backups:** `marketdata-2026-11-15.dump` (539 MiB) + `.sha256` sidecar on pi3's SSD; 28 dumps retained. The
  workstation copy (`ops-tools/pull-backups.py`) and an off-site copy are still open (tracker §1, AR-11).
- **Host-level:** pi0 load 1.57 (Pi-hole serving), pi1 `throttled=0x0` load 0.73, pi2 `throttled=0x80000` (soft-temp flag
  only) load 0.50, pi3 `throttled=0x0` load 0.37. pi1/pi3 disk at 8 % / 9 %.
- **Telegram bot** on `telegram-digest:v42` (ADR 0012); `marketview` on `marketview:v14`.
- **Not re-verified 2026-11-15:** TX2 fixed config, TX2 eMMC/disk %, TX2 load — **SSH auth from this workstation
  failed** (`runcmd.py tx2` → "Authentication failed"). The TX2 workloads themselves are healthy (`timescaledb-0` Running,
  `llm-gateway` Running, `sentiment-scorer`/`headline-ner` succeeded) — this is a credential gap, not an outage. Tracker §1.
- **Not re-verified:** per-Pi thermal detail, pi1 24 h power acceptance, orin1/orin2 host state (no SSH alias).
- **Prior event (context, 2026-10-18/20):** site-wide mains power loss 2026-10-18 ~05:55–06:20 UTC (all five machines
  rebooted); pi2 rebooted again 2026-10-20 ~01:56 UTC, cause unknown. Both recovered on their own.

## 6. Keeping this file true

Update when: a pin changes (manifest `nodeSelector`), a node is cordoned/un-cordoned, a host job is added to a Pi, an image
is imported on a new node, or hardware changes. `python3 ops-tools/workloads.py --check` covers CronJob/doc agreement but
not placement, so placement edits are manual.

See also: ops manual §2 (inventory + TX2 fixed config), §3 (workloads), §8 (failure runbook); ADR 0011; skill `node-power-health`.
