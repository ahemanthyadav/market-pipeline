"""LLM bake-off for ML-7a (llm-gateway ADR 0002): which model writes the most faithful notes?

For each of the last DAYS trading days, rebuild that day's facts (narrative.build_facts(as_of)) and
ask every CANDIDATE model group the same question. Scored on: first-try pass rate (every number in
the facts), final pass rate (after the one retry), median latency, refusals/errors. Prints a table and
a BAKEOFF-JSON line; writes nothing. Pick the winner as the primary in ../llm-gateway/config/litellm.yaml,
the next two from other families as fallbacks.

Env: PG*, LLM_BASE_URL, LLM_API_KEY (a temporary bake-off key allowed on the candidate groups),
     CANDIDATES (comma list of gateway model groups), DAYS (10), PAUSE (s between calls, 3).
"""
import json
import os
import statistics
import sys
import time

import narrative as n


def trading_days(cur, k):
    cur.execute("SELECT DISTINCT ts::date FROM index_prices WHERE ticker='^NSEI' ORDER BY 1 DESC LIMIT %s", (k,))
    return [r[0] for r in cur.fetchall()]


def score(results):
    """results: [{"first_ok", "final_ok", "latency", "error"}] -> summary dict (pure)."""
    lat = [r["latency"] for r in results if r["latency"] is not None and not r["error"]]
    k = len(results) or 1
    return {"runs": len(results),
            "first_try_pass": round(sum(r["first_ok"] for r in results) / k, 2),
            "final_pass": round(sum(r["final_ok"] for r in results) / k, 2),
            "errors": sum(bool(r["error"]) for r in results),
            "median_latency_s": round(statistics.median(lat), 1) if lat else None}


def run_one(facts, model, asker=n.ask):
    first = {}

    def timed(msgs):
        t = time.time()
        out = asker(msgs, model)
        first.setdefault("latency", time.time() - t)
        # an empty reply is NOT a pass (it has no bad numbers, but it is no answer) — llm-gateway ops/bakeoff.py
        first.setdefault("ok", bool(out[0] and out[0].strip()) and not n.bad_numbers(out[0], facts)
                         and not n.wrong_directions(out[0], facts))
        return out
    status, _s, _m, _a, detail = n.narrate(facts, timed)
    return {"first_ok": bool(first.get("ok")), "final_ok": status == "ok",
            "latency": first.get("latency"), "error": detail if status == "unavailable" else ""}


def main() -> int:
    cands = [c.strip() for c in os.environ.get("CANDIDATES", "fast-json,fast-json.2,fast-json.3,general,general.2").split(",") if c.strip()]
    days, pause = int(os.environ.get("DAYS", "10")), float(os.environ.get("PAUSE", "3"))
    conn = n.connect()
    with conn.cursor() as cur:
        facts_by_day = [(d, n.build_facts(cur, d)) for d in trading_days(cur, days)]
    conn.close()
    table = {}
    for model in cands:
        res = []
        for d, facts in facts_by_day:
            res.append(run_one(facts, model))
            time.sleep(pause)
        table[model] = score(res)
        print(f"{model:16s} {table[model]}", flush=True)
    ranked = sorted(table, key=lambda m: (-table[m]["first_try_pass"], -table[m]["final_pass"],
                                          table[m]["median_latency_s"] or 1e9))
    print("RANKING:", " > ".join(ranked), flush=True)
    print("BAKEOFF-JSON " + json.dumps({"days": [str(d) for d, _ in facts_by_day], "results": table}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
