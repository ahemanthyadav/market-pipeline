# Skill: image-recovery

Restore missing `local/*` container images to k3s worker nodes when `imagePullPolicy: Never`
causes `ErrImageNeverPull` failures across all fetchers.

## Trigger

Every fetcher pod shows `ErrImageNeverPull`; `pipeline_heartbeat.last_check` is frozen days old;
Grafana stale-feed rule fires; no data is landing.

## Prerequisites

- `KUBECONFIG=~/.kube/pi-cluster.yaml` exported
- `.secrets.env` present (PI_PASS, TX2_PASS) — read by `ops-tools/*.py` automatically
- `.ssh_known_hosts` in repo root (ssh-keyscan each node IP once)
- Image tarballs exist on TX2 at `/mnt/tx2-ssd/image-tarballs/` with `SHA256SUMS.all`

## Steps

### 1. Identify missing images per node

```bash
python3 ops-tools/runcmd.py pi1  pi1  "echo @@PW@@ | sudo -S /usr/local/bin/k3s ctr -n k8s.io images ls | grep -E 'marketdata|telegram'"
python3 ops-tools/runcmd.py pi3  pi3  "echo @@PW@@ | sudo -S /usr/local/bin/k3s ctr -n k8s.io images ls | grep -E 'marketdata|telegram'"
python3 ops-tools/runcmd.py tx2  ahytx2 "echo @@PW@@ | sudo -S /usr/local/bin/k3s ctr -n k8s.io images ls | grep -E 'marketdata|telegram'"
```
A node with NO marketdata/telegram lines is missing everything.

### 2. Pull tarballs from TX2

```bash
python3 ops-tools/getfile.py tx2 ahytx2 /home/ahytx2/marketdata-data-v1.tar.gz /tmp/marketdata-data-v1.tar.gz
python3 ops-tools/getfile.py tx2 ahytx2 /home/ahytx2/marketdata-light-v1.tar.gz /tmp/marketdata-light-v1.tar.gz
```

### 3. Distribute and import (ONE TAG AT A TIME — NEVER prune-all)

```bash
for h in pi1 pi3; do
  python3 ops-tools/putfile.py $h $h /tmp/marketdata-data-v1.tar.gz /home/$h/marketdata-data-v1.tar.gz
  python3 ops-tools/runcmd.py $h $h "echo @@PW@@ | sudo -S /usr/local/bin/k3s ctr -n k8s.io images import /home/$h/marketdata-data-v1.tar.gz 2>&1 | tail -1"
  python3 ops-tools/runcmd.py $h $h "echo @@PW@@ | sudo -S rm /home/$h/marketdata-data-v1.tar.gz"
done
```
- **pi2**: must run detached (`nohup ... &`) + poll — foreground import wedges the session.
- **orin1**: SKIP — tainted `gpu=orin:NoSchedule`, STT/TTS voice only.
- **orin2**: import only if uncordoned and will receive floating jobs; find SSH username first.

### 4. Verify imports

```bash
python3 ops-tools/runcmd.py pi1 pi1 "echo @@PW@@ | sudo -S /usr/local/bin/k3s ctr -n k8s.io images ls | grep marketdata-data:v1 | head -1"
python3 ops-tools/runcmd.py pi3 pi3 "echo @@PW@@ | sudo -S /usr/local/bin/k3s ctr -n k8s.io images ls | grep marketdata-data:v1 | head -1"
```

### 5. Validate with one-off jobs

```bash
kubectl create job -n market-data intraday-validate --from=cronjob/intraday-fetcher
kubectl wait --for=condition=complete job/intraday-validate -n market-data --timeout=120s
kubectl logs -n market-data job/intraday-validate --tail=10   # expect: fetched N/M symbols
kubectl delete job intraday-validate -n market-data
```

### 6. Confirm heartbeat advanced

```bash
PW=$(kubectl -n market-data get secret timescaledb-auth -o jsonpath='{.data.password}' | base64 -d)
kubectl -n market-data exec timescaledb-0 -- env PGPASSWORD="$PW" \
  psql -U postgres -d postgres -At -c "SELECT feed, last_check, fresh FROM pipeline_heartbeat ORDER BY last_check DESC LIMIT 5;"
```
Expect `last_check` within the last hour and `fresh=true` for active feeds.

### 7. Regenerate checksums on TX2 (rule 6)

```bash
python3 ops-tools/runcmd.py tx2 ahytx2 "echo @@PW@@ | sudo -S sh -c 'cd /mnt/tx2-ssd/image-tarballs && sha256sum *.tar.gz > SHA256SUMS.all'"
```

### 8. Uncordon orin2 if restored

```bash
kubectl uncordon orin2
```
Leave orin1 cordoned-by-taint.

## Never

- `crictl rmi --prune` / `ctr images prune` — deletes every CronJob image; `Never` pull cannot recover
- Remove tags with prune — one by one by name only
- Import market-data images to orin1 (tainted)

## Related

- ops manual §6 (image pipeline), §6b (emergency image recovery), §8 (`ErrImageNeverPull` runbook)
- pending-tasks §1 (orin2 image import item)