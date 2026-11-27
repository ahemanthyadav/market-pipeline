# Pi cluster + Jetson TX2 market data pipeline — implementation plan

> **Updated 2026-09-30** to match the cluster as actually built. Sources of truth:
> `piCluster/homelab setup/SETUP.md` (Pis + k3s), `homelab setup/TX2_AI_Edge_Lab_Setup_and_Test_Plan.md`
> (TX2, most recent), `homelab setup/TAILSCALE-SETUP.md` and
> `homelab setup/REMOTE-ACCESS-DECISION-LOG.md` (remote access).
> Pipeline live end-to-end 2026-09-30 (Phases 0–8 implemented, gaps noted per phase).
> Remaining: pi0 tailnet, Grafana alert rules (heartbeat table now exists — unblocked), RBI rates feed.

## Current state (as of 2026-09-30)

| Node | Hardware | IP | Role | Status |
|---|---|---|---|---|
| pi0 | Pi 1 B+, 512 MB | <pi0-ip> | Pi-hole v6.4.3 + unbound (DNS) | ✅ Running |
| pi1 | Pi 3 B, 1 GB, 233 GB SSD | <pi1-ip> | k3s worker (agent: --disable-apiserver-lb) | ✅ Ready (restored 2026-09-29) |
| pi2 | Pi 3 B+, 1 GB | <pi2-ip> | k3s worker (ex-control-plane, moved to minipc/N100 on 2026-11-16), agent v1.37.2+k3s1 | ✅ Ready |
| pi3 | Pi 3 B, 1 GB | <pi3-ip> | k3s worker (agent: --disable-apiserver-lb) | ✅ Ready |
| ahytx2-desktop | Jetson TX2, 7.7 GB RAM, 28 GB eMMC | <tx2-ip> (eth1, DHCP — router-reserved) | k3s worker (GPU), agent pinned v1.35.3+k3s1 | ✅ Ready |

- 4th Pi 3B + 870 EVO SSD: still unbuilt — now optional (see Phase 0).
- Remote access: **Tailscale** is live (WireGuard was abandoned and fully removed — ISP uses CGNAT).
  Tailnet: Mac `<admin-tailnet-ip>`, pi2 `<pi2-tailnet-ip>`, pi3 `<pi3-tailnet-ip>`,
  pi1 `<pi1-tailnet-ip>` (back online 2026-09-29), TX2 `<tx2-tailnet-ip>`, phone `<phone-tailnet-ip>`.
  pi0 is **not** on the tailnet yet.
- Managed from the Mac via `export KUBECONFIG=~/.kube/pi-cluster.yaml` (server `<pi2-ip>`).

### Operational constraints (learned during TX2 bring-up — respect these in every phase)

1. **TX2 k3s agent is version-pinned at v1.35.3+k3s1**, running with
   `--kubelet-arg=fail-cgroupv1=false --disable-apiserver-lb`. The Tegra 4.9 kernel is
   cgroup v1 only; v1.36+ kubelets remove the cgroup v1 escape hatch. Consequence: don't
   upgrade the TX2 agent, and control-plane upgrades cap out around v1.38 (kubelet skew n-3). The agent unit additionally
   carries `--kubelet-arg=cgroup-driver=cgroupfs` with containerd `SystemdCgroup=false`
   (systemd-237 lacks the AttachProcessesToUnit API — drivers must match or `exec` breaks).
2. **The TX2's iptables-restore binaries are custom wrappers** (they strip `-m nfacct`, which the
   old kernel falsely probes for) backed by `/sbin/xtables-multi.orig`. If an `apt upgrade`
   reinstalls iptables, kube-proxy loses service VIPs → ClusterIP Services (incl. the DB) stop
   working. Re-verify the wrappers after any TX2 package maintenance.
3. **pi2 swap-thrashes under load** (heavy API bursts push load to 9–12 on 1 GB RAM). Do big
   applies/rollouts when the cluster is calm, stagger job schedules, and keep pi2 a light
   ingestion worker — not a primary one. Agent (re)starts: one at a time on load <2, verifying
   each before the next (proven 2026-09-29: retries against a hot server never break through).
4. **pi1 restored 2026-09-29** (was down 13+ days) — full worker capacity is back, no scheduling restrictions.
5. **CGNAT confirmed** (router WAN `<wan-ip>` ≠ externally seen `<public-ip>`): no inbound
   ports, ever. Tailscale is the access path; the leftover router UDP 51820 forward from the
   WireGuard era is unused.

---

## Tech stack

| Layer | Tool | Runs on |
|---|---|---|
| Orchestration | k3s (server v1.37.2+k3s1 on minipc/N100; TX2 agent pinned v1.35.3+k3s1) | Whole cluster |
| DNS | Pi-hole v6.4.3 + unbound | pi0 |
| Scheduling | k3s `CronJob`s (Argo/Tekton optional — but see the pi2 RAM caution in Phase 6) | Pi workers / TX2 |
| Ingestion scripts | Python: `yfinance`, `jugaad-data`, `fredapi`, `requests` (NSDL/NSE/RBI/exchangerate.host/goldapi/metals-api) | Pi workers |
| Technical analysis | Python: `pandas-ta` | Pi workers |
| Sentiment/NLP | Python: PyTorch (Jetson CUDA 10.2 / cp36 build) + FinBERT (or distilled equivalent) | Jetson TX2 (GPU) |
| GPU runtime | NVIDIA container runtime + JetPack 4.6.1 / L4T R32.7.5 (CUDA 10.2, cuDNN 8.2, TensorRT 8.2) | TX2 |
| Storage | TimescaleDB (Postgres + time-series extension) | TX2 (USB SSD sda4 → `/mnt/tx2-ssd/pgdata`; mSATA slot still free) |
| Distributed storage (optional) | Longhorn / OpenEBS | Pi workers |
| Dashboard | Grafana (Postgres datasource) | Pi worker (pi3 today) |
| Logging (optional) | Loki | Pi worker (not pi2; 4th Pi if it's ever built) |
| Remote access | Tailscale (mesh VPN; WireGuard removed) | Cluster-wide + Mac/phone |
| Secrets | k3s `Secret`s (API keys) | Cluster-wide |
| Container images | Docker, ARM64 builds | Pi workers; TX2 needs an L4T r32.7 (Ubuntu 18.04) base image |

## Data sources

| Category | Source | Access method | Cadence |
|---|---|---|---|
| US/global indices (S&P 500, Nasdaq, Dow, Russell 2000, VIX, FTSE 100, DAX, CAC 40, Euro Stoxx 50, IBEX 35, FTSE MIB, SMI, AEX, Nikkei 225, Hang Seng, Shanghai/Shenzhen, KOSPI, TAIEX, ASX 200, STI, TSX, Bovespa, IPC, DXY) | Yahoo Finance | `yfinance` batch download | Daily |
| Indian indices (Nifty 50/Bank/IT/Auto/Pharma/FMCG/Midcap 150/Smallcap 250/500, Sensex, India VIX) | NSE / BSE | `jugaad-data` / `nsepy` | Daily |
| Gold, silver | goldapi.io / metals-api.com (or `yfinance` futures tickers) | REST API | Daily |
| USD and other FX pairs | exchangerate.host | REST API (free, no key) | Daily |
| US Treasury yields, Fed funds rate | FRED | `fredapi` | Daily |
| India G-Secs, RBI repo rate | RBI public data | Scrape / published CSV | Daily |
| Market sentiment (news) | Reuters, Moneycontrol, Economic Times, etc. | RSS feeds → FinBERT scoring on TX2 | Daily |
| FII/DII daily flows | NSE / NSDL | Scrape / published daily reports | Daily |
| FDI inflows/outflows | RBI / DPIIT | Monthly published data/CSV | Monthly |
| US foreign capital flows (mirror stat) | US Treasury TIC data | Monthly published data | Monthly |

---

## GPU node hardware (as built — running)

- **Module:** NVIDIA Jetson TX2, P3310, 8GB (7.7 GB usable), 256 CUDA cores (Pascal)
- **Carrier:** Connect Tech ASG001 Astro + XBG201/XBG206 breakout — assembly and power proven
  (Dell ~19.5 V brick on the carrier terminal block, 9–36 V input range)
- **OS/stack (measured, not assumed):** Ubuntu 18.04.6, L4T R32.7.5 (JetPack 4.6.1),
  CUDA 10.2.300, cuDNN 8.2.1.32, TensorRT 8.2.1.9, OpenCV 4.1.1 (C++) / 3.2.0 (Python),
  Docker installed. Flashed with the Connect Tech BSP `CTI-L4T-TX2-32.7.5-V001` —
  the `CTI-L4T-V110` factory sticker was stale; no reflash was needed.
- **Kernel:** 4.9.337-tegra — cgroup v1 only, no nfacct match (hence constraints #1 and #2)
- **Network:** eth1 = Intel 82574L GbE (carrier PHY) is primary at <tx2-ip>
  (**DHCP — needs pinning**, see Phase 0). The module's own NIC and wlan0 (no antennas) are unused.
- **Storage:** 28 GB eMMC only. **No mSATA SSD is installed** — the mSATA + mini PCIe slots
  are still free (see Phase 2).

---

## Phase 0 — Inventory & prep — ✅ mostly complete (TX2 bring-up, 2026-09-26)

Done: carrier assembly, power-polarity check, first boot, L4T/JetPack verification
(R32.7.5 + CTI BSP, so stock-NVIDIA reflash never needed), Tailscale on the TX2, k3s join
(Phase 1), DB-host decision (TX2 — reasoning unchanged).

Still open:

1. ✅ **pi1 restored 2026-09-29.** Power-cycled after 13+ days unreachable; USB-SSD boot
   healthy, agent rejoined cleanly (with `--disable-apiserver-lb`, see Phase 1 incident note).
   Full worker capacity back.
2. **4th k3s worker — downgraded to optional.** With the TX2 joined, daily short-lived CronJobs
   fit comfortably on pi3 (+ pi2 lightly). Build the 4th Pi only if/when you want Longhorn
   redundancy or a second Grafana replica on independent hardware.
3. **✅ Pin the TX2's IP — DONE 2026-10-03** via router DHCP reservation (user-confirmed);
   host stays a DHCP client by design, lease pinned to <tx2-ip> so it can't drift out
   from under the agent's `--node-ip <tx2-ip>`.
4. **DB disk — ✅ resolved 2026-09-29 with the attached USB SSD; mSATA now truly optional.**
   The 224G USB SSD's `sda4` (158G ext3, shared with existing NAS data) is fixed-mounted at
   `/mnt/tx2-ssd` via fstab (by UUID, `nofail`; backup at `/etc/fstab.bak-20260929`), with
   `/mnt/tx2-ssd/pgdata` reserved for Postgres. The DB never needs to live on eMMC. The mSATA
   slot is still free if a second disk is ever wanted.
5. **DB host decision: TX2, unchanged.** 7.7 GB shared RAM, a native SATA upgrade path, and an
   idle GPU outside the sentiment window still beat putting Postgres on a 1 GB Pi.

---

## Phase 1 — Join the TX2 to k3s — ✅ complete (2026-09-29)

Done: agent installed, node **Ready**, running with the pinned version and flags from
constraint #1 (`v1.35.3+k3s1`, `--disable-apiserver-lb`, `--kubelet-arg=fail-cgroupv1=false`,
`--node-ip <tx2-ip>`). Registration succeeded against a calm pi2 (registration needs
~6 sequential API calls inside a ~25 s client timeout — it fails while pi2 is thrashing).

Remaining:

1. ✅ **Label + taint — DONE 2026-09-29** (`hardware=gpu`, `nvidia.com/gpu.present=true`,
   `gpu=true:NoSchedule` on `ahytx2-desktop`, verified via API before the pi2 incident below).
   Note: k3s's svclb (ServiceLB) pods won't tolerate the taint — harmless here since the
   pipeline uses ClusterIP Services only.
2. 🔶 **NVIDIA container runtime — installed, wiring pending verification.**
   `nvidia-container-runtime` 3.7.0 (+ toolkit/libs) installed from the Jetson r32.7 repo
   (5 new packages, nothing upgraded — iptables wrappers confirmed intact afterwards);
   `nvidia-container-cli info` detects the Tegra X2 (arch 6.2, CUDA 10.2). containerd drop-in
   fragment placed at
   `/var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.d/nvidia.toml` (runc v2 +
   `BinaryName=/usr/bin/nvidia-container-runtime`, `SystemdCgroup=true`); after the agent
   restart, containerd picked it up (`crictl info` shows the `nvidia` runtime; k3s log:
   "Found nvidia container runtime"). **RuntimeClass + smoke test — DONE 2026-09-29:**
   `manifests/gpu-runtimeclass.yaml` (`handler: nvidia`) applied; `manifests/gpu-smoke-test.yaml`
   pod Completed on `ahytx2-desktop`, asserting `/dev/nvhost-gpu` + `tegra/libcuda.so.1.1` +
   the `libcuda.so` symlink — full GPU passthrough works end-to-end via
   `runtimeClassName: nvidia` + taint toleration (no device-plugin needed).
3. **Smoke-test GPU passthrough — DONE 2026-09-29** (see #2). Memory cgroup verified present
   on the tegra kernel, so Phase 2 resource limits are enforceable. Key finding for Phase 4:
   the L4T runtime injects **driver + devices only** (libcuda, `/dev/nvhost-*`, firmware —
   confirmed present in-container); cuDNN/cuBLAS/CUDA toolkit must come from the **container
   image** (`l4t-cuda`/`l4t-ml` style base, per the plan), and `NVIDIA_VISIBLE_DEVICES` is an
   *input* (set it explicitly in pod specs — the runtime does not set it).
   Monitor with `tegrastats` (no nvidia-smi on Jetson).

> ⚠️ **2026-09-29 incident — pi2 control-plane wedged.** After the TX2 agent restart (needed to
> pick up the containerd fragment), the re-registration burst tipped pi2 over: ping/TCP alive,
> but SSH and `:6443` never complete handshakes (TLS timeouts); `readyz` had shown etcd
> failures. TX2 and pi3 agents were **stopped** to quiet API retry load, per the TX2 doc's own
> procedure — the cluster did not recover on its own. **Recovery needs a physical power-cycle
> of pi2** (same as pi1 once needed). After it returns: `systemctl start k3s-agent` on pi3 +
> TX2, verify all nodes Ready, then continue with the RuntimeClass + smoke test. Lesson
> reinforced: treat every agent restart / big apply as a potential pi2-tipover event — one
> mutation at a time, then confirm pi2 is still responsive.
> **Resolution (same night):** pi2 power-cycled; all agents rejoined **one at a time on a
> calm server** (pi3 → pi1 → TX2, each verified before the next — retries against a hot
> server never break through, they only sustain the storm). pi1/pi3 agents now also run with
> `--disable-apiserver-lb` (direct to pi2:6443, no local-LB hop — same as the TX2; patched
> into their systemd units). Full cluster Ready again, smoke test passed.

---

## Phase 2 — Storage layer — ✅ complete (2026-09-29)

1. Deploy **TimescaleDB** (Postgres + time-series extension) as a StatefulSet **on the TX2**,
   pinned with `nodeSelector: kubernetes.io/hostname: ahytx2-desktop` **plus a toleration for
   the `gpu=true:NoSchedule` taint** (Postgres isn't a GPU workload, but it must be allowed
   onto the tainted node). Storage is a **hostPath PV at `/mnt/tx2-ssd/pgdata`** (USB SSD
   `sda4`, fixed-mounted — see Phase 0 #4; 148G free). The official TimescaleDB image
   starts as root and chowns `PGDATA` itself, so the root-owned dir is fine.
   At a few thousand rows/day this is decades of headroom. (Manifests staged in
   `manifests/timescaledb/`: namespace, Secret, PV, StatefulSet (image pinned
   `timescale/timescaledb:2.30.1-pg16`, arm64), headless Service, schema ConfigMap + Job —
   ready to `kubectl apply`.)
   **APPLIED 2026-09-29:** namespace/Secret/PV/StatefulSet/Service live; pod `timescaledb-0`
   1/1 Running on TX2, PVC bound to `timescaledb-tx2-ssd`, PGDATA verified on sda4
   (in-pod `df` shows `/dev/sda4`).
2. Set explicit requests/limits as planned (~2 GB Postgres, ~4 GB sentiment window) — ✅ live and
   enforceable (memory cgroup present; pod healthy within limits across agent restarts).
   Ops note: `kubectl exec` was broken on the TX2 (systemd-237 predates the
   AttachProcessesToUnit D-Bus API that containerd needs with SystemdCgroup=true) — fixed by
   aligning both sides to cgroupfs (containerd fragment `SystemdCgroup=false` + agent flag
   `--kubelet-arg=cgroup-driver=cgroupfs`). Exec verified working post-fix. Bootstrap the DB
   while pi2 is calm (constraint #3).
3. Create the schema — one hypertable per data category keeps queries fast (unchanged):
   - `index_prices` (ticker, timestamp, open, high, low, close, volume)
   - `fx_rates` (pair, timestamp, rate)
   - `commodity_prices` (commodity, timestamp, price)
   - `bond_yields` (instrument, timestamp, yield)
   - `interest_rates` (rate_name, timestamp, value)
   - `news_sentiment` (headline, source, timestamp, sentiment_score, ticker_relevance)
   - `technical_indicators` (ticker, timestamp, indicator_name, value)
   - `capital_flows` (flow_type: FDI/FII/DII, direction: inflow/outflow, timestamp, amount, segment)
4. Expose it as a k3s `Service` (ClusterIP) so other pods/jobs can reach it by DNS name (e.g. `timescaledb.default.svc.cluster.local`) (unchanged).
5. Backups: daily `pg_dump` to **pi3's SSD or the Mac over Tailscale** (the 4th Pi doesn’t exist; pi1 is back with its 233 GB SSD available). Same accepted tradeoff as
   before: a TX2 outage takes down GPU inference *and* the database together.
6. Longhorn/OpenEBS: still an optional resilience upgrade. Weigh their constant API/watch load
   against pi2's 1 GB RAM (constraint #3) — at this data volume, plain daily `pg_dump` may be
   all you ever need.

> ✅ **Phase 2 verified 2026-09-29:** schema Job `SCHEMA-OK` (8 hypertables + `raw_headlines`);
> write-test Job `WRITE-OK` (INSERT → SELECT → DELETE round-trip, 0 rows left behind).
> Exec wart found + fixed: `kubectl exec` failed on TX2 pods (containerd `SystemdCgroup=true`
> needs the `AttachProcessesToUnit` D-Bus API, absent from the TX2's systemd-237) — fixed via
> the cgroupfs pair (containerd fragment `SystemdCgroup=false` for runc+nvidia **and**
> kubelet `--cgroup-driver=cgroupfs`; drivers must match). Exec verified working afterwards.
> Note: fixing the unit mid-incident once produced a dangling ExecStart continuation line
> (single-line unit + inserted line without trailing `\`) — caught via `systemctl show` and
> repaired before restart; always re-check parsed `ExecStart` after patching units.
> Also observed: `k3s-server` on pi2 died once (exit 1, no OOM trace, no logged fatal) under
> peak storm load and self-restarted via `Restart=always` — another reason to keep pi2 calm
> during mutations.

---

## Phase 3 — Data ingestion jobs — ✅ all jobs live 2026-09-30 (2 feed gaps noted)

Unchanged approach: each source is a small Python script in its own ARM64 Docker image, deployed
as a k3s `CronJob`. Don't pin these to specific nodes — let the scheduler balance them across
the healthy workers. **Pool is whole again (pi1/pi2/pi3 all Ready as of 2026-09-29)**; keep pi2
light by policy (constraint #3). The TX2 stays excluded via its taint; the 4th Pi is no longer
assumed — add it back if built.

Image pattern for every job: built natively on the TX2's docker, distributed to
pi1/pi2/pi3 via `k3s ctr -n k8s.io images import`, pods use `imagePullPolicy: Never`
(no registry deployed). Rebuild procedure lives in each Dockerfile header; yfinance
dep versions locked in `manifests/ingestion/yfinance/requirements.lock`.

1. ✅ **Indices batch fetcher — LIVE 2026-09-29** (`manifests/ingestion/yfinance/`: script +
   Dockerfile + CronJob `yfinance-fetcher`, schedule `30 22 * * *` UTC). Validated end-to-end:
   28/28 tickers → rows in `index_prices` (idempotent DELETE+INSERT per ticker, so re-runs and
   late corrections overwrite cleanly). Image `local/yfinance-fetcher:v3` (adds FETCH_PERIOD/BACKFILL; daily path unchanged) built natively on the
   TX2, distributed via `ctr images import` to pi1/pi2/pi3, `imagePullPolicy: Never` (no registry
   deployed — see Dockerfile header for the rebuild/redistribute procedure; versions locked in
   `requirements.lock`: yfinance 1.7.0, pandas 3.0.6, psycopg 3.3.6). Finding: Yahoo renamed the
   FTSE MIB ticker `FTSE.MI` → `FTSEMIB.MI` (old symbol 404s); batch now uses the new symbol.
   One-shot 1y backfill applied (7016 rows, 28 tickers) — dashboards/TA have history from day one.
2. ✅ **NSE/India fetcher — LIVE 2026-09-30** (`manifests/ingestion/nse/`, CronJob `nse-fetcher`, `15 11 * * *` UTC). 8/8 validated tickers → `index_prices` (image `local/nse-fetcher:v2`, backfill-capable; 1y backfill ~1973 rows). Yahoo findings: `^NSEI ^NSEBANK ^CNXIT ^CNXPHARMA ^BSESN ^INDIAVIX NIFTYMIDCAP150.NS NIFTYSMLCAP250.NS` live; `^CNXAUTO`/`^CNXFMCG` stale-dropped (series frozen); **Nifty 500 has no Yahoo symbol** (gap — needs jugaad-data/NSE-direct later).
3. ✅ **FX fetcher — LIVE 2026-09-30** (`manifests/ingestion/fx/`, CronJob `fx-fetcher`, `0 23 * * *` UTC). 10/10 pairs → `fx_rates` via yfinance `=X` series (no exchangerate.host key needed): USDINR/EURINR/GBPINR/JPYINR/EURUSD/GBPUSD/USDJPY/USDCNY/USDCHF/AUDUSD. Image `local/fx-fetcher:v1`.
4. ✅ **Commodities — DONE via the yfinance batch** (decision: no separate job, no metals API keys). GC=F/SI=F/CL=F ride along in job #1 → `index_prices`. The `commodity_prices` hypertable stays empty by design (split out only if a dedicated metals feed is ever wanted).
5. ✅ **Bonds & rates — COMPLETE 2026-10-03** (`manifests/ingestion/rates/`, CronJob `rates-fetcher`, `20 23 * * *` UTC). FRED key provided → DFF (Fed funds) into `interest_rates`, DGS2/DGS10/DGS30 into `bond_yields` for official depth alongside the yfinance yield series. 4/4 validated, image `local/rates-fetcher:v1`, key in `fred-api` Secret (never in images). Remaining gap: India G-Secs/RBI repo (no stable machine feed).
6. ✅ **News fetcher — LIVE 2026-09-30** (`manifests/ingestion/news/`, CronJob `news-fetcher`, `45 23 * * *` UTC). RSS → `raw_headlines` (scored=FALSE) with same-day dedupe: 60 headlines staged on validation. ET + Business Standard feeds live; Moneycontrol ×2 + Reuters dead (WARN-tolerant by design). Image `local/news-fetcher:v1`.
7. ✅ **FII/DII fetcher — LIVE 2026-09-30** (`manifests/ingestion/fiidii/`, CronJob `fiidii-fetcher`, `0 14 * * *` UTC). NSE `fiidiiTradeReact` JSON (session + browser headers) → `capital_flows` (flow_type FII|DII, direction 'net', amount Rs cr, segment = NSE category). Validated: DII +6952.71 / FII −9980.22 for 29-Sep-2026. Finding: the API returns a bare list, not a `{data: …}` envelope (v1 bug → fixed in v2). Image `local/fiidii-fetcher:v2`.
8. 🔶 **FDI monthly — WATCHDOG ONLY by design** (`manifests/ingestion/fdi/`, CronJob `fdi-watch`, `0 9 1 * *`). No stable machine-readable monthly FDI feed exists (DPIIT factsheets are PDFs, RBI tables unstructured) — auto-import would fabricate data. The job logs current DPIIT factsheet links + the manual INSERT template for human review (validated: exits 0, reports honestly). Procedure: quarterly 5-minute manual INSERT into `capital_flows` (flow_type FDI). US TIC mirror stat: deferred.
9. ✅ **Intraday fetcher — ADDED + DEPLOYED 2026-10-03** (user request: gold, USD/INR
   "and similar" checked more frequently; `manifests/ingestion/intraday/`, CronJob
   `intraday-fetcher`, `10 * * * *` hourly). One `yfinance` pull (1h×2d): 10 FX pairs →
   `fx_rates` (hourly rows alongside daily — nothing computes over that table), GC=F/SI=F/
   CL=F/DX-Y.NYB → new `intraday_prices` hypertable (kept out of `index_prices`: ta-compute
   iterates every ticker over all rows and would run daily RSI/MACD over mixed hourly+daily
   bars). Idempotent against the candles' real timestamps — closed markets write nothing
   new. Validated: 14/14 symbols, 537 rows first run. Shipped alongside:
   `ticker_names` reference table (46 rows, `05-ticker-names.sql`) powering
   "Nifty 50 (^NSEI)"-style labels in the Indices variable + `${ticker:text}` panel
   titles + gold/silver/oil/DXY panels UNIONing daily + intraday (browser-validated).

Daily jobs run staggered (all UTC) so each stage lands before the next — and to keep API load on pi2 low (constraint #3): 11:15 NSE → 14:00 FII/DII → 22:30 yfinance batch → 23:00 FX → 23:15 yields → 23:45 news → 00:15 sentiment (TX2) → 00:30 TA → 01:30 pg-backup → 01:45 pipeline-health. Hourly: intraday-fetcher at :10. Monthly: fdi-watch on the 1st at 09:00.

---

## Phase 4 — Sentiment analysis (TX2 only) — ✅ complete 2026-09-30

1. ✅ Built for CPU inference by design (`manifests/sentiment/`, image `local/sentiment-fetcher:v1`,
   TX2-local, 2.05 GB): JetPack 4.6.1's CUDA-10.2/Python-3.6 era cannot run modern torch, so a
   python:3.11 container with PyTorch CPU aarch64 wheels (torch 2.14.0+cpu, transformers 5.17.0)
   scores the daily batch on ARM cores. FinBERT `ProsusAI/finbert` baked in at build for
   deterministic offline runs. GPU/TensorRT remains an optional future optimization.
2. Deploy as a `CronJob` scheduled after the news fetcher, with `nodeSelector: hardware=gpu` and
   a toleration for the `gpu=true:NoSchedule` taint so it's the only job that lands on
   `ahytx2-desktop`. ✅ LIVE 2026-09-30 (CronJob `sentiment-scorer`, `15 0 * * *` UTC,
   image TX2-local, `Never` pull): 60/60 staged headlines scored in 53 s — score ±p/0 mapping,
   keyword ticker_relevance. No `runtimeClassName` needed (CPU path).
3. Job reads unscored rows from `raw_headlines`, runs inference, writes `sentiment_score` + `ticker_relevance` into `news_sentiment`.
4. Daily aggregate: **deferred to Grafana** (dashboard computes `AVG(sentiment_score)` by day) — no derived rows stored.

---

## Phase 5 — Technical analysis compute — ✅ complete 2026-09-30

1. ✅ LIVE (`manifests/ingestion/ta/`, CronJob `ta-compute`, `30 0 * * *` UTC, image `local/ta-compute:v1`): pure-pandas RSI14 (Wilder) / MACD line+signal+hist / SMA20 / SMA50 / Bollinger(20,2) — no `pandas-ta` dependency. Validated on 36 tickers × 9 indicators post-backfill (tickers with <50 bars skipped).
2. Write results into `technical_indicators`.
3. Keep this separate from the fetch jobs so a slow indicator calc never blocks or delays data ingestion.

---

## Phase 6 — Orchestration & sequencing — ✅ staggered-CronJob route adopted

1. Same two routes, with a new tilt: **prefer the simple staggered-`CronJob` route**.
   Argo Workflows/Tekton controllers sit on the API server watching continuously, and pi2's
   1 GB control plane (constraint #3) is the weakest link in this cluster. Adopt a workflow
   tool only if staggering proves genuinely insufficient.
2. Add a dead-letter/retry pattern — if a CronJob fails (API down, rate-limited), log it and retry once before giving up for the day; don't let one failed source silently break the whole pipeline.
3. Centralize logs — even `kubectl logs` across pods is fine at this scale, or run a small Loki instance on **pi3 or the TX2** if you want searchable logs. **Not pi2** (RAM), and not the 4th Pi (doesn't exist). Revisit placement if the 4th Pi ever gets built.
   **Verdict 2026-09-30:** staggering (table in Phase 3) + WARN-tolerant scripts with
   `backoffLimit` retries IS the orchestration; Loki deferred indefinitely — `kubectl logs`
   suffices at this job count.

---

## Phase 7 — Dashboard & visualization — ✅ live 2026-09-30

1. ✅ Deployed (`manifests/grafana/`: Deployment pinned to pi3 + ClusterIP Service, v12.2.0, file-provisioned datasource + 5 dashboards, tailnet-only via port-forward). Verified via API: health ok, datasource + dashboards provisioned, live query returned 8989 rows / 36 tickers.
2. Don't build one crowded dashboard — split into a small set of focused dashboards, linked from an overview (shipped: Overview, Indices, FX-Commodities-Yields, Sentiment-News, Capital-Flows):
   - **Overview:** single-stat panels for Nifty 50, S&P 500, Sensex, VIX, India VIX, DXY (value + day change), a sentiment-index gauge, and a daily FII/DII net-flow number. Links into the detail dashboards below.
   - **Indices:** time-series per index family (Indian / US / Europe / APAC), using a Grafana template variable for ticker selection instead of one panel per index; optional correlation heatmap once enough history exists.
   - **FX & commodities:** time-series for tracked FX pairs + DXY, and for gold/silver/oil; optional derived panel for gold priced in each tracked currency.
   - **Bonds & rates:** time-series for US Treasury yields + Fed funds rate, India G-Secs + RBI repo rate; a yield-curve panel if capturing multiple maturities.
   - **Sentiment & news:** time-series of daily aggregated sentiment score, a table of the day's most positive/negative headlines, and sentiment scores annotated onto index price charts via Grafana annotations.
   - **Capital flows:** daily FII/DII net flow as a bar chart (inflow above zero, outflow below), a separate monthly FDI trend panel (don't mix daily and monthly cadence on one chart), and a cumulative running-total line to show regime shifts.
3. Use Grafana **template variables** (date range, ticker/index selection) so panels are reused rather than duplicated per ticker.
4. Threshold-based **alerts** — TODO (panels are alert-ready; needs a notification channel configured in Grafana).
5. **Resilience: single replica by decision** — Grafana is stateless (file-provisioned); a second replica adds nothing at this scale.

---

## Phase 8 — Remote access & hardening — ✅ mostly done (Tailscale is live)

1. Remote access **landed differently than planned**: home-hosted WireGuard failed (CGNAT drops
   the return path — router WAN ≠ public IP) and was **fully removed** (keys, configs, pi1/pi2
   WG interfaces, Mac configs). **Tailscale** is deployed on pi1, pi2, pi3, the TX2, the Mac and
   the phone (IPs in "Current state"). Optional finishing touches: add **pi0** to the tailnet
   (remote Pi-hole admin + ad-block DNS on the move), enable MagicDNS with pi0 as tailnet DNS,
   and/or approve a subnet route (`--advertise-routes=<lan-cidr>`) so the DB/Grafana/Pi-hole
   are reachable remotely without per-node installs. No router changes needed anywhere (CGNAT-safe).
2. Grafana/DB stay **tailnet-only** — CGNAT means nothing can be exposed publicly anyway; don't
   expose them publicly regardless. The public-website/domain question is deliberately out of
   scope here (see `REMOTE-ACCESS-DECISION-LOG.md`).
3. Rotate/secure any API keys (FRED, gold API, etc.) as k3s `Secret`s, not hardcoded in the CronJob images.
4. Housekeeping: remove the router's now-unused UDP 51820 port-forward left over from the WireGuard attempt.
5. ✅ DB backups: daily `pg-backup` CronJob (01:30 UTC, `manifests/backup/`) → pi3 SSD hostPath, 7-day rotation, validated (345K dump, BACKUP-OK). Still open: pi0 → tailnet, dead port-forward removal.

---

## Capacity & scaling limits (what this setup is and isn't sized for)

**TX2 (sentiment + DB, consolidated):**
- FinBERT-base-scale sentiment scoring on a few hundred to ~1000 headlines/day: comfortable, minutes not hours.
- TimescaleDB with ~35 indices + FX/bonds/rates/sentiment/capital-flows at daily granularity: comfortable given 7.7 GB RAM, well within the TX2's headroom.
- **Not sized for:** full LLM inference (7B+ params) for daily summarization, high-frequency/intraday data, or heavy concurrent multi-user dashboard load. If any of those become goals later, that's when the RTX 3090 desktop (via on-demand Wake-on-LAN, kept as an optional fallback) or a hardware upgrade becomes worth revisiting.
- **Fixed constraints:** cgroup v1 kernel + agent pinned at v1.35.3 (constraint #1). An optional JetPack 5 reflash (kernel 5.10: cgroup v2, nftables, nfacct) would remove the wrappers and the version pin, but it means a recovery-mode reflash and full re-validation — keep it in the "if needed" bucket.
- **Storage:** eMMC (OS + images — hit 85% during the sentiment build; TX2 docker cruft pruned after) + USB SSD sda4 (DB + tarball staging); mSATA slot still free.

**Pi 3B workers (ingestion, k3s system, Grafana):**
- Comfortable for short-lived, I/O-bound ingestion CronJobs and Grafana at low query concurrency.
- 1GB RAM per node means k3s/containerd already uses a meaningful share — fine as long as nothing memory-heavy (like the DB) is scheduled here, which is why Phase 2 keeps TimescaleDB off this tier.
- pi1 restored 2026-09-29 — all three Pi workers healthy; pi2 stays a light worker by policy (constraint #3).

**Storage hardware notes:**
- TX2: DB volume lives on the attached 224G USB SSD (`sda4` → `/mnt/tx2-ssd`, `pgdata/` subdir alongside existing NAS data — fixed fstab mount, not a desktop auto-mount); eMMC keeps OS + container images only (already 54% full — another reason the DB stays off it). mSATA slot still free as a future second disk.
- 4th Pi worker: 870 EVO still spare/unbuilt; if built, USB-SATA on a Pi 3B is still capped by the shared USB2/Ethernet bus — JMicron/ASM1153E-chipset adapters still recommended.
- pi1/pi2/pi3 already boot from USB SSDs (pi1's is a 233 GB disk, root already expanded) — fine for job state/logs, no SD-card wear worries on those nodes.

**Where clustering helps vs. doesn't:**
- Helps: spreading ingestion load, resilience via Longhorn/OpenEBS for storage, Grafana replica redundancy.
- Doesn't help: adding RAM to a single Postgres instance (true horizontal Postgres scaling, e.g. Citus, is real overkill at this data volume), or giving you a second GPU.
- New: pi2's 1 GB control-plane RAM is the practical ceiling on management-plane ambition (workflow controllers, storage orchestrators, monitoring stacks all watch the API) — another reason the "simple staggered CronJobs" route is the right default here.

---

## Suggested build order (if working solo, evenings/weekends pace)

1. ✅ Phases 0–1: TX2 built, flashed (CTI BSP), joined to k3s as a GPU worker
2. ✅ Physical power-cycles 2026-09-29: pi1 (was down 13+ days) and pi2 (wedged control-plane)
   — all agents rejoined one-at-a-time, full cluster Ready.
3. ✅ Phase 1 complete: label/taint, NVIDIA runtime + containerd fragment, RuntimeClass +
   GPU smoke test passed.
4. ✅ Phase 2 complete: TimescaleDB 2.30.1-pg16 Running on the TX2 (PV bound to
   `/mnt/tx2-ssd/pgdata`, schema applied — 8 hypertables + staging table, write-path verified
   twice including after an agent restart).
5. ✅ Ingestion live: yfinance 28/28, NSE 8/8, FX 10/10, yields 4/4, news 60 staged, FII/DII 2 rows, FDI watchdog.
6. ✅ Sentiment on TX2 (FinBERT CPU, 60/60) + TA (36 tickers) + Grafana (5 dashboards) + pg_dump backups.
7. ✅ FRED key provided 2026-10-03 → rates job live (DFF + DGS depth). Leftovers: RBI rates (no feed), pi0 → tailnet, dead port-forward, alert rules.

---

*Pipeline live end-to-end (FRED key in place 2026-10-03 — no credential gaps remain). Leftovers when convenient: pi0 → tailnet, dead port-forward removal, Grafana alert rules, RBI rates feed.*
