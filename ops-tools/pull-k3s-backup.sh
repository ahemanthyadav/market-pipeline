#!/bin/bash
# pull-k3s-backup.sh — consistent copy of the k3s control-plane datastore (k3s-cp) to the workstation.
#
# k3s-cp runs kine on SQLite (/var/lib/rancher/k3s/server/db/state.db), so `k3s etcd-snapshot`
# does not apply. k3s-cp has no sqlite3 CLI; Python's sqlite3 online-backup API takes a consistent copy
# while k3s keeps running. The archive also holds the server `token`, server/tls (CA + keys) and
# server/cred — everything a rebuilt k3s-cp needs so agents keep trusting it. Written mode 600 under out/backups/k3s/ (gitignored) and the remote copy is deleted.
#
# k3s-cp is reached by key-auth SSH (no password), so this script uses plain `ssh`/`scp` rather than
# runcmd.py/getfile.py (those require a password from .secrets.env). K3S_CP_IP is read from the
# gitignored .hosts.env (not a tracked file).
#
# Usage:  bash ops-tools/pull-k3s-backup.sh [--keep 14]
# Exit:   0 ok · 3 checksum mismatch · 4 backup/integrity failed on k3s-cp
# Restore: ops manual §9 "Control-plane restore".
set -euo pipefail
cd "$(dirname "$0")/.."
KEEP=14
while [ $# -gt 0 ]; do
  case "$1" in
    --keep) KEEP="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Determine repo root: walk up from CWD to find .hosts.env (gitignored, local only).
# Robust whether invoked as `bash script.sh` or `bash < script.sh`.
REPO_DIR="$(pwd)"
for _ in 1 2 3 4 5; do
  [ -f "$REPO_DIR/.hosts.env" ] && break
  REPO_DIR="$(dirname "$REPO_DIR")"
  [ "$REPO_DIR" = "/" ] && break
done
[ -f "$REPO_DIR/.hosts.env" ] || { echo "cannot find .hosts.env (run from the repo root)" >&2; exit 1; }
cd "$REPO_DIR"
set -a && . .hosts.env && set +a
CP_HOST="minipc@${K3S_CP_IP}"
DEST=out/backups/k3s
REMOTE=/tmp/k3s-cp-backup.tar.gz
mkdir -p "$DEST"; chmod 700 "$DEST"
stamp() { date -u +%FT%TZ; }

# Run a multi-line script on k3s-cp as root via sudo + stdin heredoc (key-auth SSH, no password).
run_on_cp() {
  ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$CP_HOST" 'sudo bash -s' <<'REMOTE_EOF'
set -e
D=/tmp/k3s-cp-backup
rm -rf "$D"; mkdir -m 700 "$D"
python3 -c "
import sqlite3
src = sqlite3.connect("/var/lib/rancher/k3s/server/db/state.db")
dst = sqlite3.connect("/tmp/k3s-cp-backup/state.db")
src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
n = dst.execute("select count(*) from kine").fetchone()[0]
dst.close(); src.close()
print("INTEGRITY", ok, "ROWS", n)
"
cp /var/lib/rancher/k3s/server/token "$D"/token
cp -a /var/lib/rancher/k3s/server/tls /var/lib/rancher/k3s/server/cred "$D"/
tar -czf /tmp/k3s-cp-backup.tar.gz -C /tmp k3s-cp-backup
chown minipc:minipc /tmp/k3s-cp-backup.tar.gz
rm -rf "$D"
sha256sum /tmp/k3s-cp-backup.tar.gz | cut -d" " -f1
REMOTE_EOF
}

out=$(run_on_cp)
integrity=$(sed -n 's/^INTEGRITY \([^ ]*\) ROWS \([0-9]*\)$/\1 \2/p' <<<"$out")
want=$(echo "$out" | tail -1)
if [ "${integrity%% *}" != "ok" ] || [ -z "$want" ]; then
  echo "$(stamp) FAIL backup on k3s-cp (integrity='${integrity:-none}')"
  ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$CP_HOST" "rm -f $REMOTE" >/dev/null 2>&1 || true
  exit 4
fi

f="$DEST/k3s-cp-backup-$(date -u +%F).tar.gz"
scp -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$CP_HOST:$REMOTE" "$f.part" >/dev/null
ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$CP_HOST" "rm -f $REMOTE" >/dev/null 2>&1 || true
got=$(sha256sum "$f.part" | cut -d" " -f1)
if [ "$got" != "$want" ]; then
  rm -f "$f.part"
  echo "$(stamp) MISMATCH downloaded ${got:0:12} != remote ${want:0:12}"
  exit 3
fi
mv "$f.part" "$f"; chmod 600 "$f"
echo "$got  $(basename "$f")" > "$f.sha256"; chmod 600 "$f.sha256"

# In-cluster fast-path copy on pi3 (independent host, same LAN). Primary DR copy
# remains the workstation archive above; this is a convenience landing zone so a
# restore can start from the cluster without waiting on the workstation.
#
# 2026-11-15: lives in the pi3 user's HOME, never under /var/lib/market-data —
# that tree is the root-owned hostPath mounted into pg-backup and restore-drill
# (both run as root), so the token/CA keys would be readable from those pods.
# The earlier path also never worked: mkdir as the pi3 user failed on the
# root-owned dir and `|| true` hid it. Relative paths resolve against $HOME
# for both runcmd (ssh) and putfile (sftp).
PI3_BACKUP_DIR=k3s-cp-backups
pi3_copy() {
  local name; name=$(basename "$f")
  python3 ops-tools/runcmd.py pi3 pi3 "mkdir -p $PI3_BACKUP_DIR && chmod 700 $PI3_BACKUP_DIR" >/dev/null || return 1
  python3 ops-tools/putfile.py pi3 pi3 "$f" "$PI3_BACKUP_DIR/$name.part" >/dev/null || return 1
  local remote_sum
  remote_sum=$(python3 ops-tools/runcmd.py pi3 pi3 \
    "cd $PI3_BACKUP_DIR && chmod 600 $name.part && mv $name.part $name && sha256sum $name" \
    | sed -n 's/^\([0-9a-f]\{64\}\)  .*/\1/p')
  [ "$remote_sum" = "$got" ] || { echo "pi3 sha256 ${remote_sum:0:12} != ${got:0:12}" >&2; return 1; }
  python3 ops-tools/runcmd.py pi3 pi3 \
    "cd $PI3_BACKUP_DIR && echo '$got  $name' > $name.sha256 && chmod 600 $name.sha256" >/dev/null || return 1
  python3 ops-tools/runcmd.py pi3 pi3 \
    "cd $PI3_BACKUP_DIR && ls -1 k3s-cp-backup-????-??-??.tar.gz 2>/dev/null | sort -r | tail -n +8 | while read -r old; do rm -f \"\$old\" \"\$old.sha256\"; done" >/dev/null || true
}
if pi3_copy; then
  echo "$(stamp) K3S-BACKUP-PI3-OK $(basename "$f") -> pi3:~/$PI3_BACKUP_DIR (mode 600, sha256 verified)"
else
  echo "$(stamp) WARN: k3s control-plane backup could not be copied to pi3 (workstation copy complete)"
fi

# Rotation: newest $KEEP archives, only files this script names.
ls -1 "$DEST"/k3s-cp-backup-????-??-??.tar.gz 2>/dev/null | sort -r | tail -n +$((KEEP + 1)) | while read -r old; do
  rm -f "$old" "$old.sha256"; echo "$(stamp) pruned $(basename "$old")"
done
echo "$(stamp) K3S-BACKUP-OK $(basename "$f") rows=${integrity#* } sha256=${got:0:12}"
