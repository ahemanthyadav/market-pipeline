# UI / Visualization Implementation Plan — all analysis & data (2026-10-26)

> **Goal:** one place to *see* everything the pipeline computes — prices, fundamentals,
> the ADR 0026 valuation panel, sector relevance + aggregates, scores/screener, factor
> returns, membership history, flows, sentiment, reports, pipeline health.
> **Decision (pending ADR 0027 at Phase 2 build):** extend the EXISTING Grafana (cheap,
> tonight-ready, provisioned-as-code) for everything chart-shaped, then a small read-only
> **Analysis Explorer** web app for the things Grafana is bad at: rendered markdown
> reports, a one-page per-stock dossier, full-universe sortable tables with cross-links.
> No registry (ADR 0004), Tailscale/port-forward access only, read-only DB role, every
> query rule-9 bounded, nothing writable from the UI.

## What there is to visualize (inventory, 2026-10-26)

| Layer | Tables | Today's UI |
|---|---|---|
| Prices | stock_prices, index_prices, intraday_prices, fx_rates, bond_yields, interest_rates | Grafana (overview/indices/markets/derived) |
| Fundamentals | stock_fundamentals (latest), financial_facts (point-in-time, EV-9 lines) | none |
| **Valuation (ADR 0026)** | stock_valuations (8-method panel), sector_styles, metric_relevance | none |
| **Sector analysis** | sector_metrics (daily medians/returns/upside) | none (one `__sectors__` md row) |
| Scores/screener | stock_scores, screening_runs, stock_reports (md) | Grafana screener (no upside, no reports) |
| Evidence layer | factor_returns (G-20), index_membership (EV-1), universe_membership_snapshots | none |
| Flows/sentiment | capital_flows, news_sentiment, headline_events | Grafana (flows/sentiment) |
| Ops | pipeline_heartbeat, job_runs (AR-3) | Grafana alerts only |

## Phase 1 — Grafana dashboards for the new layers (agent-ready, ~1 session)

No new infra, no new grants (verified 2026-10-26: `grafana_ro` already has SELECT on
stock_valuations / sector_metrics / factor_returns / index_membership via default
privileges). All provisioned as code in `manifests/grafana/dashboards.yaml` (apply the
ConfigMap; the file provider picks changes up without a pod restart). Rule-9 bounds on
every hypertable query.

1. **`valuation.json` — Valuation Explorer** (symbol variable from universe_constituents):
   stat row (close · fair value · upside% · methods count · discount rate); bar panel of
   the 8 per-method values for the symbol; table: top-25 / bottom-25 upside per universe
   (eligible names only, join stock_scores); fair-value-vs-close time series (accrues as
   stock_valuations builds history); method-coverage stat row (count per method, today).
2. **`sectors.json` — Sector Analysis** (universe variable): latest sector_metrics table
   joined to sector_styles (members, median P/E/P/B/EV-EBITDA/ROE/margin, 6m/12m, median
   upside, lens note) — the live version of the `__sectors__` report; bargauge of median
   upside by sector; time series of a chosen sector's medians; metric_relevance table
   (weight + reason) for a chosen sector — "what matters here" as a dashboard.
3. **`dossier.json` — Stock Dossier** (symbol variable): latest fundamentals table;
   annual statement history from financial_facts (revenue / net income / FCF / dividends
   time series — EV-9 lines); valuation stat row; membership state timeline from
   index_membership; the symbol's stock_scores row; recent matching headlines.
4. **`factors.json` — Factor Returns** (market variable US/IN): cumulative compounded
   factor series from factor_returns (window SQL over monthly percents); latest-month
   bars; long-window factor table.
5. **Screener touch-up:** add `upside_pct` (join stock_valuations) to the Top-25 table.

Deliverables: edited `dashboards.yaml`, `kubectl apply`, screenshot-level verification of
each panel, docs sync (ops manual Grafana section lists dashboards), tracker tick.

## Phase 2 — "Analysis Explorer" app (1–2 sessions + §6 cycle; ADR 0027 at build time)

**Why an app at all:** stock_reports markdown (per-stock, `__digest__`, `__sectors__`)
deserves real rendering; a dossier page should combine valuation panel + sector lens +
relevance notes + fundamentals + news in ONE scrollable page with links; full-universe
sortable/filterable tables beat Grafana tables.

- **Stack:** FastAPI + Jinja2 + htmx + `markdown` lib on `python:3.12-slim` — pure-Python
  (native TX2 build, no node toolchain), ~50 MB image, matches every house pattern.
  Explicitly not Streamlit/Dash (RAM-hungry on Pis) and not a JS SPA (build-chain cost).
- **Feed dir:** `manifests/webui/` — `app.py`, `templates/`, `Dockerfile`,
  `requirements.lock` (AR-5 from day one), `deployment.yaml` (+Service). Image
  `local/webui:v1`, `imagePullPolicy: Never`, pinned **pi3** (beside Grafana; check
  `free -m` headroom first), requests 64Mi/50m, limits 256Mi/500m, non-root
  securityContext + readiness probe + `automountServiceAccountToken: false` from day one.
- **DB access:** the existing read-only `grafana_ro` role via Secret `grafana-db-ro`
  (no new role, no owner SQL). SELECT-only, parametrized, rule-9 bounds; headlines and
  report markdown HTML-escaped / sanitized (DB text is untrusted input to a browser).
- **Pages:** `/` (sector table + latest digest + health strip) · `/stock/{symbol}`
  (dossier: valuation panel, sector lens + relevance notes, fundamentals, statement
  sparklines, score row, news, link to raw report) · `/screener?universe=` (full-universe
  sortable table: rank, composite, factors, upside) · `/sectors` · `/reports/{key}`
  (rendered stock_reports incl. `__digest__`/`__sectors__` + date picker) ·
  `/health` (pipeline_heartbeat + job_runs last-run table).
- **Exposure v1:** ClusterIP + `kubectl port-forward` (exactly like Grafana today).
  Owner decision recorded at build: optionally NodePort on the LAN or `tailscale serve`
  for phones. No auth in v1 — same trust boundary as Grafana's port-forward.
- **Tests:** hermetic route tests (scripted fake connection, like the fetcher tests),
  markdown-rendering/escaping tests, a bounds-lint test asserting every SQL string in
  app.py contains a `ts >=` bound or targets a non-hypertable.
- **Ops:** 4th always-on workload → `workloads.py --check` tables, ops manual §3 row,
  README status line, canary = rollout + curl every route via port-forward.

## Phase 3 — later, each its own session

- Telegram v25 deep links: `/stock` card links to the dossier URL (riders already queued).
- Valuation history views once stock_valuations accrues weeks of rows (fair-value drift,
  upside distribution over time).
- EV-0 result visualization (IC/t-stat tables per signal) after the §5 re-run lands.
- Sector rotation view (relative 1m/3m sector returns vs universe).
- Auth / Tailscale identity headers ONLY if ever exposed beyond port-forward.

## Order, effort, gates

| Step | Effort | Gate |
|---|---|---|
| Phase 1 dashboards | ~1 session | none — agent-ready now |
| Phase 2 app | 1–2 sessions + §6 cycle | ADR 0027 write-up at build; pi3 RAM check; owner exposure choice |
| Phase 3 items | à la carte | per item |

**Explicitly not doing:** public internet exposure (CGNAT + by decision), write paths
from the UI (watchlist edits stay in the bot), LLM content in the UI v1 (factcheck-gated
narratives remain the digest's job), React/node toolchains, a registry.
