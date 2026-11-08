# Documentation Index — marketdata

> **What this repo is:** a home-lab market-data platform on a 5-node k3s cluster
> (Pi workers + Jetson TX2) ingesting ~1,000 instruments daily — indices, FX,
> commodities, yields, rates, news, FinBERT sentiment, FII/DII flows, technicals,
> and a 2,251-stock fundamental screener (S&P 1500 + Nifty Total Market, ADR 0029) — into TimescaleDB,
> with Grafana dashboards, Tailscale-only access, and no container registry.
> **Status 2026-10-26 — healthy; hardening + valuation/membership layer deployed.** Pod hardening applied
> (SA tokens off everywhere, probes, non-root canary passed; netpol applied but unenforced on the TX2 — kernel
> lacks ipset, see `manifests/network-policies.yaml`). New since 10-26: **EV-9 full statement lines**,
> **EV-1 point-in-time S&P 500 membership** (`index_membership`, ADR 0025 — US signal work unblocked),
> **G-20 factor returns** (`factor_returns`: Ken French + IIM-A), **Auto/FMCG + MIDCAP150 restored via
> NSE-direct** (series complete 2015→today), and the **ADR 0026 valuation layer**: an 10-method intrinsic
> fair-value panel per symbol daily (`stock_valuations`), sector→style metric-relevance tables
> (`sector_styles`/`metric_relevance`), and daily sector aggregates + rendered `__sectors__` analysis
> (`sector_metrics`) — all descriptive, never in the composite without EV-0 (amended 2026-10-26: the owner-approved VALUATION WATCH alert may fire from it — telegram v25). **UI live:** 11 provisioned Grafana dashboards + the `marketview` web explorer (sibling repo, ADR 0027 — dossiers, comparison charts, sector views, rendered reports, watchlist + scorecard pages; `kubectl -n market-data port-forward svc/marketview 8080:8080`). **Accountability layer live 2026-10-27 (ADR 0028):** prediction scorecard (write-once snapshots, 30/91/182d benchmark-relative outcomes), weekly earnings calendar (digest section, dossier badge, valuation-watch 3-day earnings gate), regime traffic light (digest + /regime + home page; descriptive only), weekly DQ-5 accuracy job (`__accuracy__` report; first run ALL CLEAN, 10/10 FRED cross-checks). AI layer live (shared
> `llm-gateway`, NVIDIA free tier: daily AI market note, every number checked — ADR 0022/0023).
> 4 nodes Ready (pi2 cordoned control plane), 36 CronJobs (nse-fundamentals suspended until its first backfill completes), the others
> active, DB `Guaranteed` QoS with readiness probe. Images: consolidated toolkit images `local/marketdata-light:v1`,
> `local/marketdata-data:v1`, `local/marketdata-ml:v1` (TX2 only), telegram `local/telegram-digest:v37`. Least-privilege DB
> roles for Grafana and the Telegram bot. Daily second copies of the DB dump
> and the k3s control-plane datastore on the workstation. A mains power loss on 10-18 rebooted everything — recovered on
> its own; no UPS by decision. Work order and reasoning: `engineering-plan-2026-10-18.md`; open items: `pending-tasks.md`.

## Reading order (new session? start here)

1. `AGENTS.md` (repo root) — ground rules, invariants, DB access pattern, agent roster
2. `market-data-architecture.md` — what the system *is*
3. `market-data-pipeline-operations-manual.md` — how to *operate* it
4. Everything else, as needed

## The docs

| Doc | Purpose | Read when | Update when |
|---|---|---|---|
| **`pending-tasks.md`** | **The single tracker of everything not yet done** — immediate, security, features, v2, recurring, icebox; each item links to its owning doc | Start of any session / before planning work; after completing anything | Mark ✅ + date on completion; owning doc updated in the same session |
| **`database-contract.md`** | The data platform's published interface (ADR 0032): `v_*` contract views, schema-change process + `schema_migrations`, role matrix, invariants consumers may rely on | Building anything that reads the DB (any repo); before any schema change | New view/role; a consumer migrates; AR-8 lands |
| **`market-data-architecture.md`** | Canonical system reference: nodes, tech stack, feed catalog, DB schema + live volumes, all workloads, security, limitations register, file/repo structure | You need to understand what exists and why | Any cluster/schema/workload change; capacity facts; limitations |
| **`node-roles.md`** | Per-node jobs and responsibilities for pi0–pi3 + tx2: what each runs (pinned workloads, host cron jobs), CronJob placement, blast radius, current scheduling state | You need to know what a node is for, or what breaks if it dies | A pin, cordon, host job, image placement or hardware change |
| **`market-data-pipeline-operations-manual.md`** | The runbook: image build/redistribute cycle (§6), daily health checklist (§7), failure recovery copy-paste commands (§8), backup/restore + drill (§9), DB query reference (§5) | Something breaks, or before/after any mutation | New failure modes, new jobs, new runbook lessons; feed quirks (§4) |
| **`deployment-guide.md`** | Start-to-finish deploy path: change-type decision table, pre-flight gates, condensed §6 image cycle (incl. the in-image hash check), apply/canary rules, rollback, record-and-push | Deploying anything; onboarding a session that will mutate the cluster | The deploy flow itself changes; a new deploy-time failure mode (deep detail still goes to ops manual §6/§8) |
| **`market-data-pipeline-plan.md`** | The original design/build log — Phase 0→8 with per-phase build notes, decisions, and the incident history of building it (pi2 wedges, TX2 bring-up) | You want the "why did we do it this way" history | Phase-completion ticks; major scope changes |
| **`project-candidates.md`** | Working menu of everything that can be its own project/session — agent-ready, gated, time-gated, and architecture items; each with size, gate, and OK-marker | Picking the next project or session; sizing work before starting | A project completes, a gate clears, or a new candidate is found |
| **`repo-structure-criteria.md`** | Four-criteria decision framework (C1–C4) for what stays in the `marketdata` monorepo vs what gets its own repo (`llm-gateway`, `marketview` already separate) | Before proposing a repo split; onboarding the repo-layout decision | Any component moves between repos or a criterion changes |
| **`system-architecture-visual.md`** | Visual companion to `market-data-architecture.md`: Mermaid diagrams for system context, deployment, temporal job chain, data flow, DB entity relationships, image rebuild cycle, failure detection, and project dependency map | You need to see how components talk to each other at a glance | Any cluster/schema/workload change; new inter-project dependency |
| **`market-data-pipeline-architecture-roadmap.md`** | Hardening roadmap with live status: what's DONE (constraints, heartbeat, restore drill), what's next (alerts, feed gaps), what is deliberately NOT changing | Planning the next hardening step | Whenever a roadmap item completes or a new "do not change" decision lands |
| **`market-data-finds-and-projects.md`** | **32 hard-won finds** with fixes (infra gotchas, feed quirks, screener-rollout lessons, alerting-provisioning scars, ML capacity facts) + the tiered project catalog with recommended order | Before debugging anything (someone already hit it) or picking the next project | Every new gotcha; project completions |
| **`stock-screener-implementation-plan.md`** | The v1 BUILD ORDER for the Nifty 500 + S&P 500 screener — as-built schema, sources (EDGAR + mixed), CronJobs, balanced scoring formulas, validation bars, rollout phases with live status | Extending/fixing the screener layer | Any screener code/schema change; phase completions |
| **`ui-implementation-plan.md`** | The visualization plan (2026-10-26): Phase 1 Grafana dashboards for the valuation/sector/factor/membership layers (agent-ready, grants verified); Phase 2 read-only FastAPI "Analysis Explorer" (dossier pages, rendered reports, full-universe tables — ADR 0027 at build); Phase 3 extras | Building or changing any UI/dashboard | When a phase lands or the stack decision changes |
| **`nifty500_sp500_fundamental_analysis_implementation_plan.md`** | The TARGET ARCHITECTURE (v2+) above v1: security master, sector-relative 6-component scoring, classifications, style tags + investor lenses (§13), market-regime layer (§14), AI placement, binding ops constraints (§15) | Planning v2+ analysis features | When v1 scope shifts or a v2 item gets built |
| **`market-data-telegram-bot.md`** | The Telegram channel as built — digest CronJob, Grafana alert contact point, **and the two-way listener (deployed 2026-10-09)** — plus operating procedures (test-firing, threshold tuning, token rotation) and the remaining update catalog incl. Strata-chat blockers | Anything touching the bot or notification channel; planning further two-way work | Any bot/alerting change; new alert rules; two-way decisions |
| **`data-foundations-schema-draft-2026-10-10.md`** | DRAFT (nothing applied): verified fundamentals/price data defects (DQ-2, DQ-3, G-1) and the additive schema + writer plan that fixes them (`financial_facts`, `corporate_actions`, membership snapshots, factor returns) | Before touching `stock_fundamentals.py`, `stock_prices.py` or the schema ConfigMap | The draft is applied, amended or rejected (record which) |
| **`data-gaps.md`** | The single inventory of every known data gap (DG-01…DG-19): what is missing, why, the research/backtest consequence, status (open/blocked/accepted), priority ranking for closing | Before planning any backtest or research feature (the gap list bounds what is testable); when a DG-xx id is cited elsewhere | A gap opens, closes, or its status/owner-decision changes (dated, per the header's legend) |
| **`evidence-based-signals-roadmap.md`** | Which screener/bot signals have research backing, which do not, the replacement for each weak one, and the build order (EV-0…EV-18) with a written acceptance test | Before adding or re-weighting any factor, screen or score | A signal is tested (record pass/fail in §5), added, or dropped |
| **`ml-roadmap-2026-10-10.md`** | The ML/LLM plan beyond ranking signals: ML-0…ML-8, **each labelled by research backing 🟢🟡🔵⚪** (experiment infra, anomaly detection, news embeddings, vol forecasts, regime, peer clusters, hard-gated GBM ranker, **LLM via hosted API**), guardrails, phasing, data/deploy pattern, risks, ADR candidates | Planning any model or LLM work; before touching the digest/bot narrative | When an ML item is built, rejected, or gated differently (update the tracker in the same session) |
| **`prediction-validation-and-training-2026-10-10.md`** | Pipeline so every daily prediction is logged write-once, resolved against outcomes on adjusted prices, scored against baselines and monitored; plus the offline train → gate → shadow → promote path for components that learn. Inventory of what is predicted today, schema, daily jobs, per-kind validation rules, acceptance tests (PV-0…PV-3, TP-0…TP-2) | Adding or changing any score, forecast or model; judging whether a prediction works | When a prediction kind is added/retired or a threshold decision (ADR) lands |
| **`engineering-plan-2026-10-18.md`** | Order and reasoning for the 2026-10-18 engineering pass (AR-4, DQ-1/DQ-2 data foundations, chunk merge, EV-0, AR-8, news constraints, AR-6/7 decision) and what is deliberately deferred | Before starting large work; to see why something was not done | When the order changes or an item is decided |
| **`architecture-review-2026-10-10.md`** | Architect's review of the repo/system structure: strengths to keep, 12 findings (AR-1…AR-12) with evidence, target repo layout, phased plan, ADR candidates, how to re-measure | Planning structural/refactor work; before adding jobs or touching manifests | When an AR item is applied or rejected (update the tracker in the same session) |

## Roles in one line each

- **Architecture** = the map · **Ops manual** = the driving school · **Plan** = the
  travel diary · **Roadmap** = the maintenance schedule · **Finds** = the scars ·
  **Screener plan** = the current construction project · **Target architecture** =
  the city plan it grows into.

## House documentation rules

- Docs are the record of truth (in git since 2026-10-10, remote on GitHub) — update on
  every verified change; keep "Last verified" dates true.
- Feed quirks/symbol deaths → ops manual §4 **and** the fetcher script header.
- New failure modes → ops manual §8 (copy-paste fix commands).
- Long-shelf-life decisions → `docs/adr/` via the `adr` skill — 33 written
  (0001–0033, latest: classification soundness, continuous calibration loop, and deterministic-first routing); see `docs/adr/` for the format.
- Secrets never enter docs — see AGENTS.md §7 for where they live. Neither do IP
  addresses (AGENTS.md §8): use node names/placeholders; real values are in the
  gitignored `.hosts.env`.
