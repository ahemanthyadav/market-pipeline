# Market Data Pipeline — Agent Guide

k3s homelab market-data pipeline: 5-node Pi/Jetson cluster ingesting daily market
data into TimescaleDB, with Grafana dashboards and a Telegram bot/digest.
Git repo on `main` (origin GitHub) — conventional commits; never commit credentials or IPs.
`docs/` is the record of truth; `docs/pending-tasks.md` is the single task tracker.

Read before cluster mutations: `docs/market-data-pipeline-operations-manual.md`
(§5 DB queries, §6 image rebuild cycle, §7 daily checklist, §8 failure runbook,
§9 backup/restore).

## Ground rules

1. **Working directory** — run everything from `marketdata/`. Export
   `KUBECONFIG=~/.kube/pi-cluster.yaml` before any kubectl in a new shell.
2. **pi2 is fragile** (1 GB RAM control-plane). Never restart more than one agent at
   a time; if load > 3 sustained, follow §8 cool-down first.
3. **TX2 fixed config is load-bearing** (agent flags, containerd fragment,
   iptables wrappers — §2). Never "clean up" or apt-upgrade without §8 re-verification.
4. **Data invariants**: fetchers DELETE+INSERT only their own `(key, ts)` rows —
   re-runs are idempotent, never duplicate. `index_prices` stays pure-daily
   (intraday → `intraday_prices`). Timestamps are UTC; `stock_prices.ts` is
   `00:00 UTC` on the trade date (ADR 0013).
5. **PV `timescaledb-tx2-ssd` is Retain** — deleting the PVC keeps data on sda4,
   but never delete the `timescaledb` StatefulSet/PVC/PV casually.
6. **No registry by design**: images are `local/*` with `imagePullPolicy: Never`,
   built on TX2, distributed as tarballs via `ops-tools/`. Any fetcher code change
   = tag bump in `cronjob.yaml` + full §6 cycle + tarball to
   `/mnt/tx2-ssd/image-tarballs/` with `SHA256SUMS.all` regenerated.
   **Never `crictl rmi --prune` / `ctr images prune`** — it deletes every CronJob
   image and `Never` pull cannot recover (incident 2026-10-18). Remove old tags one
   by one, by name.
7. **Secrets never enter tracked files, code, docs, or chat output.** The only local
   store is gitignored `.secrets.env` (template `.secrets.env.example`);
   `ops-tools/*.py` read it themselves and remote commands use the `@@PW@@` token —
   never write a password on a command line; never echo/cat a secret. Agents may not
   read/edit `.secrets.env` (denied in `opencode.jsonc`). Missing value? Ask; never guess.
8. **No IP addresses in tracked files.** Use node names (`pi0..pi3`, `tx2`) or
   placeholders; real addresses live only in gitignored `.hosts.env`. `ops-tools/*.py`
   resolve aliases; agents may read `.hosts.env` but must not copy its values into
   tracked files.
9. **Hypertable queries are bounded in time** — always add a `ts >=` bound in hot
   paths (open-ended per-symbol reads open every chunk; 34 s planning measured on
   3,382 chunks before the 1-year chunk merge). Applications read the `v_*`
   contract views, which carry the bound (`docs/database-contract.md`, ADR 0032).
10. **Never print a secret, even indirectly**: `ps`/`pgrep -af` on a host while
    `runcmd.py` runs shows the expanded `@@PW@@`.

## Before any push

- One-time per clone: `git config core.hooksPath .githooks` (pre-commit guard:
  gitleaks + secret/address/private-IP/forbidden-file checks).
- Must pass: `python3 ops-tools/workloads.py --check` (doc tables agree with
  manifests) and `bash tests/run-all.sh` (every `tests/test_*.py` / `test-*.sh`,
  globbed — CI uses the same entrypoint).
- Conventional commits; push over SSH.

## DB access (read-only by default)

```bash
export KUBECONFIG=~/.kube/pi-cluster.yaml
PW=$(kubectl -n market-data get secret timescaledb-auth -o jsonpath='{.data.password}' | base64 -d)
kubectl -n market-data exec timescaledb-0 -- env PGPASSWORD="$PW" \
  psql -U postgres -d postgres -c "<READ-ONLY SQL>"
```

Roles: `postgres` (jobs/schema), `grafana_ro` (SELECT only), `telegram_app`
(SELECT + writes on `watchlist`/`bot_users` only). Non-SELECT SQL: propose first,
get approval before running.

Key tables: `index_prices`, `fx_rates`, `bond_yields`, `interest_rates`,
`news_sentiment`, `technical_indicators`, `capital_flows`, `intraday_prices`,
`raw_headlines`, `pipeline_heartbeat`, `ticker_names`; screener layer:
`universe_constituents`, `stock_prices`, `stock_fundamentals`,
`stock_metrics_daily`, `stock_scores`, `stock_reports`, `screening_runs`,
`job_state`; bot layer: `watchlist`, `bot_users`; data foundations:
`corporate_actions`, `price_anomalies`, `financial_facts`,
`universe_membership_snapshots`, `factor_returns`, `job_runs`, `llm_narratives`,
`headline_embeddings`. Schema source of truth:
`manifests/timescaledb/schema-configmap.yaml` (applied via `schema-job.yaml`,
never ad-hoc psql DDL).

## LLM use

Only through the shared gateway (`../llm-gateway`, Secret `llm-gateway-key`).
Never a scoring input; never acts on unvalidated AI answers; only public market data
goes out. The number/direction check lives in `manifests/telegram/factcheck.py`.
Embedding requests run with `disable_fallbacks` and store the answering model.

## Analysis work

- Local CSV/Parquet/JSON → DuckDB MCP server (in-memory, `uvx`).
- Cluster DB → the read-only psql pattern above, or the disabled `postgres` MCP
  server (port-forward + `DATABASE_URI`, see `opencode.jsonc`).
- Save plots/tables to `out/`.
- Signal research goes through EV-0 (`ops-tools/ev0.py export|run`, report in
  `out/ev0/`); the evidence roadmap §5 acceptance test is the gate for scoring/ML.
- Load skills `eda`, `data-quality`, `sql-conventions`, `data-viz`,
  `report-writer` instead of re-deriving checklists.

## Commands and subagents

Commands: `/health` · `/db <sql>` · `/eda <file>` · `/rebuild <fetcher>` (full
§6 image cycle) · `/review` · `/test-fix`.

Agents (`.opencode/agents/`): `data-analyst`, `ops`, `reviewer`, `incident`,
`security-auditor`, `pipeline-dev`. Maintenance skills to load instead of
re-deriving: `canary-validate`, `cluster-mutation`, `backfill-resume`,
`commit-and-changelog`, `adr`.

## Docs discipline

- Update `docs/*` whenever you verify or change something; keep "Last verified"
  dates true.
- Mark completed work ✅ in `docs/pending-tasks.md` and update the owning doc in
  the same session — the tracker and docs must never disagree.
- Feed quirks and symbol deaths → manual §4 plus the fetcher script header.
- Long-shelf-life decisions → ADR in `docs/adr/` (32 ADRs; load-bearing ones:
  0004 no-registry, 0012 bot, 0013 stock_prices ts, 0022–0024 LLM/embeddings,
  0026 valuation-is-descriptive, 0027 marketview, 0028 scorecard/earnings/regime,
  0029 universes, 0030 Damodaran/Graham-growth, 0031 strategy lab, 0032 DB contract).
