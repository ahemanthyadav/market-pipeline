# Deployment Guide — how a change goes live

> **Purpose:** the start-to-finish path from "code edited" to "running on the
> cluster, validated, documented". This is the wrapper; the copy-paste depth
> lives in the ops manual (§6 image cycle, §7 health checklist, §8 recovery)
> and the skills (`cluster-mutation`, `canary-validate`, `commit-and-changelog`).
> **Last verified: 2026-10-30** (telegram-digest v34 rollout, end to end).

## 0. What "deploy" means here

No registry (ADR 0004). Images are built natively on the TX2 as
`local/<name>:vN`, archived as tarballs, imported into each node's containerd,
and referenced with `imagePullPolicy: Never`. **The image tag in the manifest
is the deploy mechanism**: a same-tag rebuild rolls nothing and is invisible
to the cluster. Every code change therefore means a tag bump — and a tag that
ever held wrong bits is burned (never rebuild it; bump again).

## 1. Decide what kind of change you have

| Change | What deploying takes |
|---|---|
| Fetcher / bot / job **code** | Tag bump + full image cycle (§3 below) + apply + canary |
| **Manifest-only** (schedule, env, resources) | Apply + verify live spec + canary — no image work |
| **Schema** | Edit `manifests/timescaledb/schema-configmap.yaml`, apply, run `schema-job.yaml` — never psql ad hoc |
| **Docs-only** | No cluster action; gates + push (§6) |
| Grafana dashboards | Provisioned ConfigMaps — apply + pod restart (ops manual §3b) |

## 2. Pre-flight (every deploy)

```bash
export KUBECONFIG=~/.kube/pi-cluster.yaml          # every new shell
python3 ops-tools/workloads.py --check             # docs tables agree with manifests
bash tests/run-all.sh                              # full suite, never piped through tail
python3 ops-tools/runcmd.py pi2 pi2 'uptime; free -m | head -2'   # pi2 load < 2 target, > 3 = stop (§8 cool-down)
```

One change at a time; know the rollback before you apply (`cluster-mutation`
skill). Nothing here ever prints a secret: `ops-tools/*.py` expand `@@PW@@`
from the gitignored `.secrets.env` only inside commands that consume it.

## 3. Image cycle (code changes) — condensed from ops manual §6

1. **Bump the tag everywhere** the image is referenced: the cronjob/deployment
   yamls **and** the doc tables (`workloads.py --check` catches stragglers).
   Telegram is three references: `cronjob.yaml` ×2 + `deployment.yaml`.
2. **Fresh build dir on the TX2**, ship exactly what the Dockerfile COPYs
   (`putfile.py`); a stale leftover file silently ships old code.
3. **Build** natively (`runcmd.py tx2 … docker build`). Warm cache ≈ seconds;
   a cold pip layer exceeds the 120 s channel timeout → `nohup` + poll
   (a TIMEOUT from runcmd does **not** mean the build died).
4. **Verify the image contents** — md5sum every COPY'd file in-image against
   the repo. This catches the build-before-commit race: v33 (2026-10-30) was
   built minutes before the last commit landed and carried a stale
   `stocks.py`/`bot.py`; the check burned the tag before it ever ran.
5. **Archive**: `docker save | gzip` straight into
   `/mnt/tx2-ssd/image-tarballs/`, regenerate `SHA256SUMS.all` (the restore
   source after the §8 prune incident — never skip it).
6. **Distribute only where the workload can schedule**: pinned images
   (telegram, marketview → pi1) go to that node only; TX2-tainted images skip
   distribution; unpinned CronJobs go to pi1/pi2/pi3. `getfile.py` →
   `putfile.py` → `k3s ctr -n k8s.io images import`. pi1/pi3 imports block
   1–2 min (fine); **pi2 imports run detached or they wedge the session**.
   Verify with `ctr images ls | grep <tag>` before applying anything.
7. **Clean up**: delete the home-dir tarball copies and the build dir on the
   TX2, and the tarball from each node after import (the archive copy stays).

## 4. Apply + validate (`canary-validate` skill)

```bash
kubectl apply -f manifests/<dir>/cronjob.yaml      # and deployment.yaml for always-on
kubectl -n market-data get cronjob <name> -o jsonpath='{.spec.jobTemplate.spec.template.spec.containers[0].image}{"\n"}'
```

- **A sed'd yaml is not a deploy** — jsonpath the live object before believing
  anything.
- CronJobs: `kubectl create job <name>-validate --from=cronjob/<name>`,
  poll-capture the logs (failed pods vanish in ~1 min), check the script's
  OK-marker, then **trust the database, not the exit code** (row counts /
  `pipeline_heartbeat`), then delete the job.
- Always-on (telegram-bot, marketview): the tag change rolls the pod —
  `rollout status`, then read the **new** pod's logs (`kubectl logs deploy/…`
  may still pick the terminating pod; name the pod).
- **Telegram caveat:** a digest/alerts canary fans real messages out to the
  owner + members off-schedule. Don't. Validation = in-image hash check +
  clean bot roll ("listening as @Marketfredbot …") + the owner exercising a
  changed `/command`; the next scheduled digest is the production proof.

## 5. Rollback

The previous image is still imported on the node (and archived on the TX2):
revert the tag in the manifest, `kubectl apply`, verify the live spec. For a
misbehaving CronJob mid-investigation: `kubectl -n market-data patch cronjob
<name> -p '{"spec":{"suspend":true}}'` — and remember to unsuspend (§7 checks
for leftovers).

## 6. Record + push (every deploy)

1. Docs in the same session: image-tag tables (ops manual §3, architecture),
   the feed/bot doc header, `pending-tasks.md` (✅ + date), new failure modes
   → §8. A change not written down did not happen.
2. Gates again (`workloads.py --check` + `tests/run-all.sh`), conventional
   commit (`commit-and-changelog` skill), push over SSH.

## Worked example — telegram-digest v34, 2026-10-30

v33 was built, archived and imported on pi1 but never applied; the in-image
hash check (§3.4) showed its `stocks.py`/`bot.py` predated commit 36e6529 →
tag burned. v34: bumped 3 manifests + 4 docs, gates green (36 CronJobs
consistent, all tests), fresh `tg-build` on TX2, build 8 s warm, all 8 in-image
hashes = HEAD, tarball #79 archived + SHA256SUMS regenerated, imported on pi1
only (22 s), pi2 pre-flight 2.48 (< 3, proceeded), applied cronjobs → verified
live tags, applied deployment → rolled in ~10 s, new pod log clean. No digest
canary (telegram caveat above); owner-side `/sp500` is the functional check.
