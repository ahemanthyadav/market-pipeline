"""Pull the newest verified pg-backup dumps from pi3 to a second location, checksummed, and check freshness.

Usage:
    python3 ops-tools/pull-backups.py [--dest out/backups] [--keep 2] [--max-age-hours 36] [--prune-local 7]

A "verified" dump is one with a `.sha256` sidecar written by the (AR-11) pg-backup job, which only publishes a
dump after `pg_restore -l` and size checks. For each of the newest --keep dumps: skip if the local copy already
matches the sidecar hash, otherwise download to a temp name, verify the sha256, then rename. Deletes nothing unless --prune-local N is given (then only dumps dated
>= --prune-from beyond the newest N, and only when their sidecar verifies).

Exit codes: 0 ok · 2 newest verified dump older than --max-age-hours (staleness alert) · 3 checksum mismatch ·
4 no verified dump found. Password comes from the gitignored .secrets.env via _creds.py (never on a command line).
Cron-able from the workstation or pi0 — the sidecar hash is the integrity check, the exit code is the alert.
"""
import argparse
import hashlib
import os
import sys
import time

import paramiko

from _creds import host_key_policy, password_for, resolve_host

HOST, USER, REMOTE_DIR = "pi3", "pi3", "/var/lib/market-data/backups"


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def prune_local(dest: str, keep: int, prune_from: str) -> None:
    """Delete local marketdata-YYYY-MM-DD.dump (+ sidecar) beyond the newest `keep`, considering only
    dumps dated >= prune_from and only ones whose sidecar still verifies (never a lone or unverified copy)."""
    verified = []
    for n in os.listdir(dest):
        if not (n.startswith("marketdata-") and n.endswith(".dump")):
            continue
        day = n[len("marketdata-"):-len(".dump")]
        if len(day) != 10 or day < prune_from:
            continue
        path, side = os.path.join(dest, n), os.path.join(dest, n + ".sha256")
        try:
            with open(side) as f:
                ok = f.read().split()[0] == sha256_of(path)
        except (OSError, IndexError):
            ok = False
        if ok:
            verified.append((day, n))
        else:  # unverifiable copies are never deleted and never count toward `keep`
            print(f"keep  {n}  (sidecar missing or mismatched — not pruning)")
    for _, n in sorted(verified, reverse=True)[keep:]:
        os.remove(os.path.join(dest, n))
        os.remove(os.path.join(dest, n + ".sha256"))
        print(f"pruned {n}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default="out/backups")
    ap.add_argument("--keep", type=int, default=2)
    ap.add_argument("--max-age-hours", type=float, default=36)
    ap.add_argument("--prune-local", type=int, default=0, metavar="N",
                    help="after pulling, keep only the newest N local dumps dated >= --prune-from (0 = never delete)")
    ap.add_argument("--prune-from", default="2026-10-18", metavar="YYYY-MM-DD",
                    help="dumps dated before this are never pruned (keeps hand-kept copies, e.g. pre-migration dumps)")
    a = ap.parse_args()
    os.makedirs(a.dest, exist_ok=True)

    client = paramiko.SSHClient()
    host_key_policy(client)   # pins via .ssh_known_hosts when present
    client.connect(resolve_host(HOST), username=USER, password=password_for(HOST, USER), timeout=15)
    sftp = client.open_sftp()
    attrs = {e.filename: e for e in sftp.listdir_attr(REMOTE_DIR)}
    verified = sorted((n for n in attrs if n.startswith("marketdata-") and n.endswith(".dump")
                       and n + ".sha256" in attrs and attrs[n].st_size > 0),
                      key=lambda n: attrs[n].st_mtime, reverse=True)
    if not verified:
        print("NO-VERIFIED-DUMP on pi3")
        return 4
    rc = 0
    newest_age_h = (time.time() - attrs[verified[0]].st_mtime) / 3600
    for name in verified[:a.keep]:
        with sftp.open(f"{REMOTE_DIR}/{name}.sha256") as f:
            want = f.read().decode().split()[0]
        local = os.path.join(a.dest, name)
        if os.path.exists(local) and sha256_of(local) == want:
            print(f"have  {name}  sha256 ok")
            continue
        tmp = local + ".part"
        sftp.get(f"{REMOTE_DIR}/{name}", tmp)
        got = sha256_of(tmp)
        if got != want:
            os.remove(tmp)
            print(f"MISMATCH {name}: downloaded {got[:12]} != sidecar {want[:12]} (removed)")
            rc = 3
            continue
        os.replace(tmp, local)
        with open(local + ".sha256", "w") as f:
            f.write(f"{want}  {name}\n")
        print(f"pulled {name}  {os.path.getsize(local) / 1e6:.0f} MB  sha256 ok")
    sftp.close()
    client.close()
    if a.prune_local > 0 and rc == 0:
        prune_local(a.dest, a.prune_local, a.prune_from)
    print(f"newest verified dump: {verified[0]} ({newest_age_h:.1f} h old, limit {a.max_age_hours:g} h)")
    if newest_age_h > a.max_age_hours:
        print("STALE: newest verified dump is older than the limit")
        return rc or 2
    if rc == 0:   # dead-man's stamp for pi0's nodewatch — only a clean, fresh pull counts
        import subprocess
        subprocess.run(["bash", os.path.join(os.path.dirname(os.path.abspath(__file__)), "backup-stamp.sh"),
                        "pg-dumps"], check=False)
    return rc


if __name__ == "__main__":
    sys.exit(main())
