# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Read `AGENTS.md` (repo root) first — it is the binding ground-rules document** (secrets/IP hygiene, data invariants, pi2 fragility, the no-registry image discipline, DB access pattern, docs discipline). This file adds commands and the cross-file architecture picture; where they overlap, AGENTS.md wins.

## Commands

```bash
# Once per clone
git config core.hooksPath .githooks        # pre-commit secret/IP guard

# Every new shell that touches the cluster
export KUBECONFIG=~/.kube/pi-cluster.yaml

# Tests — plain unittest scripts, no pytest; each runs standalone with no DB/network
python3 tests/test_stocks.py               # run one test file
python3 tests/test_stocks.py TestClass.test_name   # run one test (unittest CLI)
bash tests/run-all.sh                      # every test (same entrypoint CI uses)

# Pre-push gate (both must pass)
python3 ops-tools/workloads.py --check     # docs tables agree with manifests
bash tests/run-all.sh
```

There is no build step on the workstation: images are built **on the TX2 node** and distributed as tarballs (ops manual §6; `/rebuild <fetcher>` command runs the full cycle). Any fetcher code change requires a tag bump in its `cronjob.yaml` plus that cycle.

## Architecture (the big picture)

**Data flow:** ~26 CronJobs (one per feed) → TimescaleDB (`timescaledb-0` StatefulSet on the TX2 SSD, Retain PV) → Grafana dashboards + a Telegram digest/bot. Everything lives in the `market-data` namespace on a 5-node k3s cluster (pi0–pi3 + tx2; pi2 is a cordoned, fragile control plane).

**The fetcher pattern** — each feed is a self-contained directory under `manifests/ingestion/<feed>/` (also `manifests/stock-screener/`, `manifests/sentiment/`, etc.):
- `<feed>-fetcher.py` — the job code; feed quirks documented in the script header
- `Dockerfile` + `requirements.lock` — built on TX2 as `local/<name>:vN`, `imagePullPolicy: Never` (no registry, ADR 0004)
- `cronjob.yaml` — schedule + image tag (the tag is the deploy mechanism)
- Writers are idempotent: DELETE+INSERT of only their own `(key, ts)` window
- `manifests/_common/mdjob.py` is the shared helper (connect / write_window / job_run); it is **copied into each build context** at rebuild time, not imported across directories

**Schema** lives in `manifests/timescaledb/schema-configmap.yaml`, applied by `schema-job.yaml` — numbered SQL files, additive-only in practice. Schema changes mean editing the ConfigMap, not psql ad hoc.

**Telegram layer** (`manifests/telegram/`): `bot.py` (two-way listener Deployment), `digest.py` + `alerts.py` (CronJobs), sharing `stocks.py` (message layout), `resolver.py` (symbol resolution), `factcheck.py` (number/direction validation that gates every LLM narrative), `users.py`. One image (`local/telegram-digest`) serves all of them.

**Tests** mirror this: `tests/test_*.py` insert the manifest directory into `sys.path` and exercise the pure logic against scripted fakes — no cluster needed. New fetcher/bot logic should keep that separation (pure functions testable without a connection).

**Docs are the record of truth.** `docs/README.md` is the index with a read/update-when table; `docs/pending-tasks.md` is the single task tracker; `ops-tools/workloads.py --check` mechanically enforces that doc tables (schedules, image tags, CronJob counts) match the manifests — run it after any manifest or doc change. Long-lived decisions go in `docs/adr/` (32 ADRs as of 2026-10-27; several are load-bearing: 0004 no-registry, 0012 bot, 0013 stock_prices ts, 0022–0024 LLM/embeddings, 0026 valuation-is-descriptive, 0027 marketview, 0028 scorecard/earnings/regime, 0029 universes, 0030 Damodaran/Graham-growth, 0031 strategy lab, 0032 DB contract views).

**Ops tooling** (`ops-tools/`): `runcmd.py`/`getfile.py`/`putfile.py` SSH helpers resolve node aliases from gitignored `.hosts.env` and credentials from `.secrets.env` via `@@KEY@@` tokens — never put real IPs or passwords on a command line or in tracked files. `ev0.py` is the gate for any scoring/ML change (evidence roadmap §5).

**Agent roster, slash commands and maintenance skills** are defined under `.opencode/` and listed in AGENTS.md — prefer the listed skills (`canary-validate`, `cluster-mutation`, `backfill-resume`, `sql-conventions`, `commit-and-changelog`, `adr`) over re-deriving their checklists.
