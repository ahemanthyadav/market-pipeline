#!/bin/bash
# collect-node-stats.sh — pull every node's node-local stats CSV (ADR 0011)
# into out/node-stats/ for review / DuckDB analysis.
#
# Usage:
#   bash ops-tools/collect-node-stats.sh
#
# Output: out/node-stats/<host>.csv (pi0 pi1 pi2 pi3 tx2 orin1 orin2)
#
# Passwords: ops-tools/getfile.py reads PI_PASS / TX2_PASS from the gitignored
# `.secrets.env` (host-aware, via _creds.py) — nothing is read from docs, printed
# or stored here.
#
# Quick looks after pulling:
#   for f in out/node-stats/*.csv; do tail -n 2 "$f"; done                # freshest sample per node
#   grep -c ',0x0,' out/node-stats/pi2.csv       # clean-throttle sample count
# throttle_hex decodes (Pi): bit0 uv-now, bit1 cap-now, bit2 throttle-now,
#   bit16/17/18/19 = uv/cap/throttle/softtemp occurred since boot. 0x0 = clean.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$REPO/out/node-stats"
mkdir -p "$OUT"
cd "$REPO"

# Host aliases (pi0..pi3, tx2) resolve to real IPs via the gitignored .hosts.env.
for h in pi0 pi1 pi2 pi3; do
  python3 ops-tools/getfile.py "$h" "$h" \
    /var/log/node-stats/stats.csv "$OUT/$h.csv"
done

python3 ops-tools/getfile.py tx2 ahytx2 \
  /var/log/node-stats/stats.csv "$OUT/tx2.csv"

# orin1/orin2 (logger installed 2026-11-19): key auth through the workstation's ssh
# aliases — they have no password slot in _creds.py, so getfile.py cannot reach them.
for h in orin1 orin2; do
  scp -q -o BatchMode=yes -o ConnectTimeout=8 "$h:/var/log/node-stats/stats.csv" "$OUT/$h.csv" \
    || echo "WARN $h: stats.csv not collected (ssh alias '$h' unreachable?)" >&2
done

echo "--- freshest sample per node:"
for f in "$OUT"/*.csv; do tail -n 1 "$f"; done
