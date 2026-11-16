# Control-plane migration — pi2 → `k3s-cp` VM on the N100 mini PC

> **Status:** decided 2026-11-15 (pending-tasks §1, node-roles.md §1b target). **Not applied.**
> **Runbook:** this file (replaces the 2026-11-12 pve2 plan).
>
> Replace pi2 (1 GB, fragile, cordoned control plane, v1.36.4+k3s1) as the k3s server with a
> 2 vCPU / 4 GB VM (`k3s-cp`) on the N100 mini PC at home running Proxmox. The VM is amd64 and is
> tainted `CriticalAddonsOnly=true:NoExecute` — load-bearing, because it must never run marketdata
> pods. Same LAN, no Tailscale in the cluster path. TX2 changes only `K3S_URL`.
>
> **I (the agent) have `kubectl` only — read allowed, mutating (`apply`/`cordon`/`label`/`taint`/`patch`)
> requires your confirmation per `opencode.jsonc`. I have no shell access to the nodes (passwords live in
> the denied `.secrets.env`), so every step marked **[HOST]** must be run by you on the physical machine;
> every step marked **[KUBECTL]** I can run once you confirm.
>
> No IP addresses in this tracked file — node names only (pi0..pi3, tx2, k3s-cp); real addresses live in
> the gitignored `.hosts.env` (`PI2_IP`, `K3S_CP_IP`, …). Load for a shell: `set -a; source .hosts.env; set +a`.

---

## 0. Preconditions (verify before starting)

- [ ] Calm cluster window: no backfill or heavy job running; `pipeline_heartbeat` 16/16 `fresh`.
- [ ] pi2 load average < 2 (`uptime` on pi2) — AGENTS.md rule 2 / cluster-mutation pre-flight. If > 3
      sustained, follow ops manual §8 cool-down first; never restart more than one agent at a time.
- [ ] Fresh k3s control-plane backup exists: `bash ops-tools/pull-k3s-backup.sh` ran OK today
      (workstation `out/backups/k3s/k3s-cp-backup-<date>.tar.gz` + pi3 `~/k3s-cp-backups/`). **The restore
      has never been rehearsed** — do the restore drill on a spare SSD/SSD image before the move.
- [ ] N100 running Proxmox; a `k3s-cp` VM exists (2 vCPU / 4 GB, amd64), reachable on the LAN, SSH-able
      from the admin workstation. Coolify (if you run it) moved into a second VM — `k3s-cp` is control-plane
      only.
- [ ] You know the SSH user + password for pi2, k3s-cp, pi1, pi3, tx2, orin1, orin2 (out-of-band; never
      write them here). I cannot run `python3 ops-tools/runcmd.py` — `.secrets.env` is denied to me.
- [ ] Rollback understood: if anything fails, stop, leave pi2 as the control plane, and re-apply the last
      good `pull-k3s-backup.sh` archive to pi2 per ops manual §9.

---

## 1. Phase — build `k3s-cp` (you, [HOST])

On the N100 / Proxmox:

1.1. Create the `k3s-cp` VM (2 vCPU / 4 GB RAM, amd64, Ubuntu/Debian), on the same LAN as the cluster.
1.2. Install k3s server on it:
```
curl -sfL https://get.k3s.io | sh -s - server \
  --tls-san <k3s-cp-hostname> \
  --cluster-init   # only if this is a brand-new single-server cluster
```
   Pick a k3s server version within one minor of every agent (see **version skew** below).
1.3. Note the join token: `/var/lib/rancher/k3s/server/token` on k3s-cp (you'll compare it against pi2's
     restored token — they must match so existing agents keep trusting the CA).
1.4. Stop k3s on k3s-cp (`sudo systemctl stop k3s`) until you restore pi2's datastore in Phase 2.

### Version skew (decision needed)
- pi1/pi3 agents: v1.36.4 · tx2 agent: **pinned v1.35.3+k3s1** (cgroup v1 ceiling, TX2 fixed config —
  never apt-upgrade per AGENTS.md rule 3 / ops manual §2) · orin1/orin2 agents: v1.37.2.
- k3s tolerates agents at most one minor older than the server. A v1.37 server leaves tx2 (v1.35.3)
  two minors behind → tx2 agent may not rejoin. Decide: (a) keep server at v1.36.x so tx2 stays N-1
  (orins v1.37.2 are N+1, generally tolerated), or (b) upgrade tx2 too (breaks its pinned cgroup-v1
  config — needs ops manual §2 re-verification). pending-tasks wants server v1.37 "to end the Orin >
  server skew" — resolve tx2 first.

---

## 2. Phase — transfer cluster identity pi2 → `k3s-cp` (you, [HOST])

k3s runs kine on SQLite (`/var/lib/rancher/k3s/server/db/state.db`); `k3s etcd-snapshot` does not apply.
Copy pi2's datastore + server identity onto k3s-cp so agents keep trusting the CA and rejoin.

2.1. On pi2 (needs your SSH + sudo password):
```
sudo systemctl stop k3s
sudo sqlite3 /var/lib/rancher/k3s/server/db/state.db ".backup /tmp/state.db"   # pi2 may lack sqlite3 CLI;
                                                                                 # fall back to Python online-backup API
sudo cp /var/lib/rancher/k3s/server/token /tmp/token
sudo cp -a /var/lib/rancher/k3s/server/tls /tmp/tls
sudo cp -a /var/lib/rancher/k3s/server/cred /tmp/cred
sudo chown $USER /tmp/state.db /tmp/token; sudo chmod 600 /tmp/state.db /tmp/token
```
   Pull `/tmp/{state.db,token,tls,cred}` off pi2 to your workstation (scp/sftp).
2.2. On k3s-cp (k3s still stopped):
```
sudo systemctl stop k3s
sudo cp /tmp/state.db /var/lib/rancher/k3s/server/db/state.db
sudo cp /tmp/token /var/lib/rancher/k3s/server/token
sudo cp -a /tmp/tls /var/lib/rancher/k3s/server/tls
sudo cp -a /tmp/cred /var/lib/rancher/k3s/server/cred
sudo systemctl start k3s
```
2.3. Verify k3s-cp is serving: `kubectl --server https://<k3s-cp>:6443 --tokenfile /var/lib/rancher/k3s/server/token get nodes`
     should list the existing agents (they will reconnect once their K3S_URL is repointed in Phase 3).

> The control-plane datastore holds cluster config only (~8 MB + keys) — no market data. TimescaleDB
> stays on tx2 and is untouched by this migration.

---

## 3. Phase — repoint agents' `K3S_URL` one at a time (you, [HOST])

Each agent's `/etc/systemd/system/k3s-agent.service` (or env file) points `K3S_URL` at pi2:6443.
Change it to `k3s-cp:6443` and restart `k3s-agent`, **one node at a time, verify Ready between nodes**:

- tx2 → pi3 → pi1 → orin1 → orin2  (proven agent-restart order; pi3 first)

Per node:
```
# on the agent host
sudo sed -i 's#K3S_URL=.*#K3S_URL=https://k3s-cp:6443#' /etc/systemd/system/k3s-agent.service  # or wherever K3S_URL lives
sudo systemctl daemon-reload
sudo systemctl restart k3s-agent
# then, from the admin workstation:
kubectl get node <node>     # wait until Ready
```
- TX2's agent has fixed flags (`--disable-apiserver-lb`, cgroupfs, containerd fragment, iptables wrappers)
  — preserve them; only `K3S_URL` changes. Re-verify ops manual §2 fixed config after.
- k3s-agents that exhaust startup retries exit and stay `failed` (NRestarts=0) — after any long control-plane
  outage each agent needs a manual `systemctl start k3s-agent`. Do NOT reboot the whole cluster to "fix" them.

---

## 4. Phase — taint `k3s-cp`, demote pi2 ([KUBECTL] — needs your confirmation)

Run once `k3s-cp` is serving and all agents are Ready against it:

```
kubectl taint node k3s-cp CriticalAddonsOnly=true:NoExecute
kubectl label node k3s-cp node-role.kubernetes.io/control-plane=    # or however you mark it
kubectl uncordon pi2            # pi2 becomes a plain worker for CPU jobs (pending-tasks: "pi2 post-transfer role")
kubectl label node pi2 <worker labels>   # per node-roles.md §1b target layout
```

Then verify: `kubectl get nodes` — k3s-cp should show `control-plane`, Ready, tainted; pi2 `Ready` (worker),
no longer `SchedulingDisabled`; all other nodes Ready.

---

## 5. Phase — post-migration ([KUBECTL] + you [HOST])

5.1. **Retarget `ops-tools/pull-k3s-backup.sh`** from `pi2` to `k3s-cp`; drop the pi2 load gate (the VM isn't
     the fragile 1 GB Pi). Change `REMOTE`/runcmd target and the `pi2` host alias in the script (keep
     node names, not IPs). The pi3 fast-path copy (`~/k3s-cp-backups/`) stays.
5.2. **Point pi0 nodewatch's API probe** at `k3s-cp:6443` instead of `pi2:6443` (`nodewatch.sh` checks
     `PI2_IP:6443` today — edit the watch list to probe `k3s-cp`). pi0 is not a k8s node; this is its
     `ops-tools/nodewatch.sh` config on pi0.
5.3. **Upgrade the server to v1.37** once tx2's version question is settled (ends the Orin v1.37 >
     server v1.36 skew).
5.4. **Relax AGENTS.md rule 2** (pi2 is no longer the 1 GB control plane; "never restart more than one
     agent at a time / load > 3 cool-down" applies to k3s-cp instead).
5.5. **pi2 post-transfer role:** plain worker for CPU jobs (pending-tasks §1) — import `data` + `light`
     images, uncordoned.
5.6. **4th Pi worker** joins after this move (pending-tasks §1); **Orins GPU-only** and **tx2 workload
     separation (AR-10)** follow in their own sessions.

---

## 6. Verify (after every phase)

```
kubectl get nodes                      # all Ready; k3s-cp = control-plane; pi2 = worker
kubectl get pods -A                    # no crash-looping; timescaledb-0, grafana, telegram-bot, marketview, llm-gateway up
kubectl -n market-data get cronjobs    # all 36, none suspended
kubectl top node k3s-cp pi2            # VM and pi2 healthy
# DB read-only sanity:
export KUBECONFIG=~/.kube/pi-cluster.yaml
PW=$(kubectl -n market-data get secret timescaledb-auth -o jsonpath='{.data.password}' | base64 -d)
kubectl -n market-data exec timescaledb-0 -- env PGPASSWORD="$PW" \
  psql -U postgres -d postgres -c "SELECT count(*) FROM index_prices;"
```

---

## 7. Rollback

If `k3s-cp` fails to serve or agents won't rejoin:
1. Leave `k3s-cp` as-is; do not delete pi2's `state.db` (it's still on pi2 until you overwrite it — and Phase 2
   only copies it *off* pi2; pi2's original is intact until you `cp` over it).
2. Restart pi2's k3s (`sudo systemctl start k3s` on pi2) — agents still point at pi2 until Phase 3 repoints them.
3. Re-apply the last `pull-k3s-backup.sh` archive to pi2 per ops manual §9 if pi2's datastore was disturbed.
4. Document what failed; retry in the next calm window.

---

## 8. Docs to update in the same session (AGENTS.md discipline)

- `docs/node-roles.md` — flip §1 live row (pi2 → worker), confirm §1b target (k3s-cp control plane).
- `AGENTS.md` — rule 2 now references k3s-cp; project note moves from "decided, not applied" to applied.
- `ops-tools/pull-k3s-backup.sh` — retarget pi2 → k3s-cp (step 5.1).
- `ops-tools/nodewatch.sh` / pi0 config — API probe → k3s-cp (step 5.2).
- `docs/pending-tasks.md` — mark "Control plane migration → k3s-cp VM on the N100" ✅ with date + this runbook.
- Re-verify: `python3 ops-tools/workloads.py --check` and `bash tests/run-all.sh` before any push
  (conventional commit, push over SSH; no credentials/IPs in tracked files).

---

## Open questions for you before we start

1. Is the N100 + Proxmox + `k3s-cp` VM already provisioned, or do you need me to list the exact VM specs?
2. Which k3s server version do you want on `k3s-cp` given tx2 is pinned to v1.35.3 (see version skew above)?
3. Confirm: proceed with Phase 4 (`kubectl taint`/`uncordon`/label) once `k3s-cp` is serving?
