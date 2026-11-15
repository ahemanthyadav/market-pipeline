#!/bin/bash
# pull-k3s-backup.sh — consistent copy of the k3s control-plane datastore (pi2) to the workstation.
#
# k3s here runs kine on SQLite (/var/lib/rancher/k3s/server/db/state.db), so `k3s etcd-snapshot`
# does not apply. pi2 has no sqlite3 CLI; Python's sqlite3 online-backup API takes a consistent copy
# while k3s keeps running. The archive also holds the server token, server/tls (CA + keys) and
# server/cred — everything a rebuilt pi2 needs so agents keep trusting it. Written mode 600 under out/backups/k3s/ (gitignored) and the remote copy is deleted.
#
# Usage:  bash ops-tools/pull-k3s-backup.sh [--keep 14] [--max-load 3]
# Exit:   0 ok · 3 checksum mismatch · 4 backup/integrity failed on pi2 · 5 skipped (pi2 load too high)
# Restore: ops manual §9 "Control-plane restore".
set -euo pipefail
cd "$(dirname "$0")/.."
KEEP=14
MAX_LOAD=3
while [ $# -gt 0 ]; do
  case "$1" in
    --keep) KEEP="$2"; shift 2 ;;
    --max-load) MAX_LOAD="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
DEST=out/backups/k3s
REMOTE=/tmp/k3s-cp-backup.tar.gz
mkdir -p "$DEST"; chmod 700 "$DEST"
stamp() { date -u +%FT%TZ; }

# AGENTS rule 2: never add load to a busy control plane.
load=$(python3 ops-tools/runcmd.py pi2 pi2 'cut -d" " -f1 /proc/loadavg' | grep -E '^[0-9.]+$' | tail -1)
if [ -z "$load" ] || awk -v l="$load" -v m="$MAX_LOAD" 'BEGIN{exit !(l > m)}'; then
  echo "$(stamp) SKIP pi2 load=${load:-unknown} > $MAX_LOAD"
  exit 5
fi

out=$(python3 ops-tools/runcmd.py pi2 pi2 'set -e; D=/tmp/k3s-cp-backup; rm -rf $D; mkdir -m 700 $D
echo @@PW@@ | sudo -S -p "" python3 -c "
import sqlite3
src = sqlite3.connect(\"/var/lib/rancher/k3s/server/db/state.db\")
dst = sqlite3.connect(\"/tmp/k3s-cp-backup/state.db\")
src.backup(dst)
ok = dst.execute(\"PRAGMA integrity_check\").fetchone()[0]
n = dst.execute(\"select count(*) from kine\").fetchone()[0]
dst.close(); src.close()
print(\"INTEGRITY\", ok, \"ROWS\", n)
"
echo @@PW@@ | sudo -S -p "" cp /var/lib/rancher/k3s/server/token $D/token
echo @@PW@@ | sudo -S -p "" cp -a /var/lib/rancher/k3s/server/tls /var/lib/rancher/k3s/server/cred $D/
echo @@PW@@ | sudo -S -p "" tar -czf '"$REMOTE"' -C /tmp k3s-cp-backup
echo @@PW@@ | sudo -S -p "" chown "$(id -un)" '"$REMOTE"'
chmod 600 '"$REMOTE"'
echo @@PW@@ | sudo -S -p "" rm -rf $D
echo SHA $(sha256sum '"$REMOTE"' | cut -d" " -f1)')
integrity=$(sed -n 's/^INTEGRITY \([^ ]*\) ROWS \([0-9]*\)$/\1 \2/p' <<<"$out")
want=$(sed -n 's/^SHA \([0-9a-f]\{64\}\)$/\1/p' <<<"$out")
if [ "${integrity%% *}" != "ok" ] || [ -z "$want" ]; then
  echo "$(stamp) FAIL backup on pi2 (integrity='${integrity:-none}')"
  python3 ops-tools/runcmd.py pi2 pi2 "rm -f $REMOTE" >/dev/null || true
  exit 4
fi

f="$DEST/k3s-cp-backup-$(date -u +%F).tar.gz"
python3 ops-tools/getfile.py pi2 pi2 "$REMOTE" "$f.part" >/dev/null
python3 ops-tools/runcmd.py pi2 pi2 "rm -f $REMOTE" >/dev/null
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
  # sftp.put sets no mode: tighten BEFORE the final name exists, then verify there.
  local remote_sum
  remote_sum=$(python3 ops-tools/runcmd.py pi3 pi3 \
    "cd $PI3_BACKUP_DIR && chmod 600 $name.part && mv $name.part $name && sha256sum $name" \
    | sed -n 's/^\([0-9a-f]\{64\}\)  .*/\1/p')
  [ "$remote_sum" = "$got" ] || { echo "pi3 sha256 ${remote_sum:0:12} != ${got:0:12}" >&2; return 1; }
  python3 ops-tools/runcmd.py pi3 pi3 \
    "cd $PI3_BACKUP_DIR && echo '$got  $name' > $name.sha256 && chmod 600 $name.sha256" >/dev/null || return 1
  # Rotation on pi3: keep newest 7 archives, only files this script names.
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
