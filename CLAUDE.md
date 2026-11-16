# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Read `AGENTS.md` (repo root) first — it is the binding ground-rules document** (secrets/IP hygiene, data invariants, pi2 fragility, the no-registry image discipline, DB access pattern, docs discipline). This file adds commands and the cross-file architecture picture; where they overlap, AGENTS.md wins.

## Commands

```bash
# Once per clone
git config core.hooksPath .githooks        # pre-commit secret/IP guard (gitleaks + address checks)

# Every new shell that touches the cluster
export KUBECONFIG=~/.kube/pi-cluster.yaml

# Tests — plain unittest scripts, no pytest; each runs standalone with no DB/network
python3 tests/test_stocks.py                         # one file
python3 tests/test_stocks.py IndexQuantTest          # one class
python3 tests/test_stocks.py TestClass.test_name     # one test
bash tests/run-all.sh                                # everything (globbed; CI uses the same entrypoint)

# Pre-push gate (both must pass; check run-all's exit code directly — piping it through tail hides failures)
python3 ops-tools/workloads.py --check     # docs tables (schedules, image tags, CronJob counts) agree with manifests
bash tests/run-all.sh

# Extra checks CI also runs (.github/workflows/ci.yml)
ruff check --select E9,F63,F7,F82 .        # fatal class only; F401 deliberately not enforced
shellcheck -S error $(git ls-files '*.sh')
```

New test classes must go **before** the `if __name__ == "__main__":` guard, or they silently never run. Check the "Ran N tests" count went up.

There is no build step on the workstation. Images are built **on the TX2 node** and distributed as tarballs (ops manual §6, `docs/deployment-guide.md`; the `/rebuild` command runs the full cycle).

## Architecture (the big picture)

**Data flow:** 36 CronJobs → TimescaleDB (`timescaledb-0` StatefulSet on the TX2 SSD, Retain PV) → Grafana, the Telegram bot/digest, and marketview (a sibling repo). Everything runs in the `market-data` namespace. The k3s cluster has 6 nodes: pi1, pi2 (cordoned, fragile control plane), pi3, tx2, orin1, orin2. pi0 is DNS only and not a node. Who runs what, and the decided-but-not-applied target layout: `docs/node-roles.md` (§1 live, §1b target).

**Images: a few shared images, not one per feed.** Code lives where it belongs (`manifests/ingestion/<feed>/`, `manifests/stock-screener/scripts/`, `manifests/ml/`, `manifests/_common/`, `ops-tools/ml*.py`). It is packaged into shared images defined under `manifests/images/{data,light,ml}/`, plus `manifests/telegram/` (`local/telegram-digest`, which serves the bot Deployment and the digest/alerts CronJobs).
- Each CronJob selects its script with `command: ["python", "<script>.py"]`.
- **The build context is assembled from the Dockerfile's COPY list.** Every COPY'd basename must resolve to exactly one tracked file anywhere in the repo, so script names must stay unique repo-wide.
- Shared helpers (`_common/mdjob.py` connect/write_window/job_run, `_common/sessions.py` exchange calendars) are copied in, not imported across directories.
- A code change means a new tag in **every** manifest that references that image (`grep -r 'local/marketdata-data:' manifests`), the §6 cycle, and an import on every node that can run those jobs. Verify the in-image md5 against the repo before rollout.

**Writers are idempotent:** DELETE+INSERT of only their own `(key, ts)` window, in one transaction. Review findings recur where code deletes a wider window than it rewrites.

**Schema** lives in `manifests/timescaledb/schema-configmap.yaml` as numbered keys. Apply it by running `schema-job.yaml` under a **new Job name**; never run DDL in psql or from application code (writers run as `pipeline_rw`, which has DML only).
- **Every key is re-applied on every run.** An older key must never undo a newer one: if a later key widens a view, guard the earlier definition with `to_regclass(...) IS NULL`.
- A role must be created at or before the first key that GRANTs to it.
- `tests/test_manifest_safeguards.py` (`SchemaReapplySafety`) enforces both. The job fails with `SCHEMA-FAIL <file>`.
- Applications read the `v_*` contract views (`docs/database-contract.md`, ADR 0032), which carry the rule-9 `ts >=` bound.

**Telegram layer** (`manifests/telegram/`):
- `bot.py`: the two-way listener.
- `digest.py` + `alerts.py`: CronJobs.
- `router.py`: deterministic routing first, then an AI fallback.
- `stocks.py`: message layout; must fit `PHONE_WIDTH` 38 and show how every derived number was computed.
- `resolver.py`/`nicknames.py`: symbol resolution.
- `users.py`: the access registry.
- `factcheck.py` gates every LLM narrative. A number passes only if it equals a fact rounded or truncated to the decimals written, or its percent form. It also ships in the llm ConfigMap via `ops-tools/apply-llm.sh`.
- Every handler call in `bot.handle()` must go through `run_command()`, which enforces the owner-only `ADMIN` check. Routing paths must not call `COMMANDS[...]` directly.

**Tests** insert the manifest directory into `sys.path` and exercise pure logic against scripted fakes; no cluster needed. Keep new logic in pure functions testable without a connection. A fake that ignores the SQL it receives cannot catch query bugs (e.g. the `DISTINCT ON` key); when the SQL is the point, make the fake honour it.

**Docs are the record of truth.**
- `docs/README.md` indexes every doc with a read/update-when table.
- `docs/pending-tasks.md` is the single task tracker; mark items ✅ with a date and update the owning doc in the same session.
- `ops-tools/workloads.py --check` mechanically enforces doc/manifest agreement.
- Long-lived decisions go in `docs/adr/` (33 ADRs; load-bearing ones are listed in AGENTS.md).

**Ops tooling** (`ops-tools/`):
- `runcmd.py`/`getfile.py`/`putfile.py` SSH helpers resolve node aliases from gitignored `.hosts.env` and credentials from `.secrets.env` via `@@KEY@@` tokens. Never put real IPs or passwords on a command line or in tracked files.
- `runcmd.py` exits 4 when the remote command fails.
- `ev0.py` is the gate for any scoring/ML change (evidence roadmap §5). Point-in-time joins must key on the filing date, not `period_end`.

**Agent roster, slash commands and maintenance skills** are defined under `.opencode/` and listed in AGENTS.md. Prefer the listed skills (`canary-validate`, `cluster-mutation`, `backfill-resume`, `sql-conventions`, `commit-and-changelog`, `adr`) over re-deriving their checklists.
