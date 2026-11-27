#!/bin/bash
# backup-stamp.sh <name> — tell pi0's nodewatch that a workstation backup pull just succeeded.
#
# Why: from 2026-11-12 to 2026-11-26 both workstation pulls (pg dumps, k3s control-plane datastore)
# did not run at all and nothing noticed — a cron job that never starts cannot report its own
# staleness. The pull scripts call this on success; it touches /var/lib/nodewatch-stamps/<name>
# on pi0 (key-auth SSH, no password), and nodewatch.sh alerts when a stamp is older than 36 h.
#
# Names in use: pg-dumps (pull-backups.py) · k3s-backup (pull-k3s-backup.sh).
# Never fails the caller: a missed stamp is reported by nodewatch, not by a broken backup run.
# One-time setup on pi0:  sudo install -d -o pi0 -g pi0 -m 755 /var/lib/nodewatch-stamps
set -u
name="${1:-}"
case "$name" in pg-dumps|k3s-backup) ;; *) echo "usage: backup-stamp.sh pg-dumps|k3s-backup" >&2; exit 2 ;; esac
cd "$(dirname "$0")/.."
[ -f .hosts.env ] || { echo "WARN backup-stamp: no .hosts.env — stamp not written"; exit 0; }
PI0_IP="$(grep -m1 '^PI0_IP=' .hosts.env | cut -d= -f2- | tr -d "'\"")"
if [ -n "$PI0_IP" ] && ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new \
     "pi0@${PI0_IP}" "touch /var/lib/nodewatch-stamps/$name" 2>/dev/null; then
  echo "stamp $name written on pi0"
else
  echo "WARN backup-stamp: could not write stamp $name on pi0 (nodewatch will alert after 36 h)"
fi
exit 0
