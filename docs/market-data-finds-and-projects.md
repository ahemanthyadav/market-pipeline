# Market Data Pipeline — Finds & Potential Projects

> **Compiled:** 2026-10-03, after the Grafana validation pass, the hardening pass
> (constraints/heartbeat/restore-drill), the intraday deploy, and the TX2 ML-capacity
> assessment. Everything here was observed live on the cluster, not theorized.
> Doc map: `market-data-architecture.md` (system as built) ·
> `market-data-pipeline-operations-manual.md` (runbooks) ·
> `market-data-pipeline-architecture-roadmap.md` (hardening roadmap) ·
> this file (discoveries + project candidates).

---

## 1. Finds

### 1.1 Infrastructure gotchas (each one cost real debugging time)

| # | Find | Evidence | Implication |
|---|---|---|---|
| F1 | **Grafana postgres plugin ignores top-level `database:`** — reads only `jsonData.database` | Backend `/api/ds/query` returned data while browser panels refused to fire; console: "Postgres requires a default database" | API-level validation can pass while the UI is broken. Browser-validate dashboards (Playwright), not just curl. |
| F2 | **Dashboards without a `time` property open at "Last 6 hours"** | All 5 dashboards rendered "No data" despite data in range; daily rows live at midnight timestamps | Always ship `"time": {"from":"now-90d","to":"now"}` in dashboard JSON. |
| F3 | **`GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH` pointed at the wrong mount** | Home page: "Failed to load home dashboard"; path env referenced `/provisioning/dashboards/` while files mount at `dashboards-files/` | Fixed; grafana home = Overview now. |
| F4 | **pi2 (1 GB control-plane) wedges under registration bursts** — twice in one night | SSH/API handshakes time out while ping/TCP stay alive; `k3s-server` once died exit-1 with no OOM trace and self-restarted | All agent (re)starts: stop everything → cool pi2 (load <2) → start one-at-a-time (pi3 → pi1 → TX2), verify each. Retries against a hot server never break through. |
| F5 | **TX2 systemd-237 lacks `AttachProcessesToUnit`** → `kubectl exec` fails with containerd `SystemdCgroup=true` | "error adding pid to cgroups: Unknown method 'AttachProcessesToUnit'" | cgroupfs pair required: containerd `SystemdCgroup=false` (runc AND nvidia) + kubelet `--cgroup-driver=cgroupfs`. |
| F6 | **Editing k3s systemd units can produce dangling ExecStart continuations** | Single-line unit + inserted line without trailing `\` → flag silently dropped | Always re-check parsed `systemctl show <unit> -p ExecStart` after patching. |
| F7 | **eMMC accumulates Pi-only build artifacts silently** — hit 88% (alert 85%) | TX2 docker held intraday/rates/health images it can never run (GPU taint) | Pruned; docker data-root relocated to sda4 (`/mnt/tx2-ssd/docker`) — future builds cost eMMC nothing. |
| F8 | **L4T nvidia runtime injects driver + devices only, not CUDA toolkit** | GPU smoke test: `/dev/nvhost-*`, `libcuda.so` present; `/usr/local/cuda` absent | Container image must ship the toolkit (`l4t-cuda`-style base); `NVIDIA_VISIBLE_DEVICES` is an input the runtime reads, not sets. |
| F9 | **No-registry image cycle has a pi2 tax** | `ctr images import` is slow on pi2 and blocks the SSH channel | Run pi2 imports detached (nohup + poll `/tmp/*.log`). |
| F10 | **CGNAT is structural, not fixable at home** | Router WAN ≠ public IP; WireGuard handshakes received but replies dropped | Tailscale is the only remote path; any public-serving ambition needs a VPS/tunnel (see decision log). |

### 1.1b UI/report accuracy finds (2026-10-26 — found by reviewing on-screen values)

| # | Find | Fix |
|---|---|---|
| F33 | **Docker `ADD <url>` creates files root:0600** → unreadable by a `runAsUser: 1000` pod. Starlette StaticFiles stats the file (stat needs only dir perms), sends `200` + `content-length`, then fails on open — **empty body behind a green status**. marketview's vendored Chart.js served 0 bytes; every chart silently dead while all route canaries passed | `chmod -R a+rX` after the ADD (marketview v2); §6 canary must fetch the static asset and CHECKSUM it, not just check routes (marketview `docs/OPERATIONS.md` §4) |
| F34 | **Ticker-vs-headline `ILIKE '%SYM%'` substring matching**: "ALL" matched every "f**all**s"/"r**all**y" headline; word boundaries (`\mALL\M`) still match the English word "All". Skewed per-stock news sentiment averages in stock_reports and showed unrelated headlines on dossiers | Ticker matches only `ticker_relevance` (where it IS a symbol); headlines match the company NAME, word-bounded (`stock_report.py` v22, marketview v3). Same trap waits for ON, IT, A, SO, KEY, COST… |

### 1.2 Data & feed quirks

| # | Find | Status |
|---|---|---|
| F11 | Yahoo symbol deaths: `FTSE.MI` → `FTSEMIB.MI` (fixed); `^CNXAUTO`/`^CNXFMCG` stale-dropped; **Nifty 500 has no Yahoo symbol at all** | Documented; 8/8 NSE symbols live via validated set |
| F12 | NSE FII/DII API returns a **bare list**, not a `{data:…}` envelope | Fixed in `fiidii-fetcher` v2 |
| F13 | RSS: Moneycontrol ×2 (login-consent wall) + Reuters (host gone) dead — **fixed 2026-10-12** with 4 replacement feeds (LiveMint, BusinessLine, CNBC, MarketWatch) via the CronJob `FEEDS` env override | WARN-tolerant fetcher; source mix changed 10-12 |
| F14 | **RBI G-Secs/repo rate: no stable machine-readable feed**; DPIIT FDI factsheets are PDFs | RBI = open gap; FDI = watchdog + quarterly manual INSERT (by design) |
| F15 | Commodities wanted no separate pipeline: GC=F/SI=F/CL=F ride in the yfinance batch | `commodity_prices` stays empty by design |
| F16 | **ta-compute iterates every `index_prices` ticker over all rows** → intraday data must never enter that table | Hourly candles live in `intraday_prices`; FX hourly in `fx_rates` (nothing computes over it) |
| F17 | Idempotency pattern: row-level DELETE+INSERT, keyed on the source's **real timestamp** — closed markets naturally write nothing new on re-runs | 537-row intraday first run; no pollution on weekends |
| F18 | Restore path is **measured, not estimated**: `pg_restore` 2 s @ 388 KB, counts match | Re-run quarterly via `restore-drill-job.yaml` |
| F19 | Job history limit (3) makes the **heartbeat table the only durable run record** | `pipeline_heartbeat` × 10 feeds, checked 01:45 UTC |

### 1.3 Stock-screener rollout finds (2026-10-04 — each cost a debug cycle)

| # | Find | Fix |
|---|---|---|
| F20 | **k3s cron schedules run in NODE-LOCAL PDT, not UTC** — verified when the "02:00-02:30 UTC" screener chain fired 09:00-09:30 UTC. All documented "UTC" times were PDT all along; functionally fine (staggering + market-close ordering hold) | Docs corrected; schedules deliberately left as-is |
| F21 | **niftyindices.com CSV path dead** (JS HTML shell at the old URL) | `archives.nseindia.com/content/indices/ind_nifty500list.csv` — which also carries Industry (sector) |
| F22 | `pandas.read_html` fails on the Wikipedia S&P 500 page markup (exception embeds the whole page) | Direct `lxml.html` parse of table `#constituents` — 503 rows incl. CIK |
| F23 | **SEC EDGAR companyfacts nesting**: units live under `facts.{tag}.units.{unit}[]` — iterating the tag dict silently yields zero facts | Parse through the `units` level; delivers ~2006→ history |
| F24 | **yfinance 1.7 renamed statement rows** to CamelCase (`NetIncome`, `TotalRevenue`, `DilutedEPS`) | Candidate lists per metric covering both naming schemes |
| F25 | **Yahoo throttles multi-symbol max-range chunks** into silent multi-minute retries (25 min, zero chunks) | Per-symbol `Ticker.history(period='max')` + `job_state` cursor — flows at ~36s/symbol |
| F26 | **Idle transactions get dropped by the pod→DB path** during long fetches ("the connection is closed") | House pattern: autocommit reads, `with conn.transaction():` per write unit, reconnect-retry once |
| F27 | **Postgres `max_locks_per_transaction=64` exhausts** under concurrent hypertable access → "out of shared memory", killed backends | 256 via StatefulSet `args:` (NOT `command:` — that replaces docker-entrypoint.sh → DB crash loop) |
| F28 | **sed-bumping an image tag in cronjob.yaml does nothing until re-APPLIED** — the report job was "validated" 3× against a stale v1 while the fix sat in v6 | Verify the LIVE tag via jsonpath before trusting any re-validation (manual §8) |

### 1.5 Telegram/alerting rollout finds (2026-10-09 — each cost a crash-loop cycle)

Grafana EXITS the whole process on any alerting-provisioning error, and the
section is all-or-nothing per file: one broken rule takes the contact point
down with it. Every error below crash-looped Grafana until the configmap was
fixed and the pod re-created.

| # | Find | Fix |
|---|---|---|
| F29 | **Env expansion re-types digit-only values as numbers — quotes do not help**: `chatid: "$TELEGRAM_CHAT_ID"` → expanded → `Config.chatid of type string` unmarshal error anyway | Literal quoted value in the file for digit-like settings (chatid is an identifier, not a credential — the token stays env-expanded; non-numeric values are safe) |
| F30 | **Alert-rule queries need a nonzero lookback**: `relativeTimeRange: {from: 0, to: 0}` → `invalidRelativeTime [From: 0s]` | `from: 600, to: 0` on every query AND expression node |
| F31 | **`policies:` fails invalidFormat while its receiver doesn't exist** — the route tree validates against live contact points, and 12.2's paths moved: policy tree = `/api/v1/provisioning/policies`, rules list = `/api/prometheus/grafana/api/v1/rules`; `contactPoints:` entries need a nested `receivers:` list (flat form is silently skipped) | Provision the contact point first, add the route tree after; use the kebab-case/nested forms |
| F32 | **A pod recreated seconds after a configmap apply can mount the PREVIOUS revision** (kubelet informer cache lag) and crash-loop on the old error, muddying which fix failed | After applying, wait ~60 s before deleting the pod — or expect one stale crash and judge by the SECOND attempt's logs |

### 1.4 ML capacity on the TX2 (measured 2026-10-03)

| Resource | Live state | ML implication |
|---|---|---|
| CPU | 1–3% used, load 0.17 (6 cores) | Wide open for batch inference |
| GPU | 0% — idle; sentiment runs CPU by design | Available, **but see the catch** |
| RAM | 5.9 GB available of 7.86 (DB capped 2 Gi) | ~4–5 GB for new ML outside the 00:15–00:30 UTC sentiment window (which takes up to 4 Gi) |
| eMMC | 54% (2026-10-10: docker + k3s containerd both on sda4) | No longer a constraint for images |
| sda4 | 145 GB free (DB + docker data-root) | Room for models, embeddings stores, build churn |

**The GPU catch (hard ceiling):** TX2 is Pascal (sm_62) + CUDA 10.2 on JetPack 4.6.1 —
**modern PyTorch cannot use this GPU at all**. GPU options are: old L4T-era torch
(1.10–1.12) containers, or **TensorRT 8.2.1 (installed, Pascal-compatible)** running
ONNX-exported models. CPU-side modern torch works fine (2.14 runs today).
Lifting the ceiling entirely = JetPack 5 reflash (icebox — recovery-mode, full revalidation).

---

## 2. Potential projects

### Tier 1 — quick wins (days; everything already unblocked)

1. **Grafana alert rules** — `pipeline_heartbeat.fresh` is the ready-made alert source;
   `fresh=false` = missed job/stale feed. Candidates: VIX spike, sentiment drop, large
   FII outflow, stale feed, missed heartbeat. **Only decision needed:** notification
   channel (Telegram favorite — its bot also enables project #7).
2. **Entity/event extraction on headlines** — ✅ **DONE 2026-10-09** (ADR 0010):
   `headline-ner` CronJob (TX2, 00:45 PDT, image v3) — gazetteer extraction from
   `ticker_names` into `headline_entities` (instrument-linked) + 16-type keyword
   `headline_events`; deterministic, no model; 323 entities / 163 events over the
   563-headline history on first run. Feeds #10; #10 can start any time.
3. **Per-job heartbeat v2 + upstream guards** — each fetcher upserts its own
   `last_ok`/rows/duration; sentiment/TA self-check source freshness before computing.
   Lands with the next natural image rebuilds (don't rebuild 9 working images just for this).
4. **news_sentiment/raw_headlines unique constraints** — needs `ON CONFLICT DO NOTHING`
   in scorer/fetcher first (INSERT-only writers today). Pairs with #3's rebuild.
5. **Extend intraday coverage** — add `^GSPC`, `^NSEI` etc. to the hourly pull (one-list
   change in `intraday-fetcher`); intraday equity-index candles need a second hypertable
   consumer check (dashboards only — TA never touches intraday).
6. **Telegram daily digest bot** — 08:00 IST message: Nifty/S&P/Sensex/VIX/DXY closes,
   sentiment gauge, FII/DII net, stale-feed warnings. Reuses all existing tables; doubles
   as the Grafana alert channel (#1).
7. **Security hygiene pass** — secrets out of git ✅ (2026-10-10, `.secrets.env`);
   remaining: pi0 → tailnet; delete the dead router UDP 51820 forward. (Credential
   rotation deliberately not a priority.)

### Tier 2 — medium projects (a weekend each, real new capability)

8. **RBI G-Secs/repo feed hunt** — ✅ **DONE 2026-10-09**: no third-party feed
   needed; the official RBI mobile "Current Rates" widget carries everything
   (policy repo/SDF/MSF/bank/reverse-repo + CRR/SLR, 5 G-Sec benchmark yields,
   3 T-bill cut-offs). `rbi-fetcher` (07:00 PDT) scrapes it with lxml;
   tenor-bucketed into `bond_yields` (`IN_GS_{3,5,10,15,30}Y`) and
   `interest_rates` (`IN_*`). India yields panel added to the Bonds dashboard.
9. **Nifty 500 / Auto / FMCG sources** — **split 2026-10-09**: Nifty 500
   restored via Yahoo `^CRSLDX` (live probe: 21 valid daily bars; nse-fetcher
   v3). Auto/FMCG confirmed dead across Yahoo symbol families (`^CNXAUTO`,
   `NIFTYAUTO.NS` — current-quote ghosts only); NSE-direct (session-cookie API
   like fiidii) or jugaad-data remain the only avenues.
10. **News/event intelligence** — on top of #2: dedupe clusters, event timelines,
    per-company sentiment aggregation. This is where the pipeline starts answering
    "what happened and how did markets react" rather than just recording prices.
11. **GPU experiments via TensorRT 8.2** — export a small model (classifier/embedding)
    to ONNX, serve via the proven RuntimeClass path. Useful + satisfies curiosity;
    NOT a prerequisite for anything.
12. **Derived metrics pack** — gold priced in INR (USDINR × GC=F), yield-curve spreads
    (10Y−2Y), FII-flow vs Nifty-returns correlation panel, sentiment as an annotated
    overlay on index charts. All pure SQL + Grafana; no new ingestion.

### Tier 3 — big bets (need the 3090 box or new hardware)

13. **RTX 3090 AI layer** — WoL, larger models, embeddings at scale, RAG.
    The TX2 stays data/edge; the 3090 does anything modern-GPU (roadmap §7 split).
14. **GraphRAG** — entity graph (company → sector → index → news event → sentiment →
    flow → price) once #2/#10 have produced reliable entity/event history. Deferred
    until there are actual relationship questions to ask (roadmap §8).
15. **Historical analytics** — regime detection, correlation heatmaps, backtests.
    Blocked only on accumulating history (backfills seeded 1y; every day adds more).
16. **Streaming** — only if a use case demands it; the daily/hourly batch covers current
    needs. Declined for now (roadmap §9).

### Icebox — understood, deliberately not doing

- JetPack 5 reflash (would fix cgroups/nftables/version-pin properly — full revalidation cost)
- Longhorn/OpenEBS, Loki, Argo/Tekton, Citus (pi2 1 GB ceiling)
- 4th Pi worker (optional; cluster has capacity)
- Public website/VPS-in-front (tracked in `REMOTE-ACCESS-DECISION-LOG.md`, separate effort)

---

## 2b. External quant repos — evaluated 2026-10-30

Owner supplied a prioritised list of seven open-source quant repos; each was judged against the
standing ADRs (0018 EV-0 gate, 0026 deterministic valuation, 0031 no-LLM research scoring,
0022/DG-19 TX2 ML ceiling) and `data-gaps.md`. Verdict: the list's ranking is roughly inverted
for this pipeline — the two smallest libraries fit, the four platforms conflict with decisions
already recorded. None becomes cluster infrastructure; adopted ones are **workstation research
tools** reading via the ADR 0032 `v_*` contract views, with EV-0 still the gate.

| Repo | Verdict | Why |
|---|---|---|
| **QuantStats** | ✅ Adopt (workstation) | Pandas tearsheets (Sharpe, drawdown, rolling stats) over the returns series `ev0.py` / Strategy Lab already produce; replaces hand-rolled stats. Smallest dep, clearest win. |
| **VectorBT** | ✅ Adopt (workstation, pinned) | Vectorized numba backtesting for Strategy Lab testables (EV-19 PEAD, EV-20 panels) — parameter sweeps the hand-written harness can't do cheaply. Augments EV-0, never replaces it; acceptance criteria still written first. OSS version is maintenance-mode (maintained fork is paid `vectorbt.pro`) — pin the version, treat as tool not infrastructure. |
| **Riskfolio-Lib** | 🧊 Icebox | Portfolio optimization is premature: ADR 0026 keeps scoring descriptive and nothing has passed EV-0 into an allocation decision. Revisit only with multiple validated signals to weight. |
| **Qlib** (Microsoft) | ⛔ Skip platform; mine Alpha158 | Competing platform: own data format/pipeline, x86/GPU workflows the TX2 can't serve (DG-19). Would duplicate TimescaleDB + fetcher architecture to feed it. The salvageable asset is the **Alpha158 factor library**: public, well-documented expressions over plain OHLCV — cherry-pick candidates, implement natively in SQL/pandas over `stock_prices`/`stock_metrics_daily`, gate each through EV-0. Pure-price factors dodge the India fundamentals gaps (DG-03/DG-17) entirely. Qlib's own benchmarks also show its DL zoo barely beats a tuned LightGBM — if ML ranking is ever wanted, that is the ceiling to aim at with our own features, not the platform. |
| **RD-Agent** | ⛔ Skip | Autonomous LLM factor discovery — the direct opposite of the house rules: ADR 0031 scoring is deterministic (no LLM in the loop) and acceptance tests precede testing. A data-snooping machine (McLean & Pontiff); also sits on Qlib. |
| **TradingAgents** | ⛔ Skip | Multi-agent LLM narrative. The Telegram layer already does LLM narrative *with* `factcheck.py` gating every number — a stronger design. Adding it = ungated LLM opinions + gateway API spend. |
| **OpenBB** | ⛔ Skip (as infra) | The 26 fetchers with idempotent writers already are the ingestion layer; closes no real gap (DG-01 needs NSE PDFs, DG-02 needs a nonexistent archive, DG-04 needs a Finnhub/Tiingo key). Free providers are mostly the same yfinance. At most a workstation exploration tool when hunting a new source. |

**Framing note:** the limiting factor is data, not tooling — the top of `data-gaps.md` (DG-01
India point-in-time membership, DG-03 NSE fundamentals backfill) blocks India backtests no matter
which backtester is adopted. A session on DG-01 buys more research capability than any repo above.

---

## 3. Recommended order

1. Telegram bot (#6) → gives you the alert channel AND a daily digest immediately
2. Grafana alerts (#1) on top of it
3. Entity extraction (#2) → event intelligence (#10) — the ML headroom is waiting
4. RBI hunt (#8) whenever a free evening + curiosity coincide
5. Everything else opportunistically; Tier 3 stays parked until the 3090 wakes up
