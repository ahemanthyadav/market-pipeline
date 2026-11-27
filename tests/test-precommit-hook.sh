#!/bin/bash
# Characterization test for .githooks/pre-commit — each BAD case must be blocked,
# each GOOD case must pass. Stages scratch files, runs the hook, always restores
# the index. No real secret is read or printed (fake values are built at run time;
# the hook's local-value check is exercised through HOOK_SECRETS_FILE).
#
# Usage: bash tests/test-precommit-hook.sh        (from anywhere inside the repo)
set -u
cd "$(git rev-parse --show-toplevel)"
HOOK=.githooks/pre-commit
SCRATCH=.hooktest-scratch
FAKEENV=$(mktemp)
pass=0; fail=0
cleanup() { git reset -q -- "$SCRATCH" 2>/dev/null; rm -rf "$SCRATCH" "$FAKEENV"; }
trap cleanup EXIT

expect() {  # expect <blocked|passes> <label> [env assignments via HOOK_*]
  local want="$1" label="$2"
  git add -f -- "$SCRATCH" 2>/dev/null
  "$HOOK" >/dev/null 2>&1; local rc=$?
  git reset -q -- "$SCRATCH" 2>/dev/null
  if { [ "$want" = blocked ] && [ $rc -ne 0 ]; } || { [ "$want" = passes ] && [ $rc -eq 0 ]; }; then
    echo "  ok    $label"; pass=$((pass+1))
  else
    echo "  FAIL  $label (wanted $want, rc=$rc)"; fail=$((fail+1))
  fi
  rm -rf "$SCRATCH"; mkdir -p "$SCRATCH"
}
mkdir -p "$SCRATCH"

echo "== must be BLOCKED"
touch "$SCRATCH/secret.yaml";                                   expect blocked "forbidden filename secret.yaml"
touch "$SCRATCH/id.pem";                                        expect blocked "forbidden filename *.pem"
echo "x" > "$SCRATCH/a.txt"; mkdir -p "$SCRATCH/__pycache__"; echo x > "$SCRATCH/__pycache__/m.pyc"; expect blocked "__pycache__ content"
echo "host 10.99.88.77 up" > "$SCRATCH/a.md";                   expect blocked "private IPv4 literal"
echo "peer 100.101.102.103" > "$SCRATCH/a.md";                  expect blocked "tailnet-range IPv4 literal"
REAL=$(grep -hE '^[A-Z0-9_]+=.+' .hosts.env 2>/dev/null | head -1 | cut -d= -f2 | cut -d/ -f1)
if [ -n "$REAL" ]; then echo "addr $REAL" > "$SCRATCH/a.md";   expect blocked "real address from local hosts file"
else echo "  skip  real address (no local hosts file)"; fi
echo "echo hunter2 | sudo -S ls" > "$SCRATCH/a.md";            expect blocked "literal password in sudo echo"
echo "SSH_PASS='x1y2z3' python3 ops-tools/runcmd.py a b c" > "$SCRATCH/a.md"; expect blocked "SSH_PASS on command line"
echo "tok $(printf '123456789:%s' "$(printf 'A%.0s' {1..35})")" > "$SCRATCH/a.md"; expect blocked "telegram-token shape"
echo 'chatid: "1234567890"' > "$SCRATCH/a.md";                 expect blocked "chat id literal"
echo "run '@@PW@@' tok $(printf '123456789:%s' "$(printf 'A%.0s' {1..35})")" > "$SCRATCH/a.md"; expect blocked "token on a line that also mentions @@PW@@ (old bypass)"
echo "postgres://user:hunter22pw@db/x" > "$SCRATCH/a.md";      expect blocked "URL with credentials"
echo "GRAFANA_ADMIN_PASSWORD=zq9-fake-local-secret-value" > "$FAKEENV"
echo "text zq9-fake-local-secret-value text" > "$SCRATCH/a.md"
HOOK_SECRETS_FILE="$FAKEENV" expect blocked "exact value from local credentials file"
if command -v gitleaks >/dev/null 2>&1; then
  RND=$(head -c 400 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 36)   # high entropy, or gitleaks rightly ignores it
  echo "t ghp_$RND" > "$SCRATCH/a.md";                            expect blocked "gitleaks: GitHub token shape"
else echo "  skip  gitleaks token shape (gitleaks not installed — the hook skips it too; CI installs it)"; fi

echo "== must PASS"
printf 'LLM_PROVIDER=fakevendorname\nGRAFANA_ADMIN_PASSWORD=zq9-fake-local-secret-value\n' > "$FAKEENV"
echo "the fakevendorname device plugin" > "$SCRATCH/a.md"
HOOK_SECRETS_FILE="$FAKEENV" expect passes "vendor identifier (LLM_PROVIDER) is not a secret"
echo "fakevendorname key zq9-fake-local-secret-value" > "$SCRATCH/a.md"
HOOK_SECRETS_FILE="$FAKEENV" expect blocked "a real secret on the same line is still blocked"
echo "# runbook: use placeholders like <pi2-ip> and the @@PW@@ token" > "$SCRATCH/a.md"; expect passes "placeholders + @@PW@@ token"
echo "cuDNN 8.2.1.32 and TensorRT 8.2.1.9, localhost 127.0.0.1" > "$SCRATCH/a.md";       expect passes "version numbers / loopback"
echo "ssh pi2@<pi2-ip>   # alias: runcmd.py pi2 pi2 'uptime'" > "$SCRATCH/a.md";        expect passes "aliases in commands"
echo "SSH_PASS=@@PW@@ python3 ops-tools/runcmd.py pi2 pi2 'uptime'" > "$SCRATCH/a.md";  expect passes "SSH_PASS with @@PW@@ placeholder"
echo "echo @@PW@@ | sudo -S systemctl restart k3s-agent" > "$SCRATCH/a.md";             expect passes "sudo echo with @@PW@@ placeholder"

echo; echo "result: $pass passed, $fail failed"
[ $fail -eq 0 ]
