#!/bin/bash
# nodewatch.sh — out-of-cluster liveness watcher (dead-man's switch).
#
# Why: the 2026-10-09 outage ran ~16 h unnoticed because every alert path (Grafana,
# Telegram bot, heartbeat) lives INSIDE the cluster it watches. This runs from a host
# OUTSIDE k3s (pi0 is ideal; any box with bash + ping works) and needs no kubectl.
#
# Checks each node by ping, the control-plane API port by TCP, and the age of the workstation
# backup-pull stamps (NODEWATCH_STAMP_DIR, default /var/lib/nodewatch-stamps), keeps a small state
# file, and sends ONE Telegram message on each change (down / recovered), plus a
# reminder every 6 h while anything is down. Messages use node names only — no IPs.
#
# Config (read, never printed):
#   hosts   : NODEWATCH_HOSTS_FILE (default: repo .hosts.env, else /etc/nodewatch.hosts)
#             keys PI0_IP PI1_IP PI2_IP PI3_IP TX2_IP K3S_CP_IP ORIN1_IP ORIN2_IP
#             (API port is checked on K3S_CP_IP:6443). Every key is required: a node with
#             no address is a loud exit 2, never a node that is silently not watched —
#             orin1/orin2 went unwatched from 2026-11-05 to 2026-11-19 that way.
#   Telegram: NODEWATCH_SECRETS_FILE (default: repo local credentials file, else
#             /etc/nodewatch.secrets, chmod 600) keys TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID
#             — without them it only prints (same as --dry-run).
#
# Usage:  bash ops-tools/nodewatch.sh [--dry-run]       cron (pi0): */5 * * * * /path/nodewatch.sh
# Test hooks: NODEWATCH_TEST_DOWN="pi3 api"  simulates failures; NODEWATCH_STATE_DIR overrides state;
#             NODEWATCH_TEST_ONLY=1  never probes the network — only NODEWATCH_TEST_DOWN counts (hermetic tests).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/.." && pwd)"
HOSTS="${NODEWATCH_HOSTS_FILE:-}"; [ -z "$HOSTS" ] && { [ -f "$REPO/.hosts.env" ] && HOSTS="$REPO/.hosts.env" || HOSTS=/etc/nodewatch.hosts; }
SECRETS="${NODEWATCH_SECRETS_FILE:-}"; [ -z "$SECRETS" ] && { [ -f "$REPO/.secrets.env" ] && SECRETS="$REPO/.secrets.env" || SECRETS=/etc/nodewatch.secrets; }
STATE="${NODEWATCH_STATE_DIR:-/var/tmp/nodewatch}"; mkdir -p "$STATE"
# Single instance: a slow run (ping timeouts) overlapping the next */5 cron fire
# must not interleave reads/writes of $STATE/last|sent (double-send / lost change).
if command -v flock >/dev/null 2>&1; then
  exec 9>"$STATE/.lock"
  flock -n 9 || { echo "nodewatch: another run holds the lock — exiting"; exit 0; }
fi
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1

get() { grep -m1 "^$2=" "$1" 2>/dev/null | cut -d= -f2- | tr -d "'\""; }   # get <file> <key>
declare -A IP=( [pi0]="$(get "$HOSTS" PI0_IP)" [pi1]="$(get "$HOSTS" PI1_IP)" [pi2]="$(get "$HOSTS" PI2_IP)" \
                [pi3]="$(get "$HOSTS" PI3_IP)" [tx2]="$(get "$HOSTS" TX2_IP)" \
                [k3s-cp]="$(get "$HOSTS" K3S_CP_IP)" \
                [orin1]="$(get "$HOSTS" ORIN1_IP)" [orin2]="$(get "$HOSTS" ORIN2_IP)" )
NODES="pi0 pi1 pi2 pi3 tx2 k3s-cp orin1 orin2"
for n in $NODES; do [ -n "${IP[$n]}" ] || { echo "nodewatch: no address for $n in hosts file" >&2; exit 2; }; done

is_down_test() { [[ " ${NODEWATCH_TEST_DOWN:-} " == *" $1 "* ]]; }
down=()
for n in $NODES; do
  if is_down_test "$n" || { [ "${NODEWATCH_TEST_ONLY:-0}" != 1 ] && ! ping -c2 -W2 "${IP[$n]}" >/dev/null 2>&1; }; then down+=("$n"); fi
done
if is_down_test api || { [ "${NODEWATCH_TEST_ONLY:-0}" != 1 ] && ! timeout 4 bash -c "</dev/tcp/${IP[k3s-cp]}/6443" 2>/dev/null; }; then down+=("k3s-api"); fi

# Backup dead-man's stamps: the workstation's pull scripts touch one file per job here on success
# (ops-tools/backup-stamp.sh). A stamp that is missing or older than 36 h means the pull has stopped —
# from 2026-11-12 to 2026-11-26 both pulls were dead and nothing said so. No stamp directory on this
# host = the check is off, and the status line says so.
STAMPS="${NODEWATCH_STAMP_DIR:-/var/lib/nodewatch-stamps}"; STAMP_MAX="${NODEWATCH_STAMP_MAX_AGE:-129600}"
stamps_note=""
if [ -d "$STAMPS" ]; then
  for s in pg-dumps k3s-backup; do
    m="$(stat -c %Y "$STAMPS/$s" 2>/dev/null || echo 0)"
    if is_down_test "backup-pull:$s" || [ $(( $(date +%s) - m )) -gt "$STAMP_MAX" ]; then down+=("backup-pull:$s"); fi
  done
else
  stamps_note=" (backup pulls not watched: no $STAMPS)"
fi

now="$(date +%s)"; cur="${down[*]:-}"; prev="$(cat "$STATE/last" 2>/dev/null || true)"; lastsent="$(cat "$STATE/sent" 2>/dev/null || echo 0)"
msg=""
if [ "$cur" != "$prev" ]; then
  if [ -n "$cur" ]; then msg="⚠️ nodewatch: DOWN → $cur"; else msg="✅ nodewatch: all nodes + k3s API reachable again, backup pulls fresh"; fi
elif [ -n "$cur" ] && [ $((now - lastsent)) -ge 21600 ]; then
  msg="⚠️ nodewatch (reminder, still down): $cur"
fi

echo "nodewatch $(date -u +%FT%TZ): down=[${cur:-none}]$stamps_note"
if [ -n "$msg" ]; then
  echo "message: $msg"
  TOKEN="$(get "$SECRETS" TELEGRAM_BOT_TOKEN)"; CHAT="$(get "$SECRETS" TELEGRAM_CHAT_ID)"
  handled=0   # 1 = delivered, or printed because no credentials exist (nothing to retry)
  if [ "$DRY" = 1 ]; then
    echo "(not sent: dry-run)"
  elif [ -z "$TOKEN" ] || [ -z "$CHAT" ]; then
    echo "(not sent: no Telegram credentials configured)"; handled=1
  # Token/chat id go to curl via --config on stdin, never argv — `ps` on this host
  # must not show them while the call runs (same class as AGENTS rule 10).
  elif printf 'url = "https://api.telegram.org/bot%s/sendMessage"\ndata-urlencode = "chat_id=%s"\ndata-urlencode = "text=%s"\n' \
         "$TOKEN" "$CHAT" "$msg" | curl -fsS -m 15 --config - >/dev/null; then
    echo "sent"; handled=1
  else
    echo "send FAILED — state left unchanged, will retry next run" >&2
  fi
  # Persist only when handled, so a failed send retries and a handled one is not repeated.
  if [ "$DRY" = 0 ] && [ "$handled" = 1 ]; then
    echo "$cur" > "$STATE/last"; echo "$now" > "$STATE/sent"
  fi
fi
exit 0
