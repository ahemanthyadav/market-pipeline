#!/bin/bash
# Behavioural test for ops-tools/nodewatch.sh using its simulated-failure hook.
# Never sends a message (no credentials are passed) and uses a scratch state dir.
set -u
cd "$(git rev-parse --show-toplevel)"
export NODEWATCH_STATE_DIR="$(mktemp -d)"
export NODEWATCH_SECRETS_FILE=/dev/null          # force "no Telegram credentials" -> print only
# Hermetic: no real hosts file, no network probes — only NODEWATCH_TEST_DOWN decides what is "down".
# (Before 2026-10-12 this test pinged the live nodes, so it passed only while the cluster was up and
# failed outright in CI, where there is no .hosts.env.)
export NODEWATCH_HOSTS_FILE="$NODEWATCH_STATE_DIR/hosts.fake"
printf '%s=node-%s\n' PI0_IP a PI1_IP b PI2_IP c PI3_IP d TX2_IP e K3S_CP_IP f ORIN1_IP g ORIN2_IP h > "$NODEWATCH_HOSTS_FILE"
export NODEWATCH_TEST_ONLY=1
trap 'rm -rf "$NODEWATCH_STATE_DIR"' EXIT
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "  ok    $1"; pass=$((pass+1)); else echo "  FAIL  $1 (got '$2', want '$3')"; fail=$((fail+1)); fi; }
run() { bash ops-tools/nodewatch.sh "$@" 2>&1; }
has() { grep -c "$2" <<<"$1"; }

echo "== state-change logic (simulated outage)"
out=$(run);                                    check "healthy baseline: no message"            "$(has "$out" '^message:')" 0
out=$(NODEWATCH_TEST_DOWN="pi3 api" run);      check "outage -> one DOWN message"              "$(has "$out" 'DOWN → pi3 k3s-api')" 1
out=$(NODEWATCH_TEST_DOWN="pi3 api" run);      check "same outage again -> suppressed"         "$(has "$out" '^message:')" 0
out=$(NODEWATCH_TEST_DOWN="pi3" run);          check "api recovers, pi3 still down -> new msg" "$(has "$out" 'DOWN → pi3')" 1
out=$(run);                                    check "full recovery -> recovered message"      "$(has "$out" 'reachable again')" 1
out=$(run);                                    check "stable again -> silent"                  "$(has "$out" '^message:')" 0

echo "== every node is watched, and a missing address is loud"
out=$(NODEWATCH_TEST_DOWN="orin2" run);        check "orin2 down -> DOWN message"              "$(has "$out" 'DOWN → orin2')" 1
out=$(NODEWATCH_TEST_DOWN="orin1 orin2" run);  check "both orins down -> one message"          "$(has "$out" 'DOWN → orin1 orin2')" 1
out=$(run);                                    check "orins recover -> recovered message"      "$(has "$out" 'reachable again')" 1
grep -v '^ORIN2_IP=' "$NODEWATCH_HOSTS_FILE" > "$NODEWATCH_HOSTS_FILE.short"
out=$(NODEWATCH_HOSTS_FILE="$NODEWATCH_HOSTS_FILE.short" run); rc=$?
check "missing ORIN2_IP -> exit 2"             "$rc" 2
check "missing ORIN2_IP -> says which node"    "$(has "$out" 'no address for orin2')" 1

echo "== backup-pull stamps (dead-man's switch for the workstation pulls)"
export NODEWATCH_STAMP_DIR="$NODEWATCH_STATE_DIR/stamps"
out=$(run);                                    check "no stamp dir -> says pulls are not watched" "$(has "$out" 'backup pulls not watched')" 1
check "no stamp dir -> no message"             "$(has "$out" '^message:')" 0
mkdir -p "$NODEWATCH_STAMP_DIR"; touch "$NODEWATCH_STAMP_DIR/pg-dumps" "$NODEWATCH_STAMP_DIR/k3s-backup"
out=$(run);                                    check "fresh stamps -> silent"                  "$(has "$out" '^message:')" 0
touch -d '40 hours ago' "$NODEWATCH_STAMP_DIR/k3s-backup"
out=$(run);                                    check "stamp older than 36 h -> DOWN message"   "$(has "$out" 'DOWN → backup-pull:k3s-backup')" 1
out=$(run);                                    check "still stale -> suppressed"               "$(has "$out" '^message:')" 0
rm "$NODEWATCH_STAMP_DIR/pg-dumps"
out=$(run);                                    check "missing stamp counts as stale"           "$(has "$out" 'DOWN → backup-pull:pg-dumps backup-pull:k3s-backup')" 1
touch "$NODEWATCH_STAMP_DIR/pg-dumps" "$NODEWATCH_STAMP_DIR/k3s-backup"
out=$(run);                                    check "stamps fresh again -> recovered message" "$(has "$out" 'backup pulls fresh')" 1
out=$(bash ops-tools/backup-stamp.sh nonsense 2>&1); check "backup-stamp rejects unknown names" "$?" 2
unset NODEWATCH_STAMP_DIR

echo "== safety"
out=$(NODEWATCH_TEST_DOWN="tx2" run --dry-run); check "dry-run never claims it sent"          "$(has "$out" '^sent')" 0
out=$(NODEWATCH_TEST_DOWN="tx2" run);           check "no credentials -> prints, does not send" "$(has "$out" 'no Telegram credentials')" 1
leak=$(grep -nE "([0-9]{1,3}\.){3}[0-9]{1,3}" ops-tools/nodewatch.sh | wc -l)
check "script contains no IP literals"          "$leak" 0

echo; echo "result: $pass passed, $fail failed"; [ $fail -eq 0 ]
