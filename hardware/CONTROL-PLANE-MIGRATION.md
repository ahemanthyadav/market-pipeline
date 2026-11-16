# Control-plane migration — pi2 → `k3s-cp` control plane on the N100 mini PC

> **Status:** decided 2026-11-15; **applied 2026-11-16 — on bare metal, not a VM.**
>
> **As built:** k3s server v1.36.4+k3s1 installed directly on the N100's existing Ubuntu 26.04 (hostname and
> Kubernetes node name **`minipc`**, SSH user `minipc`), alongside Coolify (Docker), using the whole host
> (4 cores / ~14 GiB RAM / 915 GB NVMe). No Proxmox, no VM. Skip §0 Proxmox precondition and §1.1 VM creation;
> Phase 1 below is the bare-metal install. The rest of the runbook (restore state.db + token + TLS, repoint
> agents, taint, retarget backup/nodewatch) was followed exactly and is preserved here for future
> restore/disaster-recovery reference.
>
> **Measured control-plane consumption (2026-11-16):** node `minipc` reports CPU 248m (6% of 4), memory
> 2056 MiB (13% of ~14.8 GiB); the `k3s server` process itself is ~700 MB RSS / ~15% CPU at idle. No marketdata
> workloads schedule here — node is tainted `CriticalAddonsOnly=true:NoExecute`.
>
> **Important cohabitation note:** k3s and Coolify/Docker share the N100 host and both program iptables. Never
> flush iptables rules on this host. Do not add this host as a server to any other Coolify (it would gain root
> on the host holding the k3s CA keys + join token). Coolify on this N100 is experiments-only; production SaaS
> runs on the pve2 Coolify. There is **no Proxmox snapshot/`vzdump` safety net** — `pull-k3s-backup.sh` is the
> only control-plane backup, and a N100 reboot takes the API down with it.
>
> Replace pi2 (1 GB, fragile, cordoned control plane, v1.36.4+k3s1) as the k3s server with k3s-cp on the
> N100 mini PC at home. The host is amd64 and is tainted `CriticalAddonsOnly=true:NoExecute` — load-bearing,
> because it must never run marketdata pods. Same LAN, no Tailscale in the cluster path. TX2 changes only
> `K3S_URL`.
>
> **I (the agent) have `kubectl` only** — read allowed, mutating (`apply`/`cordon`/`label`/`taint`/`patch`)
> requires your confirmation per `opencode.jsonc`. Every step marked **[HOST]** must be run by you on the physical
> machine (passwords live in the denied `.secrets.env`); every step marked **[KUBECTL]** I can run once you confirm.
>
> No IP addresses in this tracked file — node names only (pi0..pi3, tx2, k3s-cp); real addresses live in the
> gitignored `.hosts.env` (`PI2_IP`, `K3S_CP_IP`, …). Load for a shell: `set -a; source .hosts.env; set +a`.

---

## 0. Preconditions (verify before starting)

- [ ] Calm cluster window: no backfill or heavy job running; `pipeline_heartbeat` 16/16 `fresh`.
- [ ] pi2 load average < 2 (`uptime` on pi2) — AGENTS.md rule 2 / cluster-mutation pre-flight. If > 3
      sustained, follow ops manual §8 cool-down first; never restart more than one agent at a time.
- [ ] Fresh k3s control-plane backup exists: `bash ops-tools/pull-k3s-backup.sh` ran OK today
      (workstation `out/backups/k3s/k3s-cp-backup-<date>.tar.gz` + pi3 `~/k3s-cp-backups/`). **The restore
      has never been rehearsed** — do the restore drill on a spare SSD/SSD image before relying on it.
- [ ] N100 running Ubuntu 26.04, reachable on the LAN, SSH-able from the admin workstation. Coolify runs
      alongside on the same host (experiments only).
- [ ] You know the SSH user + password for pi2, minipc(k3s-cp), pi1, pi3, tx2, orin1, orin2 (out-of-band;
      never write them here).
- [ ] Rollback understood: if anything fails, stop, leave pi2 as the control plane, and re-apply the last good
      `pull-k3s-backup.sh` archive to pi2 per ops manual §9.

---

## 1. Phase — install k3s server on N100 bare metal (you, [HOST])

On the N100 host (`minipc`, Ubuntu 26.04):

1.1. Install k3s server v1.36.4+k3s1 (matches pi1/pi3 agents; keeps tx2's pinned v1.35.3 at N-1):
```
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=v1.36.4+k3s1 sh -s - server \
  --tls-san minipc \
  --tls-san <K3S_CP_IP> \
  --write-kubeconfig-mode 644 \
  --disable traefik
```
   `--disable traefik` avoids a port 80/443 conflict with Coolify (already bound on this host). k3s-cp is
   control-plane-only and tainted, so it doesn't need ingress; traefik runs on the workers as it does today.
   `--tls-san minipc` + `--tls-san <K3S_CP_IP>` so agents reach it by hostname or LAN IP. No Tailscale in the
   cluster path.

1.2. Confirm it's up:
```
sudo systemctl status k3s --no-pager
sudo kubectl get nodes   # uses /etc/rancher/k3s/k3s.yaml on the N100; agents show NotReady until repointed
```

1.3. Save the server token (compare to pi2's restored token — they must match so agents keep trusting the CA):
```
sudo cat /var/lib/rancher/k3s/server/token
```

### Version skew (decision already made)
- Server: v1.36.4+k3s1 · pi1/pi3 agents: v1.36.4 · tx2 agent: pinned v1.35.3+k3s1 (cgroup v1, TX2 fixed config)
  · orin1/orin2 agents: v1.37.2.
- k3s tolerates agents at most one minor older than the server. tx2 at v1.35.3 is N-1 (tolerated); orins at
  v1.37.2 are N+1 (generally tolerated). Do **not** upgrade the server to v1.37 until tx2's version question is
  settled (v1.37 server would put tx2 two minors behind → may not rejoin).

---

## 2. Phase — transfer cluster identity pi2 → `k3s-cp` (you, [HOST])

k3s runs kine on SQLite (`/var/lib/rancher/k3s/server/db/state.db`); `k3s etcd-snapshot` does not apply.
Copy pi2's datastore + server identity onto k3s-cp so agents keep trusting the CA and rejoin.

2.1. On pi2 (needs your SSH + sudo password):
```
sudo systemctl stop k3s
sudo mkdir -p /tmp/k3s-cp-backup
sudo python3 -c "
import sqlite3
src = sqlite3.connect('/var/lib/rancher/k3s/server/db/state.db')
dst = sqlite3.connect('/tmp/k3s-cp-backup/state.db')
src.backup(dst)
print(dst.execute('PRAGMA integrity_check').fetchone()[0])
dst.close(); src.close()
"
sudo cp /var/lib/rancher/k3s/server/token /tmp/k3s-cp-backup/token
sudo cp -a /var/lib/rancher/k3s/server/tls /tmp/k3s-cp-backup/tls
sudo cp -a /var/lib/rancher/k3s/server/cred /tmp/k3s-cp-backup/cred
sudo chown -R $USER /tmp/k3s-cp-backup; sudo chmod 600 /tmp/k3s-cp-backup/*
```
   Pull `/tmp/k3s-cp-backup/{state.db,token,tls,cred}` off pi2 to your workstation (scp/sftp).

2.2. On k3s-cp (`minipc`, k3s stopped while we overwrite its datastore):
```
sudo systemctl stop k3s
sudo cp /tmp/k3s-cp-backup/state.db /var/lib/rancher/k3s/server/db/state.db
sudo cp /tmp/k3s-cp-backup/token /var/lib/rancher/k3s/server/token
sudo rm -rf /var/lib/rancher/k3s/server/tls /var/lib/rancher/k3s/server/cred
sudo cp -a /tmp/k3s-cp-backup/tls /var/lib/rancher/k3s/server/tls
sudo cp -a /tmp/k3s-cp-backup/cred /var/lib/rancher/k3s/server/cred
sudo chown -R root:root /var/lib/rancher/k3s/server/tls /var/lib/rancher/k3s/server/cred
sudo chmod 700 /var/lib/rancher/k3s/server/tls /var/lib/rancher/k3s/server/cred
sudo chmod 600 /var/lib/rancher/k3s/server/token
sudo rm -f /var/lib/rancher/k3s/server/db/state.db-wal /var/lib/rancher/k3s/server/db/state.db-shm
sudo systemctl start k3s
```
   Remove leftover `state.db-wal`/`-shm` from k3s-cp's own (Phase 1) cluster — they conflict with the restored DB.

2.3. Verify k3s-cp is serving and lists the existing agents:
```
sudo kubectl get nodes
```
   Agents show `NotReady` until their `K3S_URL` is repointed in Phase 3.

> The control-plane datastore holds cluster config only (~8 MB + keys) — no market data. TimescaleDB
> stays on tx2 and is untouched by this migration.

---

## 3. Phase — repoint agents' `K3S_URL` one at a time (you, [HOST])

Each agent's `/etc/systemd/system/k3s-agent.service.env` (or service file) points `K3S_URL` at pi2:6443.
Change it to `https://<K3S_CP_IP>:6443` and restart `k3s-agent`, **one node at a time, verify Ready between nodes**:

- tx2 → pi3 → pi1 → orin1 → orin2  (proven agent-restart order; pi3 first)

Per node:
```
# on the agent host
sudo sed -i 's#K3S_URL=.*#K3S_URL="https://<K3S_CP_IP>:6443"#' /etc/systemd/system/k3s-agent.service.env
sudo systemctl daemon-reload
sudo systemctl restart k3s-agent
sleep 5
sudo systemctl is-active k3s-agent
# then, from the admin workstation:
kubectl get node <node>     # wait until Ready
```
- TX2's agent has fixed flags (`--disable-apiserver-lb`, cgroupfs, containerd fragment, iptables wrappers) —
  preserve them; only `K3S_URL` changes. Re-verify ops manual §2 fixed config after.
- k3s-agents that exhaust startup retries exit and stay `failed` (NRestarts=0) — after any long control-plane
  outage each agent needs a manual `systemctl start k3s-agent`. Do NOT reboot the whole cluster to "fix" them.
- pi2 had no separate k3s-agent service (it ran the server itself); after the move create
  `/etc/systemd/system/k3s-agent.service` + `.env` pointing at k3s-cp and enable/start it (see pending-tasks
  "pi2 post-transfer role").

---

## 4. Phase — taint `k3s-cp`, demote pi2 ([KUBECTL] — needs your confirmation)

Run once `k3s-cp` is serving and all agents are Ready against it:

```
kubectl taint node minipc CriticalAddonsOnly=true:NoExecute --overwrite
kubectl label node pi2 node-role.kubernetes.io/control-plane-
kubectl uncordon pi2
```

Then verify: `kubectl get nodes` — `minipc` should show `control-plane`, Ready, tainted; pi2 `Ready` (worker),
no longer `SchedulingDisabled`; all other nodes Ready.

---

## 5. Phase — post-migration ([KUBECTL] + you [HOST])

5.1. **Retarget `ops-tools/pull-k3s-backup.sh`** from `pi2` to `k3s-cp`; drop the pi2 load gate (the bare-metal
     N100 host isn't the fragile 1 GB Pi). The script now uses key-auth SSH (no password) to `minipc@<K3S_CP_IP>`
     since k3s-cp has no password-based sudo. The pi3 fast-path copy (`~/k3s-cp-backups/`) stays.
5.2. **Point pi0 nodewatch's API probe** at `k3s-cp:6443` instead of `pi2:6443` (`nodewatch.sh` checks
     `K3S_CP_IP:6443` now). pi0 is not a k8s node; this is its `ops-tools/nodewatch.sh` config on pi0 plus
     `/etc/nodewatch.hosts` entry `K3S_CP_IP=<K3S_CP_IP>`.
5.3. **Upgrade the server to v1.37** once tx2's version question is settled (ends the Orin v1.37 >
     server v1.36 skew).
5.4. **Relax AGENTS.md rule 2** (pi2 is no longer the 1 GB control plane; the cool-down rule now applies to
     k3s-cp instead).
5.5. **pi2 post-transfer role:** plain worker for CPU jobs (pending-tasks §1) — import `data` + `light`
     images, uncordoned.
5.6. **4th Pi worker** joins after this move (pending-tasks §1); **Orins GPU-only** and **tx2 workload
     separation (AR-10)** follow in their own sessions.

---

## 6. Verify (after every phase)

```
kubectl get nodes                      # all Ready; minipc = control-plane; pi2 = worker
kubectl get pods -A                    # no crash-looping; timescaledb-0, grafana, telegram-bot, marketview, llm-gateway up
kubectl -n market-data get cronjobs    # all 36, none suspended
kubectl top node minipc pi2            # control-plane host and pi2 healthy
# DB read-only sanity:
export KUBECONFIG=~/.kube/pi-cluster.yaml
PW=$(kubectl -n market-data get secret timescaledb-auth -o jsonpath='{.data.password}' | base64 -d)
kubectl -n market-data exec timescaledb-0 -- env PGPASSWORD="$PW" \
  psql -U postgres -d postgres -c "SELECT count(*) FROM index_prices;"
```

---

## 7. Rollback

If `k3s-cp` fails to serve or agents won't rejoin:

1. Leave `k3s-cp` as-is; do not delete pi2's `state.db` (it's still on pi2 until overwritten — Phase 2 only
   copies it *off* pi2; pi2's original is intact until you `cp` over it).
2. Restart pi2's k3s (`sudo systemctl start k3s` on pi2) — agents still point at pi2 until Phase 3 repoints them.
3. Re-apply the last `pull-k3s-backup.sh` archive to pi2 per ops manual §9 if pi2's datastore was disturbed.
4. Document what failed; retry in the next calm window.

---

## 8. Docs to update in the same session (AGENTS.md discipline)

- `docs/node-roles.md` — flip §1 live row (pi2 → worker), confirm §1b target (k3s-cp control plane).
- `AGENTS.md` — rule 2 now references k3s-cp (bare metal, node `minipc`).
- `ops-tools/pull-k3s-backup.sh` — retarget pi2 → k3s-cp (step 5.1).
- `ops-tools/nodewatch.sh` / pi0 config — API probe → k3s-cp (step 5.2).
- `docs/pending-tasks.md` — mark migration ✅ with date + this runbook.
- Re-verify: `python3 ops-tools/workloads.py --check` and `bash tests/run-all.sh` before any push
  (conventional commit, push over SSH; no credentials/IPs in tracked files).
