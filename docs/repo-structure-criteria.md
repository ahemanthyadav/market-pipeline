# Repo structure criteria — single repo vs separate repo

> **Purpose:** a decision framework for whether a component lives in the `marketdata`
> monorepo or gets its own repo. Based on the actual constraints of this cluster
> (no registry, `imagePullPolicy: Never`, tarball distribution, single CI, single
> pre-commit guard, one owner, 5-node homelab).
>
> **Source:** `docs/system-architecture-visual.md`, `docs/market-data-architecture.md`,
> `AGENTS.md`, ops manual §6 (image rebuild cycle), AR-5 image consolidation.
>
> **Maintained:** update this doc whenever a component moves between repos or the
> criteria change.

---

## The four criteria (all must be "yes" to split out)

| # | Criterion | Question | Why it matters here |
|---|-----------|----------|---------------------|
| C1 | **Deployment target** | Does it run outside the `market-data` k8s namespace, or outside k8s entirely? | The monorepo's CI, manifests, and `kubectl` workflow all target `market-data`. Anything else needs its own apply path. |
| C2 | **Lifecycle / cadence** | Does it have a different release schedule, owner, or breakage blast radius than the cluster CronJobs? | A shared gateway or web app can be rebuilt/rolled back without touching 36 CronJobs. |
| C3 | **Dependencies** | Does it need base images, language versions, or system packages that don't fit the 3 consolidated images (`marketdata-light`, `marketdata-data`, `marketdata-ml`)? | AR-5 consolidated all fetchers onto 3 images to avoid rebuilding 20 images per change. A divergent stack breaks that economy. |
| C4 | **Independent release** | Would it ever be versioned, tagged, or consumed by something outside this cluster? | If the answer is no, a separate repo adds coordination cost with zero benefit. |

**Rule:** If all four are "yes" → separate repo. If any is "no" → stays in `marketdata/`.

---

## What stays in the single `marketdata` repo

Everything that runs in the `market-data` k8s namespace and shares the 3 base images, CI, and pre-commit guard.

### All CronJobs (36 live)

| Component | Manifest | Image | Why it stays |
|-----------|----------|-------|--------------|
| `yfinance-fetcher` | `manifests/ingestion/yfinance/cronjob.yaml` | `marketdata-data:v6` | Shares base image, `_common/mdjob.py` pattern, `PGUSER` Secret pattern |
| `nse-fetcher` | `manifests/ingestion/nse/cronjob.yaml` | `marketdata-data:v6` | Same |
| `fx-fetcher`, `yields-fetcher`, `rates-fetcher`, `rbi-fetcher` | `manifests/ingestion/{fx,yields,rates,rbi}/` | `marketdata-data` / `marketdata-light` | Same |
| `news-fetcher`, `intraday-fetcher` | `manifests/ingestion/{news,intraday}/` | `marketdata-light` / `marketdata-data` | Same |
| `fdi-watch`, `fiidii-fetcher`, `research-watcher` | `manifests/ingestion/{fdi,fiidii,research}/` | `marketdata-light` | Same |
| `ta-compute` | `manifests/ingestion/ta/cronjob.yaml` | `marketdata-data:v6` | Same |
| `sentiment-scorer`, `headline-ner` | `manifests/{sentiment,headlines}/cronjob.yaml` | `marketdata-ml` / `marketdata-light` | TX2-only, but still `market-data` namespace |
| `stock-prices`, `stock-fundamentals`, `stock-metrics` | `manifests/stock-screener/cronjobs.yaml` | `marketdata-data:v6` | Screener chain — tightly coupled by data flow |
| `stock-screener`, `stock-report`, `stock-valuation` | `manifests/stock-screener/cronjobs.yaml` | `marketdata-data:v6` | Same chain; `FUND_SOURCE` switch couples screener to fundamentals |
| `prediction-tracker`, `earnings-calendar`, `accuracy-check` | `manifests/stock-screener/cronjobs.yaml` | `marketdata-data:v6` | Reads `stock_scores` / `stock_valuations` — part of screener output |
| `pipeline-health` | `manifests/health/cronjob.yaml` | `marketdata-light:v1` | Reads all 14 feeds; writes `pipeline_heartbeat` |
| `news-embed` | `manifests/llm/cronjob.yaml` | `marketdata-light:v1` | ML-2 embeddings; calls `llm-gateway` but writes to cluster DB |
| `llm-narrative` | `manifests/llm/cronjob.yaml` | `marketdata-light:v1` | ML-7a; reads facts from DB, calls gateway, writes `llm_narratives` |
| `universe-refresh`, `membership-history` | `manifests/stock-screener/backfills.yaml` | `marketdata-data:v6` | Security master; feeds every screener job |
| `pg-backup` | `manifests/backup/cronjob.yaml` | `timescale/timescaledb` | Dumps the same TimescaleDB the CronJobs write |

### Always-on Deployments (2 live in `market-data`)

| Component | Manifest | Why it stays |
|-----------|----------|--------------|
| `telegram-bot` | `manifests/telegram/deployment.yaml` | Pinned pi1; same image as digest/alerts; `telegram_app` DB role |
| `marketview` (wait — this is separate) | — | See separate section below |
| `grafana` | `manifests/grafana/deployment.yaml` | `grafana_ro` DB role; reads TimescaleDB; alert rules fire into Telegram |

### Shared infra inside the repo

| Component | Path | Why it stays |
|-----------|------|--------------|
| `mdjob` shared helper | `manifests/_common/mdjob.py` | AR-2/AR-3; copied into every fetcher build context; one change ripples to all jobs |
| Build images | `manifests/images/{data,light,ml}/` | AR-5 consolidation — 3 base images for all 36 CronJobs |
| Cluster ops tools | `ops-tools/{runcmd,putfile,getfile,_creds,ev0,...}.py` | SSH/auth/image-distribution layer for the whole cluster |
| Tests | `tests/test_*.py` + `run-all.sh` | One suite, one CI entrypoint; `workloads.py --check` verifies docs/manifest drift |
| Schema | `manifests/timescaledb/schema-configmap.yaml` | Single source of truth for all tables; applied via `schema-job.yaml` |
| Docs | `docs/` | AGENTS.md discipline — tracker and docs must never disagree |

### Cross-cutting changes that prove the monorepo works

- **AR-5 image consolidation** (2026-10-30): touched ~20 manifest files, deleted 17 Dockerfiles/`requirements.lock`, built 3 new images — one commit, one CI run, one tarball archive update.
- **AR-8 role flip** (`postgres` → `pipeline_rw`): manifest + Secret change across all pipeline-fetcher CronJobs in one apply.
- **DQ-1 / DQ-2 / DQ-3**: schema SQL + fetcher code + screener reader switch, all verified together.

---

## What lives in a separate repo

Only components that satisfy all four criteria (C1–C4).

### `llm-gateway` — separate repo (`../llm-gateway`)

| Criterion | Verdict | Evidence |
|-----------|---------|----------|
| C1 Deployment target | ✅ Yes | Own k8s namespace `llm`, not `market-data` |
| C2 Lifecycle / cadence | ✅ Yes | Own release cycle; marketdata is just one consumer (10 req/min, free capabilities) |
| C3 Dependencies | ✅ Yes | LiteLLM 1.106.0, own `litellm` DB, NVIDIA key — doesn't fit `marketdata-{light,data,ml}` |
| C4 Independent release | ✅ Yes | Consumed by marketdata via NodePort :30400; could be consumed by other projects |

**Interface:** marketdata calls it over the tailnet via OpenAI SDK; gateway key lives only in its own Secret (`llm-gateway-key`). Marketdata never hosts gateway code.

### `marketview` — separate repo (`../marketview`)

| Criterion | Verdict | Evidence |
|-----------|---------|----------|
| C1 Deployment target | ✅ Yes | Deployment on pi1, served via `tailscale serve`; not a CronJob in `market-data` |
| C2 Lifecycle / cadence | ✅ Yes | Own image tag (`marketview:v13`); routes and frontend evolve independently of screener schedule |
| C3 Dependencies | ✅ Yes | Web app stack; doesn't use the 3 consolidated base images |
| C4 Independent release | ✅ Yes | Exposed as `https://pi1.<tailnet>.ts.net/`; could be consumed independently |

**Interface:** reads from TimescaleDB (via `grafana_ro` or a dedicated read role); not part of the screener chain.

---

## Gray areas — how to decide

| Question | Heuristic | Example |
|----------|-----------|---------|
| "It calls the LLM gateway — shouldn't it be separate?" | **No.** It writes to the cluster DB and runs on the cluster schedule. `llm-narrative` and `news-embed` stay in `marketdata/`; only the gateway itself is separate. | `manifests/llm/cronjob.yaml` |
| "It's ML — shouldn't it be separate?" | **No.** ML jobs run as CronJobs in `market-data`, use the same base images, and are gated by EV-0 §5. The roadmap explicitly says ML "reuses EV-0, never bypasses EV §5." | `sentiment-scorer`, `news-embed`, `ml1-anomalies` |
| "It's a backfill — shouldn't it be separate?" | **No.** One-shot Jobs share the same image and DB as the daily fetcher they backfill. | `backfill-yfinance-1y`, `backfill-nse-fundamentals` |
| "It's a tool — shouldn't it be separate?" | **No.** `ops-tools/` is the cluster's SSH/auth layer; every repo would need its own copy. | `runcmd.py`, `putfile.py`, `ev0.py` |
| "It reads TimescaleDB — shouldn't it be separate?" | **Only if it doesn't run on the cluster.** `grafana` reads DB but stays because it's a Deployment in `market-data`. `marketview` reads DB but is separate because it's its own Deployment with its own lifecycle. | `grafana` stays; `marketview` goes |

---

## Decision tree

```
Is it a k8s CronJob or Deployment in namespace `market-data`?
├─ YES → stays in marketdata/ (regardless of ML/LLM/screener/backup)
│
└─ NO → Does it satisfy ALL four criteria (C1–C4)?
    ├─ YES → separate repo (llm-gateway, marketview)
    └─ NO → stays in marketdata/ (ops-tools, tests, docs, shared helpers)
```

---

## If you ever split something out

The cleanest candidate would be **`ev0.py`** (signal test harness) — it's reusable research code that reads from the cluster via `grafana_ro` but doesn't need to run on it. Even today it lives in `ops-tools/` because it's tightly coupled to the cluster's read-only role and DB connection pattern. A split would require:

1. Its own CI/test entrypoint (no longer covered by `tests/run-all.sh`)
2. Its own secret handling for the `grafana_ro` connection
3. Documentation of the interface (CLI flags, output format)
4. A versioning/tagging scheme independent of the cluster images

Until that's worth the coordination cost, it stays in `ops-tools/`.

---

## Summary

| Scope | Repo | Governing principle |
|-------|------|---------------------|
| All cluster workloads (36 CronJobs + 2 Deployments + schema + ops-tools + tests + docs) | `marketdata` (monorepo) | One CI, one pre-commit guard, 3 base images, atomic cross-cutting changes |
| Shared LLM infrastructure | `llm-gateway` (separate) | Own namespace, own key, own release cycle, consumed over tailnet |
| Web app | `marketview` (separate) | Own Deployment, own image tag, own lifecycle, served via tailscale |

**Default answer: keep it in `marketdata/`.** Split only when all four criteria are clearly met, and document the split here when it happens.
