#!/usr/bin/env python3
"""ML-7c story summaries — LLM summaries for news story clusters.

Groups headlines by story_id (from headline_embeddings), generates an LLM
summary per story via the shared gateway, writes to app.llm_outputs
(kind='story_summary').

Usage (workstation; DB via $PSQL_CMD, LLM via gateway env vars):
  PSQL_CMD='kubectl -n market-data exec -i timescaledb-0 -- env PGPASSWORD=… psql -U postgres -d postgres' \\
    LLM_BASE_URL=http://localhost:30400 LLM_API_KEY=… LLM_CAPABILITY=general \\
    python ops-tools/ml7c.py run --limit 50
  PSQL_CMD='…' python ops-tools/ml7c.py show --limit 20
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "manifests" / "telegram"))
from factcheck import problems as factcheck_problems  # noqa: E402

OUT = "out/ml7c"


# ---------------------------------------------------------------- DB helpers

def psql(sql, text=True):
    cmd = os.environ.get("PSQL_CMD")
    if not cmd:
        sys.exit("set PSQL_CMD (see module docstring)")
    out = subprocess.run(cmd + " -v ON_ERROR_STOP=1 -At -F, -c " + _q(sql), shell=True,
                         check=True, capture_output=True, text=text)
    return out.stdout


def _q(s):
    return "'" + s.replace("'", "'\"'\"'") + "'"


def write_summary(story_id, headline_count, summary, facts_hash, model, verified=False):
    """Insert story summary into app.llm_outputs (idempotent)."""
    scope = f"story_{story_id}"
    ts = datetime.now(timezone.utc).isoformat()
    kind = _q("story_summary")
    scope_q = _q(scope)
    ts_q = _q(ts)
    text_q = _q(summary)
    fh_q = _q(facts_hash)
    ver_sql = "TRUE" if verified else "FALSE"
    sql = (f"INSERT INTO app.llm_outputs (kind, scope, ts, text, facts_hash, verified) "
           f"VALUES ({kind}, {scope_q}, {ts_q}, {text_q}, {fh_q}, {ver_sql}) "
           f"ON CONFLICT (kind, scope, ts) DO NOTHING")
    raw = psql(sql).strip()
    return raw == "INSERT 0 1"


# ---------------------------------------------------------------- fetch stories

def fetch_stories(limit=50):
    """Return list of (story_id, [headlines]) for stories with multiple headlines."""
    sql = (f"SELECT e.story_id, h.headline FROM headline_embeddings e "
           f"JOIN raw_headlines h ON h.id = e.headline_id "
           f"WHERE e.story_id IS NOT NULL "
           f"ORDER BY e.story_id, h.ts DESC LIMIT {limit * 10}")
    rows = psql(sql)
    if not rows.strip():
        return []
    stories = {}
    for line in rows.strip().split("\n"):
        parts = line.split(",", 1)
        story_id = int(parts[0])
        headline = parts[1] if len(parts) > 1 else ""
        stories.setdefault(story_id, []).append(headline)
    # Only multi-headline stories, limit count
    multi = {sid: hls for sid, hls in stories.items() if len(hls) > 1}
    return sorted(multi.items(), key=lambda x: -len(x[1]))[:limit]


# ---------------------------------------------------------------- LLM summary

def summarize_story(headlines, model=None):
    """Generate a one-sentence summary of a story from its headlines."""
    base_url = os.environ.get("LLM_BASE_URL")
    api_key = os.environ.get("LLM_API_KEY")
    if not base_url or not api_key:
        return None, "no gateway configured", None

    joined = "\n- " + "\n- ".join(headlines[:8])  # cap at 8 headlines
    prompt = (f"Summarize this news story in ONE sentence. "
              f"Use only facts from the headlines below. No speculation.\n\nHeadlines:{joined}")
    body = {"model": model or os.environ.get("LLM_CAPABILITY", "general"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2, "max_tokens": 200}
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + api_key,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.load(r)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        detail = exc.read().decode()[:200] if isinstance(exc, urllib.error.HTTPError) else str(exc)
        return None, f"gateway error: {detail}", None
    content = data["choices"][0]["message"]["content"] or ""
    return content.strip().strip('"'), "", data.get("model", "?")


# ---------------------------------------------------------------- run

def run(limit=50):
    """Generate story summaries for top stories. Returns summaries written."""
    stories = fetch_stories(limit=limit)
    if not stories:
        print("ML-7c: no multi-headline stories found", flush=True)
        return 0

    written = 0
    for story_id, headlines in stories:
        facts_hash = __import__("hashlib").sha256(
            "\n".join(sorted(headlines)).encode()).hexdigest()[:16]

        # Check if already summarized (same facts_hash)
        sql = (f"SELECT text FROM app.llm_outputs "
               f"WHERE kind='story_summary' AND scope='story_{story_id}' "
               f"ORDER BY ts DESC LIMIT 1")
        existing = psql(sql).strip()
        if existing:
            continue  # already summarized

        summary, err, model = summarize_story(headlines)
        if not summary:
            print(f"ML-7c: story {story_id} skipped — {err}", flush=True)
            continue

        verified = len(factcheck_problems(summary, headlines)) == 0
        if write_summary(story_id, len(headlines), summary, facts_hash, model or "unknown",
                         verified=verified):
            written += 1
            print(f"ML-7c: story {story_id} ({len(headlines)} headlines, verified={verified}): "
                  f"{summary[:100]}", flush=True)

    print(f"ML-7c: {written} new story summaries written", flush=True)
    return written


def show(limit=20):
    """List recent story summaries from app.llm_outputs."""
    sql = (f"SELECT scope, ts, text FROM app.llm_outputs "
           f"WHERE kind='story_summary' ORDER BY ts DESC LIMIT {limit}")
    rows = psql(sql)
    if not rows.strip():
        print("no story summaries")
        return
    print(f"{'scope':<20} {'ts':<30} text")
    print("-" * 80)
    for line in rows.strip().split("\n"):
        parts = line.split(",", 2)
        scope, ts = parts[:2]
        text = parts[2] if len(parts) > 2 else ""
        print(f"{scope:<20} {ts:<30} {text[:80]}")


def main():
    ap = argparse.ArgumentParser(description="ML-7c story summaries (shares PSQL_CMD pattern with ev0/ml0/ml1/ml3/ml5)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="generate LLM summaries for top stories + write to app.llm_outputs")
    r.add_argument("--limit", type=int, default=50, help="max stories to summarize")
    s = sub.add_parser("show", help="list recent story summaries")
    s.add_argument("--limit", type=int, default=20, help="max rows to show")
    a = ap.parse_args()
    cmd = os.environ.get("PSQL_CMD")
    if not cmd:
        sys.exit("set PSQL_CMD (see module docstring)")
    if a.cmd == "run":
        run(limit=a.limit)
    elif a.cmd == "show":
        show(limit=a.limit)


if __name__ == "__main__":
    main()
