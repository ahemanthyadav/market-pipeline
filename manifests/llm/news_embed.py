"""ML-2 groundwork — headline embeddings + story grouping via the shared llm-gateway (2026-10-19, ADR 0024).

Daily at 01:00 UTC (after news-fetcher 23:45, sentiment 00:15, NER 00:45). Embeds every raw_headlines row that
has no embedding yet (batches of BATCH, gateway capability `embed`, fallbacks DISABLED — vectors from two
different models cannot be compared, so the exact model name from the gateway is stored with each vector and
every comparison stays within one model). Then groups headlines into stories: a headline joins the story of
its most similar earlier headline from the previous STORY_HOURS if cosine similarity >= STORY_SIM, else it
starts its own story (story_id = its own headline_id).

Uses: /ask news search (semantic, bot), duplicate-story grouping. Never a scoring input (ADR 0018).
Only public headline text is sent. Exit 0 when the gateway is down (retried tomorrow — rows stay un-embedded).
Env: PG*, LLM_BASE_URL, LLM_API_KEY, BATCH (48), PAUSE (s between batches, 35 — gateway embed rpm is 2),
     MAX_BATCHES (60), STORY_SIM (0.86), STORY_HOURS (72).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

import psycopg


def connect():
    conn = psycopg.connect(host=os.environ.get("PGHOST", "timescaledb.market-data.svc.cluster.local"),
                           user=os.environ.get("PGUSER", "postgres"), password=os.environ["PGPASSWORD"],
                           dbname=os.environ.get("PGDATABASE", "postgres"))
    conn.autocommit = True
    return conn


def embed(texts):
    """-> (model_name, [vectors]). Raises RuntimeError('unavailable: …') on any gateway problem."""
    body = {"model": "embed", "input": texts, "encoding_format": "float", "disable_fallbacks": True}
    req = urllib.request.Request(os.environ["LLM_BASE_URL"].rstrip("/") + "/embeddings",
                                 data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + os.environ["LLM_API_KEY"],
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            model = r.headers.get("x-litellm-model-name") or ""
            data = json.load(r)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        detail = exc.read().decode()[:200] if isinstance(exc, urllib.error.HTTPError) else str(exc)
        raise RuntimeError(f"unavailable: {detail}") from exc
    vecs = [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]
    if not model or len(vecs) != len(texts):
        raise RuntimeError("unavailable: no model name or wrong vector count")
    return model.split("/", 1)[-1] if "/" in model else model, vecs   # strip any provider prefix


def vec_literal(v):
    return "[" + ",".join(f"{x:.6g}" for x in v) + "]"


class EmbedStopped(RuntimeError):
    """The gateway stopped answering part-way. `.done` = headlines embedded (and committed) before that."""

    def __init__(self, msg, done):
        super().__init__(msg)
        self.done = done


def rate_limited(exc):
    """Pure. True for the gateway's own rpm limit (worth waiting out), not for a dead or retired model."""
    m = str(exc)
    return "RateLimitError" in m or "No deployments available" in m or "429" in m


def embed_with_retry(embedder, texts, waits=(65.0, 65.0, 65.0), sleep=time.sleep):
    """The `embed` group is rpm-limited at the gateway (2/min, 2026-11-26): a backlog run hit the limit on
    its third batch and gave up with most of the backlog left. Wait out a rate limit; raise anything else."""
    for w in waits:
        try:
            return embedder(texts)
        except RuntimeError as exc:
            if not rate_limited(exc):
                raise
            print(f"rate-limited, waiting {w:g}s", flush=True)
            sleep(w)
    return embedder(texts)


def rejected(exc):
    """Pure. True when the provider refused the *input text* (NVIDIA answers 422 "input rejected by content
    safety screening"). Waiting does not help and the model is fine: the headline has to be left out."""
    m = str(exc)
    return "content safety" in m or "Error code: 422" in m


def embed_skipping_rejected(embedder, texts, pause=0.0, sleep=time.sleep):
    """-> (model or None, [vector or None per text]). One refused headline used to fail its whole batch of
    48, and because the batch is always the oldest pending rows, every later run hit the same batch and
    embedded nothing (2026-11-26). Halve a refused batch until the refused headline is alone, and return
    None for it."""
    try:
        return embed_with_retry(embedder, texts, sleep=sleep)
    except RuntimeError as exc:
        if not rejected(exc):
            raise
    if len(texts) == 1:
        return None, [None]
    mid = len(texts) // 2
    sleep(pause)
    m1, v1 = embed_skipping_rejected(embedder, texts[:mid], pause, sleep)
    sleep(pause)
    m2, v2 = embed_skipping_rejected(embedder, texts[mid:], pause, sleep)
    return m1 or m2, v1 + v2


def embed_pending(conn, embedder=embed, batch=48, pause=35.0, max_batches=60, sleep=time.sleep, reembed_from=""):
    """Embed headlines that have no vector yet. `reembed_from` = a retired model's name: its rows are
    replaced too (oldest first), so story grouping and search see them again after a model change
    (2026-11-26: both NVIDIA embedding models were retired; vectors of different models never compare).
    A replaced row loses its story_id and is regrouped among the new model's rows.
    A headline the provider refuses is left as it was and not asked again in this run."""
    done, skip = 0, []
    for i in range(max_batches):
        with conn.cursor() as cur:
            cur.execute("SELECT r.id, r.ts, r.headline FROM raw_headlines r "
                        "LEFT JOIN headline_embeddings e ON e.headline_id = r.id "
                        "WHERE (e.headline_id IS NULL OR e.model = %s) AND r.ts >= now() - interval '400 days' "
                        "AND r.id <> ALL(%s) ORDER BY r.ts LIMIT %s", (reembed_from or "(none)", skip, batch))
            rows = cur.fetchall()
        if not rows:
            break
        if i:
            sleep(pause)
        try:
            model, vecs = embed_skipping_rejected(embedder, [h[:1000] for _id, _ts, h in rows], pause, sleep)
        except RuntimeError as exc:
            raise EmbedStopped(str(exc), done) from exc
        refused = [hid for (hid, _ts, _h), v in zip(rows, vecs) if v is None]
        if refused:
            skip.extend(refused)
            print(f"refused by provider content screening, left out: headline ids {refused}", flush=True)
        with conn.transaction():
            with conn.cursor() as cur:
                for (hid, ts, _h), v in zip(rows, vecs):
                    if v is None:
                        continue
                    cur.execute("DELETE FROM headline_embeddings WHERE headline_id = %s", (hid,))
                    cur.execute("INSERT INTO headline_embeddings (headline_id, ts, model, embedding) "
                                "VALUES (%s, %s, %s, %s::vector)", (hid, ts, model, vec_literal(v)))
        done += len(rows) - len(refused)
        print(f"embedded {done} (model {model})", flush=True)
    return done


def group_stories(conn, sim=0.86, hours=72):
    """Assign story_id to headlines that have none, oldest first (so earlier stories exist)."""
    n = 0
    with conn.cursor() as cur:
        cur.execute("SELECT headline_id, ts, model FROM headline_embeddings WHERE story_id IS NULL "
                    "AND ts >= now() - interval '400 days' ORDER BY ts, headline_id")
        todo = cur.fetchall()
    for hid, ts, model in todo:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.story_id, 1 - (p.embedding <=> e.embedding) AS s "
                "FROM headline_embeddings e JOIN headline_embeddings p "
                "  ON p.model = e.model AND p.story_id IS NOT NULL AND p.headline_id <> e.headline_id "
                " AND p.ts <= e.ts AND p.ts >= e.ts - make_interval(hours => %s) "
                "WHERE e.headline_id = %s ORDER BY p.embedding <=> e.embedding LIMIT 1", (hours, hid))
            best = cur.fetchone()
            story = best[0] if best and best[1] >= sim else hid
            cur.execute("UPDATE headline_embeddings SET story_id = %s WHERE headline_id = %s", (story, hid))
        n += 1
    return n


def main() -> int:
    conn = connect()
    try:
        done = embed_pending(conn, batch=int(os.environ.get("BATCH", "48")),
                             pause=float(os.environ.get("PAUSE", "35")),
                             max_batches=int(os.environ.get("MAX_BATCHES", "60")),
                             reembed_from=os.environ.get("REEMBED_FROM", "").strip())
        failed = False
    except RuntimeError as exc:
        print(f"EMBED-UNAVAILABLE {str(exc)[:200]}", flush=True)
        done = getattr(exc, "done", 0)   # batches before the failure are committed — report them
        failed = True
    grouped = group_stories(conn, float(os.environ.get("STORY_SIM", "0.86")), int(os.environ.get("STORY_HOURS", "72")))
    with conn.cursor() as cur:
        cur.execute("SELECT count(*), count(DISTINCT story_id) FROM headline_embeddings "
                    "WHERE ts >= now() - interval '1 day'")
        last_day, stories = cur.fetchone()
    conn.close()
    # From 2026-11-23 to 11-26 both embedding models were retired upstream and this job printed
    # "NEWS-EMBED-OK embedded=0" every night, exit 0. A run that could embed nothing is a failure.
    status, rc = ("NEWS-EMBED-FAIL", 1) if failed and done == 0 else ("NEWS-EMBED-OK", 0)
    print(f"{status} embedded={done} grouped={grouped} last_24h={last_day} headlines in {stories} stories"
          + (" (stopped early: gateway unavailable)" if failed else ""), flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
