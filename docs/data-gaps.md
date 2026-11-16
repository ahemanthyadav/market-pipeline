# Data Gaps — single inventory

> **Purpose:** one authoritative list of every known data gap in the pipeline — what is missing,
> why, the impact, and the status. Maintained per AGENTS.md docs discipline: mark items ✅ / ⛔ /
> 🔶 / 🤔 / 🧊 with a date and source reference when the status changes.
>
> **Last verified: 2026-10-30** (live DB queries against `timescaledb-0`; all source scripts read at
> HEAD; cross-checked against `pending-tasks.md`, `ops-manual.md` §12, `architecture.md` §4.3a,
> `evidence-based-signals-roadmap.md`, `data-foundations-schema-draft.md`, and `finds-and-projects.md`).
>
> **Legend:** 🔶 open / needs work · ⛔ blocked (by what) · 🔄 recurring · 🤔 needs a decision ·
> 🧊 icebox (deliberately not doing) · ✅ closed (record kept for audit).

---

## How to read

Each gap has an ID (DG-01…), the table(s) affected, the current state, the root cause, the
consequence for research/backtesting, the status, and the next step. "Accepted" means the gap is
known and deliberately not fixed (hardware limit, no free source, or design choice) — it stays on
the list so nobody assumes it was overlooked.

---

## Open gaps — prioritised

### DG-01 — India historical index membership (the biggest open gap)

**Tables:** `universe_constituents` (NIFTY500 / NIFTYMICROCAP250), `index_membership` (India slice missing)

**State:** US side is complete — `membership_history.py` rebuilds point-in-time S&P 500 intervals from
Wikipedia's constituents + changes tables (891 intervals, drift 0, comprehensive ~2012→, selected
to 1976). India side does not exist.

**Root cause:** NSE reconstitution circulars are unstructured PDFs scattered across press pages.
No bulk machine-readable source identified (ADR 0025 evaluated S&P DJI press releases, CRSP/
Compustat (paid), scraping — all rejected or unaffordable for a homelab).

**Consequence:** India backtests (EV-0-style signal testing) suffer survivorship bias — identical to
the US problem before EV-1. `universe_membership_snapshots` accrues forward from 2026-10-18 only,
so the India *past* cannot be reconstructed.

**Status:** 🔶 Open — ~1 session for India (NSE PDF parsing). Tracked in `pending-tasks.md` §1
project row ("EV-1 historical index membership — India half deferred").

**Source:** `docs/adr/0025-historical-index-membership-from-wikipedia.md`,
`docs/pending-tasks.md` §1 project table.

---

### DG-02 — FII/DII history — forward-only

**Table:** `capital_flows` (FII/DII)

**State:** Starts 2026-09-29 and accrues daily. NSE's `fiidiiTradeReact` API serves exactly one day
(probed live 2026-10-20). No official NSE bulk archive exists.

**Alternatives considered:** third-party aggregator day-by-day pages (Stockezee/Upstox, no CSV)
and a static 2018–24 research dataset (Mendeley). Neither adopted.

**Consequence:** No historical FII/DII flow backtests possible until a source is adopted or forward
history accumulates over years.

**Status:** ✅ Closed (2026-10-26, owner decision: forward-only — no third-party backfill).

**Source:** `docs/pending-tasks.md` H-6; `docs/market-data-architecture.md` §4.3a ("partly — NSE
archive depth unverified").

---

### DG-03 — India NSE fundamentals backfill incomplete (~100 symbols + 13 dead URLs)

**Table:** `financial_facts` (source `nse_cons` / `nse_sa`), `stock_fundamentals`

**State:** `nse_fundamentals.py` backfill ran 2026-10-18→19 (357/495 symbols, 24,662 filings,
153,978 rows), hit `MAX_MINUTES` with cursor kept, resumed on RVNL.NS. ~100 symbols remain.

**Dead NSE archive URLs (~13 symbols):** HEGAM, COFORGE, ARE&M, KPIL, CEMPRO — return 404 on
NSE's side, logged WARN, skipped.

**Consequence:** India fundamentals coverage incomplete until backfill completes (`cursor cleared`);
`nse-fundamentals` CronJob stays suspended until then; India fundamental screens can't be trusted
until the DRY_RUN diff vs yfinance passes.

**Status:** ✅ Closed (2026-11-03) — `nse_fundamentals` cursor cleared 2026-11-01 03:22 UTC;
`nse-fundamentals` CronJob un-suspended (live + manifest) 2026-11-03; DRY_RUN diff vs yfinance
completed; EV-0 re-run completed across all 5 universes.

**Source:** `docs/pending-tasks.md` G-6 history backfills; `manifests/stock-screener/scripts/nse_fundamentals.py`.

---

### DG-04 — No per-stock second price source

**Table:** `stock_prices` (validation), DQ-5 `accuracy-check`

**State:** Stooq grew a JS challenge wall in Oct 2026 — the original per-stock second source for
check D is dead. FRED `SP500` index-level check exists (validates ^GSPC closes, 10/10 within 0.5%).
`accuracy_check.py` now includes `per_stock_second_source()` (deterministic weekly 25-stock S&P 500
rotation against Tiingo when `TIINGO_API_KEY` is configured).

**Consequence:** Code-ready; activates automatically once `TIINGO_API_KEY` is added to the job's environment.

**Status:** 🤔 Code-ready (2026-11-08) — awaiting owner-supplied free `TIINGO_API_KEY`.

**Source:** `manifests/stock-screener/scripts/accuracy_check.py` (check D comment),
`docs/pending-tasks.md` §1 (Stooq wall + FRED benchmark check live),
`docs/stock-screener-implementation-plan.md` §16 (FMP deferred).

---

### DG-05 — Multibagger screen backtest — Nifty impossible, S&P possible

**Tables:** `stock_prices`, `stock_fundamentals`, `financial_facts`

**State:** Nifty 500 has only ~4 annual Yahoo points per company with **no filing dates** — cannot
backtest point-in-time fundamental screens. S&P 500 has EDGAR history with filing dates since 2007
(~60 periods per symbol), so multibagger-style screens *can* be backtested there.

**Consequence:** Any India fundamental backtest (post-earnings drift EV-15 for India, etc.) is L
effort / blocked until NSE quarterly results feed with filing dates exists.

**Status:** 🔶 Structural — Nifty side blocked; S&P side testable today.

**Source:** `docs/evidence-based-signals-roadmap.md` Phase 2 (EV-15: India = L / US = M).

---

### DG-06 — Intraday coverage — 4 tickers only, ~2 years max

**Table:** `intraday_prices`

**State:** Holds 4 tickers since 2026-10-01. Yahoo keeps hourly bars ~2 years.

**Consequence:** Sparse intraday history, few symbols.

**Status:** ✅ Closed in code (2026-11-08) — `intraday-fetcher.py` expanded to 10 tickers (`GC=F`,
`SI=F`, `CL=F`, `DX-Y.NYB`, `^GSPC`, `^IXIC`, `^NSEI`, `^BSESN`, `^VIX`, `^INDIAVIX`).

**Source:** `docs/market-data-architecture.md` §4.3a; `docs/market-data-finds-and-projects.md` Tier 1 #5.

---

### DG-07 — `stock_metrics` not yet computed on `adj_close`

**Tables:** `stock_metrics_daily`, `stock_prices` (has own `adj_close`)

**State:** `stock_metrics.py` now reads `adj_close` from `stock_prices` and computes total-return
and risk series (`ret_1m`, `ret_6m`, `ret_12m`, `vol_180d`, `max_dd_252d`) on `adj_close` (falling
back to raw `close`), while keeping price-level metrics (`close`, `sma50_ratio`, `sma200_ratio`,
`pos_52w`, `dol_vol_20d`) on raw `close`.

**Consequence:** Momentum, volatility, and drawdown metrics are no longer distorted by dividends or splits.

**Status:** ✅ Closed in code (2026-11-08) — `stock_metrics.py` uses `adj_close` for return/risk series.

**Source:** `docs/pending-tasks.md` DQ-1 downstream note; `docs/adr/0014-own-total-return-adj-close.md`.

---

### DG-08 — `mdjob` / `job_runs` — single adopter

**Tables:** `job_runs`, `pipeline_heartbeat`

**State:** `job_runs` table + `manifests/_common/mdjob.py` helper exist (ADR 2 / AR-3 groundwork).
`factors-fetcher:v1` is the first adopter (first `job_runs` row: ok/7674). No other image uses it
yet. Grafana stale-feed rule can't read `job_runs` until adoption spreads.

**Consequence:** No durable per-job run record except `pipeline_heartbeat` (10 feeds, checked
01:45 UTC).

**Status:** 🔄 Adopt per image at each natural rebuild; then Grafana can read `job_runs`.

**Source:** `docs/pending-tasks.md` AR-2/AR-3 follow-ups; `manifests/_common/mdjob.py`.

---

### DG-09 — Nifty Auto / FMCG index dead on Yahoo

**Table:** `index_prices`

**State:** ^CNXAUTO / ^CNXFMCG / NIFTYAUTO.NS / NIFTYFMCG.NS are current-quote ghosts only (no
history). Nifty 500 restored via ^CRSLDX.

**Consequence:** No Auto/FMCG index price history in the pipeline.

**Status:** ✅ Closed (2026-10-26) — shipped in `nse-fetcher:v7` (paginated NSE direct API; series
live 2015→today).

**Source:** `docs/market-data-pipeline-operations-manual.md` §12 item 2;
`docs/market-data-finds-and-projects.md` Tier 2 #9, F11.

---

### DG-10 — `news_sentiment` / `raw_headlines` — no unique constraints

**Tables:** `news_sentiment`, `raw_headlines`

**State:** `11-news-constraints.sql` exists but unique constraints not applied (ops manual §12 item 5).
INSERT-only writers today.

**Consequence:** Duplicate headlines possible on re-run (24 same-headline pairs on different days
are feed republications and intentionally kept).

**Status:** ✅ Closed (2026-10-18) — `11-news-constraints.sql` applied; `ux_raw_headlines_key` and
`ux_news_sentiment_key` enforced and writers aligned.

**Source:** `docs/market-data-pipeline-operations-manual.md` §12 item 5.

---

### DG-11 — Gross profit sparse on EDGAR

**Table:** `financial_facts` (EDGAR leg), `EDGAR_MAP`

**State:** Many EDGAR filers skip the `GrossProfit` tag. `stock_fundamentals.py` now derives
`gross_profit = total_revenue - COGS` (`CostOfRevenue`, `CostOfGoodsAndServicesSold`,
`CostOfGoodsSold`) in both `edgar_fact_rows()` and `edgar_facts()` whenever `GrossProfit` is not
explicitly filed for a period.

**Consequence:** Gross profitability factor (EV-10) now covers US filers that report Revenue + COGS.

**Status:** ✅ Closed in code (2026-11-08) — `COGS_TAGS` fallback derivation added to `stock_fundamentals.py`.

**Source:** `docs/evidence-based-signals-roadmap.md` EV-9 note;
`manifests/stock-screener/scripts/stock_fundamentals.py` (EDGAR_MAP comments).

---

## Accepted gaps (by design — recorded so they are not forgotten)

### DG-12 — India G-Sec / repo rate history — forward-only

**Tables:** `interest_rates`, `bond_yields`

**State:** `rbi-fetcher` scrapes RBI's mobile "Current Rates" widget (current only, as-on T-1).
No stable machine-readable historical source for India G-Sec yields or repo rate history.

**Consequence:** India rates forward-only from 2026-10-09; US DFF holds 1954+.

**Status:** ✅ Accepted as closed gap (ops manual §12 item 1) — no source found.

**Source:** `docs/market-data-pipeline-operations-manual.md` §12 item 1;
`docs/market-data-architecture.md` §4.3a.

---

### DG-13 — FDI flows — manual quarterly INSERT by design

**Table:** (none dedicated — manual INSERT)

**State:** DPIIT FDI factsheets are PDFs. `fdi-watch` logs DPIIT links monthly → 5-min manual INSERT.

**Consequence:** FDI data sparse, manual, current-only.

**Status:** 🧊 Accepted by design — watchdog + quarterly manual INSERT.

**Source:** `docs/market-data-finds-and-projects.md` F14.

---

### DG-14 — US TIC mirror stat — deferred

**Table:** (none)

**State:** Listed in `architecture.md` as deferred; no fetcher or manifest references it.

**Consequence:** No Treasury International Capital data.

**Status:** 🤔 Deferred — no source identified.

**Source:** `docs/market-data-architecture.md` line 282.

---

### DG-15 — News / sentiment history — no backfill possible

**Tables:** `raw_headlines`, `news_sentiment`, `headline_entities`, `headline_events`

**State:** All start 2026-09-29 (pipeline go-live). RSS serves current items only. Moneycontrol ×2
and Reuters feeds died Oct 2026, replaced with LiveMint, BusinessLine, CNBC, MarketWatch — source
mix changed at that boundary, affecting sentiment comparability.

**Consequence:** No historical news backtests; sentiment has ~2-4 weeks of history.

**Status:** 🧊 Structural — forward-only.

**Source:** `docs/market-data-architecture.md` §4.3a;
`docs/market-data-finds-and-projects.md` F13.

---

### DG-16 — `commodity_prices` table — empty by design

**Table:** `commodity_prices`

**State:** Commodities (GC=F, SI=F, CL=F) ride in `index_prices` via the yfinance batch.
`commodity_prices` hypertable exists but is empty.

**Consequence:** No separate commodity pipeline; commodity data only as index tickers.

**Status:** ✅ Accepted by design.

**Source:** `docs/market-data-architecture.md` §4.3a; `docs/market-data-finds-and-projects.md` F15.

---

### DG-17 — India yfinance fundamentals — ~4 annual points, no filing dates

**Tables:** `stock_fundamentals` (India yfinance leg), `financial_facts` (yfinance values)

**State:** `stock_fundamentals.py` falls back to yfinance for India (no CIK). ~4 annual statements
per company, no filing dates.

**Consequence:** Point-in-time India fundamental signals cannot be built from Yahoo; require NSE
filings (G-6, DG-03).

**Status:** 🧊 Accepted — Yahoo limit.

**Source:** `docs/market-data-architecture.md` §4.3a.

---

### DG-18 — `universe_membership_snapshots` — no past

**Table:** `universe_membership_snapshots`

**State:** Appended weekly by `universe_refresh.py` (G-2a) from 2026-10-18 only. The past needs
EV-1 (historical index changes), which exists for US SP500 but not India (DG-01).

**Consequence:** Survivorship-free India backtests impossible until India EV-1 equivalent exists.

**Status:** 🔶 Blocked on DG-01 (India historical membership).

**Source:** `docs/market-data-architecture.md` §4.3a;
`docs/evidence-based-signals-roadmap.md` standing limitation.

---

### DG-19 — TX2 ML capacity ceiling — hardware limit

**Component:** TX2 (Pascal sm_62, CUDA 10.2, JetPack 4.6.1)

**State:** Modern PyTorch cannot use the GPU. Options: old L4T-era torch (1.10–1.12) or TensorRT
8.2.1 with ONNX. CPU-side modern torch works (2.14). JetPack 5 reflash is icebox.

**Consequence:** ML training/inference limited to CPU on TX2; RTX 3090 AI layer is a separate
Tier-3 project.

**Status:** 🧊 Icebox — hardware ceiling, not a code gap.

**Source:** `docs/market-data-finds-and-projects.md` §1.4.

---

### DG-20 — Cash & short-term investments (`cash_and_equivalents`) omitted by design (gross debt netted)

**Tables:** `financial_facts`, `stock_fundamentals`, `stock_valuations`

**State:** `stock_fundamentals.py` and `nse_fundamentals.py` ingest `total_debt`, `total_assets`,
`current_assets`, `current_liabilities`, and `shareholder_equity`, but deliberately do not ingest a
separate `cash_and_equivalents` metric. Per ADR 0026 and ADR 0030, enterprise-value valuation
methods (`epv`, `damodaran_fcff`, `ev_ebitda_peer`, `owner_earnings`) subtract gross `total_debt`
without crediting cash balances for conservatism.

**Consequence:** Cash-rich companies with large net-cash positions receive a conservative floor in
enterprise-to-equity bridges (`EV - Gross Debt` instead of `EV - (Gross Debt - Cash)`).

**Status (2026-11-12):** data half ✅ — `cash_and_equivalents` now ingested (EDGAR `CashCashEquivalentsAndShortTermInvestments` → `CashAndCashEquivalentsAtCarryingValue`; yfinance balance-sheet rows; `marketdata-data:v6`). Valuation still nets gross debt: the net-debt bridge is a 🤔 owner decision (ADR amendment). Original note: 🤔 Accepted conservatism (ADR 0026 / ADR 0030) — if exact `net_debt = total_debt - cash`
is desired, add `CashAndCashEquivalentsAtCarryingValue` / `CashCashEquivalentsAndShortTermInvestments`
to `EDGAR_MAP` in `stock_fundamentals.py` and cash tags in `nse_fundamentals.py`.

**Source:** `docs/adr/0026-intrinsic-valuation-and-sector-analysis.md`,
`docs/adr/0030-damodaran-fcff-and-graham-growth-valuations.md`,
`docs/adr/0033-classification-soundness-and-continuous-calibration.md`.

---

## Closed gaps (recently resolved — kept for audit)

| ID | Gap | Closed | How |
|----|-----|--------|-----|
| — | RBI G-Secs/repo current feed | 2026-10-09 | `rbi-fetcher` scrapes RBI widget |
| — | Nifty 500 price history | 2026-10-09 | Via Yahoo `^CRSLDX` |
| — | FX history | 2026-10-19 | H-1 backfill (58,222 rows, USDJPY 1996) |
| — | US bond yields history | 2026-10-19 | H-2 (DGS2/10/30 from 1962/1976/1977) |
| — | DFF history | 2026-10-19 | H-3 (1954-07-01, 26,390 rows) |
| — | Technical indicators history | 2026-10-19 | H-4 (2.6M rows, 38 tickers) |
| — | Stock metrics 5y weekly history | 2026-10-19 | H-5 (2.39M rows) |
| — | Corporate actions + own `adj_close` | 2026-10-18 | DQ-1 (58,896 dividends + 2,031 splits, 0 NULL adj_close) |
| — | DQ-4 timestamp normalization | 2026-10-13 | 4,434 duplicates removed; 6.32M rows re-stamped to 00:00 UTC |
| — | DQ-2 point-in-time fundamentals | 2026-10-18 | `financial_facts` keyed by duration + first filing date |
| — | EDGAR map extension (stale series) | 2026-10-20 | EV-9; stale count ~100 → ~35 |
| — | G-22 EDGAR gap (12 symbols) | 2026-10-20 | Fixed to 502/503; only VYLR remains (SEC-side) |
| — | F-4 `failed` NameError | 2026-10-09 | Fixed in v9 |
| — | Chunk merge | 2026-10-18 | stock_prices 3,382 → 107 chunks; 34s → 34ms planning |
| — | FRED SP500 second source | live | DQ-5 check D (10/10 within 0.5%) |
| DG-02 | FII/DII history | 2026-10-26 | Owner decision: forward-only |
| DG-03 | India NSE fundamentals backfill | 2026-11-03 | Cursor cleared, CronJob live, DRY_RUN diff done |
| DG-06 | Intraday ticker coverage | 2026-11-08 | Expanded from 4 to 10 tickers in `intraday-fetcher.py` |
| DG-07 | `stock_metrics` on `adj_close` | 2026-11-08 | `stock_metrics.py` uses `adj_close` for return/risk series |
| DG-11 | EDGAR gross profit sparsity | 2026-11-08 | `COGS_TAGS` fallback (`total_revenue - COGS`) in `stock_fundamentals.py` |

---

## Priority ranking for closing open gaps

| Rank | ID | Gap | Effort | Blocks |
|------|-----|-----|--------|--------|
| 1 | DG-01 | India historical index membership | ~1 session (NSE PDFs) | India EV-0 backtests (`DG-18`) |
| 2 | DG-04 | Per-stock second price source | free `TIINGO_API_KEY` (code-ready) | DQ-5 per-stock validation |
| 3 | DG-05 | Nifty multibagger point-in-time backtest | NSE quarterly filing dates | India fundamental backtests (US S&P unblocked via `EV-19`/`EV-20`) |
| 4 | DG-20 | `cash_and_equivalents` for net debt | ~30 min (`EDGAR_MAP` + NSE tag) | Net-cash credit in EV valuations (currently gross debt by design) |
| 5 | DG-08 | `mdjob` / `job_runs` adoption | per natural image rebuild | Grafana `job_runs` stale-feed rule |

---

## Cross-references

- `docs/adr/0025-historical-index-membership-from-wikipedia.md` — US point-in-time membership decision; India deferred
- `docs/adr/0029-universe-expansion-sp1500-nifty-total-market.md` — universe expansion, benchmarks, backfill order
- `docs/adr/0014-own-total-return-adj-close.md` — own total-return close
- `docs/adr/0013-stock-prices-trade-date-timestamps.md` — 00:00 UTC trade-date timestamps
- `docs/adr/0015-point-in-time-fundamentals.md` — financial_facts duration-keyed, first-filing
- `docs/market-data-pipeline-operations-manual.md` §12 — known gaps & TODOs (operational)
- `docs/market-data-architecture.md` §4.3a — historical coverage table per table
- `docs/evidence-based-signals-roadmap.md` — EV items, standing survivorship limitation
- `docs/stock-screener-implementation-plan.md` §16 — prioritised gap list (value × effort)
- `docs/market-data-finds-and-projects.md` — finds F1–F34, tiered project list
- `docs/data-foundations-schema-draft-2026-10-10.md` — F-1…F-6 original gap findings (mostly closed)
- `docs/pending-tasks.md` — single tracker of open items with dates and owners
