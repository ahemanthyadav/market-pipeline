# Project candidates — work that can be its own project

> **Purpose:** a living index of items in `docs/pending-tasks.md` that are big enough
> to run as an independent session(s) with their own objective and OK-marker.
> Detail stays in `docs/pending-tasks.md` and the linked docs; this file is the
> menu. Maintained with the same discipline: mark items ✅ when done, prune into
> the owning doc's history at housekeeping time.
>
> **Last verified: 2026-10-31** against `docs/pending-tasks.md` and the live cluster
> (nodes Ready; `workloads.py --check` passes; `tests/run-all.sh` green).

---

## How to use this list

Pick one item → run it as its own session → record ✅ in `docs/pending-tasks.md`
and update the owning doc in the same session. Size is my prior estimate of a
single focused session for one agent on a calm cluster; re-rate after the first
run.

Legend: **P0/P1/P2/P3** = priority from `stock-screener-implementation-plan.md` §16
· **🔶** needs work · **⛔** blocked · **🤔** needs a decision · **🔄** recurring

---

## A. Agent-ready now — no decision needed, just a session

### EV-0 domain (blocking path for US signals / composite v2)

| # | Project | Size | Gate / first decision | OK-marker |
|---|---------|------|----------------------|-----------|
| A1 | **EV-0 §5 re-run on point-in-time membership** | 1 session | House-prescribed next; pair review. US gates clear (EV-1 US + EV-9 live). | `ev0.py run` IC table written; nothing new passes §5; composite unchanged |
| A2 | **EV-10…14 → composite v2** | 2+ sessions | Per-universe rules = owner decision at the end. EV-9 → EV-10…13 statement lines + signals. | Screener ships composite v2 equal-weight profitability+investment; confidence is measured IC/hit rate |
| A3 | **G-21 extra signals** | ✅ Amihud/max_daily_ret/idio_vol + industry_momentum + distress_score added | Amihud/max_daily_ret/idio_vol + industry_momentum + distress_score added to `ev0.py` + tested via EV-0 §5 ✅ (all FAIL §5 as expected — no predictive power); net payout yield needs buyback data (not available) | Each must pass EV §5; price/volume/fundamentals signals land in `stock_metrics` after EV-0 pass |
| A4 | **PV-1 model-health dashboard + alerts** | ~1 session | `prediction-tracker` (schema 18) + resolver already live; outcomes accrue from 2026-11-26. | Dashboard shows prediction vs outcome, IC, hit rate; alerts for drift |

### ML domain (reuses EV-0 harness, never bypasses EV §5)

| # | Project | Size | Gate / first decision | OK-marker |
|---|---------|------|----------------------|-----------|
| A5 | **ML-0 experiment infrastructure** | ✅ done 2026-11-04 | `ops-tools/ml0.py` — walk-forward splitter, leakage test, trial budget, `ml_runs` tracking via `app.ml_runs`; tested end-to-end with ret_12m across 5 universes |
| A6 | **ML-1 anomaly detection v1** | ✅ done 2026-11-04 | `ops-tools/ml1.py` — bad_bar, feed_drop, row_count_drop, scraper_drop rules; writes to `app.data_anomalies`; `tests/test_ml1.py` 11 tests all pass | `ml1.py run` writes anomalies; `ml1.py show` lists them |
| A7 | **ML-2 semantic search + story digests** | ✅ semantic search done 2026-10-19 (`ask.py` semantic search over `headline_embeddings`) | Embeddings live (`news-embed`, pgvector); story clustering done at STORY_SIM=0.82. | `/ask` returns news results with semantic search ✅; story summary rows via ML-7c |
| A8 | **ML-7c story summaries** | ✅ done 2026-11-04 | `ops-tools/ml7c.py` LLM summaries → `app.llm_outputs` kind=story_summary; `tests/test_ml7c.py` 3 tests | Story rows populated with LLM summaries, needs gateway to run live |
| A9 | **ML-3 volatility forecasts** | ✅ done 2026-11-04 | `ops-tools/ml3.py` EWMA/GARCH/GJR-GARCH → `app.vol_forecasts`; `tests/test_ml3.py` 8 tests | Forecasts written for all 38 index tickers, unit-tested |
| A10 | **ML-5 peer clusters** | ✅ done 2026-11-04 | `ops-tools/ml5.py` correlation clusters → `app.peer_clusters`; `tests/test_ml5.py` 4 tests | Peer clusters written + stable across re-runs |

### PV / DQ / data domain

| # | Project | Size | Gate / first decision | OK-marker |
|---|---------|------|----------------------|-----------|
| A11 | **PV-2 / PV-3 ledger extension + drift monitor** | 1 session | Extend prediction ledger to sentiment, alerts, measured confidence. | Ledger writes alert+sentiment context; drift monitor alerts |
| A12 | **DQ-5 display half (basis footnote in marketview)** | small session | Dossier mixes EDGAR filing-basis rows with yfinance TTM snapshots — correct data, two bases. | Marketview dossier shows a basis column or footnote |
| A13 | **Screener value factor repricing** | small session | 🤔 decision first: reprice weekly Yahoo ratio snapshot inside `stock_screener.py` like `/stock` does. | Value screen uses day's close, not Saturday's fundamentals snapshot |

### Macro / source / ML-7a follow-on

| # | Project | Size | Gate / first decision | OK-marker |
|---|---------|------|----------------------|-----------|
| A14 | **G-13 macro series** | ~1 session | T10Y2Y, HY OAS, Buffett indicator via existing rates-fetcher pattern (v2+ §14). | New series in `interest_rates`/`bond_yields`, heartbeat fresh |
| A15 | **G-18 second price source** | ~1 session | 🤔 owner choice: free API key (Finnhub/Tiingo); Stooq now has a JS challenge wall. | Weekly accuracy-check job consumes second source for ~25-symbol sample |
| A16 | **ML-7b shortlist notes** | ~1 session | ~2026-11-02, after 2 weeks of `llm_narratives`/`llm_narrative` watch. | Shortlist notes generated for ranked stocks |

### Individual signal / fundamentals items (P2/P3, no gates)

| # | Project | Size | Gate / first decision | OK-marker |
|---|---------|------|----------------------|-----------|
| A17 | **G-9 US insider + short interest** | ~1 session | None. | Signal lands, passes EV §5 |
| A18 | **G-10 India promoter pledge/holding** | ~1 session | None. | Signal lands, passes EV §5 |
| A19 | **G-12 breadth/sector rotation** | ~1 session | None. | Signal lands, passes EV §5 |
| A20 | **G-19 13F holdings** | ~1 session | None. | Signal lands, passes EV §5 |
| A21 | **G-16 filing-text analytics** | ~1 session | None. | Text features computed on EDGAR filings |
| A22 | **G-17 mid/small-cap universe** | ~1 session | None. | Universe membership snapshots extended |
| A23 | **G-11 India delivery/bulk/OI** | ~1 session | None. | Flow features for NSE symbols |

### Risk / portfolio / other

| # | Project | Size | Gate / first decision | OK-marker |
|---|---------|------|----------------------|-----------|
| A24 | **EV-6/8/15/16** — risk-scaled + residual momentum, post-earnings drift, portfolio rules + risk dashboard | ~1 session each | EV-16 needs composite v2 decision. | New screen / dashboard rows live |

### Operational fixes (found 2026-10-31, small sessions)

| # | Project | Size | Gate / first decision | OK-marker |
|---|---------|------|----------------------|-----------|
| A25 | **Memory-request pressure on 1 GB workers** | small session | `backfill-nse-deep` Pending; `FailedScheduling` for yields/rates/intraday/fx; pi1 at 95 % of memory requests. | Pending pod scheduled; no `FailedScheduling` events |
| A26 | **Re-fired one-shot backfill Jobs** | small session | `backfill-fx`, `history-fred`, `history-yields`, `llm-bakeoff` hit deadlines after AR-5 apply; idempotent so no data harm. | Stuck Jobs deleted / re-applied cleanly; no duplicates |
| A27 | **AR-2 `mdjob` adoption + first real-DB test** | small session | API fixed/tested; adopt per image at natural rebuilds (factors-fetcher is first adopter). | `mdjob.write_window` returns live conn after retry; test passes against real DB |
| A28 | **In-cluster backup staleness alert** (AR-11 remainder) | small session | Needs Grafana rule change or health-job rebuild; cron `pull-backups.py` runs meanwhile. | Stale-dump alert fires, verified |
| A29 | **Second nodewatch watcher host** (AR-12 remainder) | small session | pi0 is the only out-of-cluster alert path. Decide host + normally-closed relay. | Second host logs every 5 min and can page |
| A30 | **Per-user digest time + alert threshold** (telegram extension) | small session | Multi-user fan-out works; per-user settings don't exist. | `/settime` / `/setthreshold` per user, scoped to chat |
| A31 | **Branch/worktree per session** (AR-9 remainder) | small session | Practice change so WIP is never committed by the wrong session. | Each session uses its own worktree; CI green |

---

## B. Own project, gated on owner decision or hardware

| # | Project | Size | Gate |
|---|---------|------|------|
| B1 | **Off-site backup** (restic/rclone + job + docs, ~1 session) | ~1 session | Destination + encryption choice; dumps hold `bot_users` chat ids and k3s archive holds join token |
| B2 | **k3s control-plane restore drill** | ~1 session | Spare SSD + ~1 h at bench; agent drives §9 steps alongside |
| B3 | **pi0 DNS over tailnet / Pi-hole listening mode** | small session | CGNAT glance closes parent item, then a listening-mode decision |
| B4 | **Tailnet exposure of marketview + Grafana** | small session | Owner-run `tailscale serve` block (§1); agents won't widen exposure by design |
| B5 | **Marketview deep-links from bot card** | small session | Owner's exposure decision first |
| B6 | **Schedule timezone** (ADR 0002 revisit) | decision | Decide before 2026-11-01 |
| B7 | **Worker heat (pi1/pi2/pi3)** | hardware | Heatsinks/airflow fitted, then read next power-review WARN column |
| B8 | **ML-6 GBM ranker** | hard-gated | EV-0 + EV-1 + composite v2 pass EV §5; documented "no" is an acceptable outcome |
| B9 | **AR-10 control-plane host / DB-host vs GPU-host separation** | hardware | Purchase decision |

---

## C. Time-gated by the calendar

| # | Project | Due | Size | OK-marker |
|---|---------|-----|------|-----------|
| C1 | **ML-7b shortlist notes** | ~2026-11-02 | ~1 session | After 2 weeks of narrative notes; then ML-7b is ~1 session |
| C2 | **Story-cluster hand-check at 0.82** | ~2026-10-27 | ~1 h | Already passed clean 2026-10-29 (105 groups, 0 false merges) |
| C3 | **Grafana FII/sentiment re-tune** | ~2026-12 | ~1 h | 60 days of real distributions; all 4 rules health ok |
| C4 | **Yearly chunk merge** | 2027-01 | ops manual §11 | 2026 chunks merged per year; row counts identical |
| C5 | **EV-1 India half** (NSE reconstitution PDFs) | own session | ~1 session | India membership history backfilled; drift 0 |

---

## D. Multi-session architecture projects (v2+ target architecture)

| # | Project | Size | Gate / first decision |
|---|---------|------|----------------------|
| D1 | **v2+ §5–§6 sector-relative 6-component scoring + classifications** | multi-session | ADR 0026 sector lens is a partial version already live |
| D2 | **v2+ §13 style tags + investor lenses** | multi-session | Phase 3.5; DCF lens deferred to Phase 5 |
| D3 | **RTX 3090 AI layer (WoL)** | multi-session | LLM summaries at scale, embeddings, RAG (roadmap §7) |
| D4 | **GraphRAG** | multi-session | Only after entity extraction has reliable history |

---

## E. Deliberately not projects

### Per-natural-rebuild riders (~20 min inside an already-happening §6 cycle; never rebuild for these alone)

- AR-5 lock installs (14 images) · F401 import cleanups · per-job heartbeat v2 · telegram `stocks.py series()` onto the facts path · news-fetcher `timegm` · per-image `securityContext` (runAsNonRoot + drop caps) · microcap benchmark → `^CRSLDX` in predictions.BENCH + marketview BENCHMARK

### Icebox (§6 — deliberately declined; revisit triggers noted)

- JetPack 5 reflash · Longhorn/OpenEBS · Loki · Argo/Tekton · Citus (pi2 1 GB ceiling — ADR 0008) · 4th Pi worker · streaming ingestion · public-website/VPS front

---

## Sources

- `docs/pending-tasks.md` — sections 0, 1, 2, 4, 5, 6 (tracker of record)
- `docs/stock-screener-implementation-plan.md` §16 — prioritised implementation order
- `docs/evidence-based-signals-roadmap.md` — EV numbering
- `docs/ml-roadmap-2026-10-10.md` — ML numbering and labels
- `docs/market-data-pipeline-operations-manual.md` §8 runbook — cluster mutation rules for any session here

*When something here completes, record the ✅ in `docs/pending-tasks.md` first, then update the owning doc in the same session — the tracker and the docs must never disagree.*
