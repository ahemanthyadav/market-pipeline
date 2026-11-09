# 0027. marketview: a read-only FastAPI explorer in its own repo

- Date: 2026-10-26
- Status: accepted (deployed same day: `local/marketview:v1`, pinned pi1)

## Context
The owner wants a full web application to review all analysis — dossiers, historic
comparison charts, sector views, rendered reports — beyond what Grafana panels do well
(UI plan: `docs/ui-implementation-plan.md`; Phase 1 Grafana dashboards shipped first).
Options: grow it inside marketdata/manifests (where the bot lives), or a sibling repo
like `../llm-gateway`. Stack options: Streamlit/Dash (RAM-hungry on 1 GB Pis), a JS SPA
(node toolchain, violates the pure-Python TX2 build pattern), or FastAPI + Jinja +
vanilla JS with a pinned, vendored Chart.js.

## Decision
1. **Separate repo `../marketview`** (the llm-gateway precedent): a real application with
   templates, static assets and its own tests outgrows a manifests directory, and its
   release cadence is independent of the pipeline's. marketdata's AGENTS.md applies in
   full; the repo's own AGENTS.md restates the sharp edges.
2. **FastAPI + Jinja2 + Chart.js 4.4.7**, vendored at image build with a sha256-pinned
   download — the app serves no CDN content at runtime. Pure Python, same no-registry
   §6 cycle, image `local/marketview:vN` imported on pi1 only (Deployment pins pi1 —
   1 GB node, 192Mi limit, beside the bot).
3. **Read-only by construction**: connects as `grafana_ro` (Secret `grafana-db-ro`);
   every SQL lives in one dict that a test LINTS (SELECT-only, rule-9 bounds on
   hypertables, ORDER BY whitelist); report markdown and headlines are HTML-escaped
   before rendering (RSS text is untrusted input to a browser).
4. **Exposure v1 = ClusterIP + port-forward**, the same trust boundary as Grafana.
   NodePort / `tailscale serve` is an owner decision tracked in pending-tasks.
5. **Charts are comparisons, not decoration**: price rebased vs the universe benchmark
   (^GSPC / ^CRSLDX), fair value vs close (accrues from stock_valuations), annual
   statement bars (point-in-time financial_facts), stock P/E vs sector median P/E, and
   a /compare page overlaying up to four names rebased to 100.

## Consequences
- Cluster state now spans two repos: the Deployment manifest lives in marketview
  (`deploy/deployment.yaml`), so marketdata's `workloads.py --check` does not see it —
  the ops manual notes marketview as an externally-managed workload instead.
- pi1 hosts two always-on pods (bot 128Mi + marketview 192Mi limits) on 1 GB; headroom
  verified at deploy (~520 MB available). If pi1 tightens, marketview is the one to move.
- A fifth always-on workload means one more §6 cycle per app change; the hermetic test
  suite (`tests/test_app.py`) is the pre-push gate, mirroring marketdata discipline.
