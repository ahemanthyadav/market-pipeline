# Pending Tasks — the single tracker

> **Purpose:** one authoritative list of everything not yet done. Detail lives in
> the linked docs — this file is the index + status. Maintained per AGENTS.md docs
> discipline: mark items ✅ with a date (and where the completion is recorded) when
> done; prune ✅ lines into the relevant doc's history at housekeeping time.
> **Last reviewed: 2026-11-08** — recovery session: restored `marketdata-data:v1` + `marketdata-light:v1` to pi1/pi3 containerd
> stores (all fetchers had `ErrImageNeverPull` since the 10-31 TX2 prune; `pipeline_heartbeat` frozen at 2026-11-04), forced
> backfill of every active fetcher, data current through Fri 2026-11-06 (markets closed weekend). Built and deployed
> `marketdata-data:v2` and `telegram-digest:v39` to pi1/pi3. Orin2 cordoned pending image import; orin1 tainted (STT/TTS only).
> Earlier **2026-10-30** (owner: "deploy all changes + create a deployment guide"): telegram
> **v34 live** on pi1 (bot deployment + digest/alerts CronJobs; rollout clean, live specs verified). v33
> **burned, never deployed** — built minutes before 36e6529 landed, in-image md5 caught stale
> stocks.py/bot.py; the §6 cycle now carries step 3b (verify image contents vs HEAD) and the new
> `docs/deployment-guide.md` is the start-to-finish wrapper (indexed in README). Tarball archive at 79
> images, SHA256SUMS regenerated. No digest canary by policy (off-schedule fan-out to members); owner-side
> `/sp500` is the functional check for the v34 features (index quant values + /sp500).
> Earlier **2026-10-26** (deploy + backlog pass — owner said "deploy all" then "implement all"):
> staged hardening applied · telegram v24 live (probe + non-root canary PASSED) · host keys pinned · H-6
> decided forward-only · AR-8 ⛔ blocked on an owner-run Secret/role creation (§1) · netpol finding: TX2
> kernel has no ipset → unenforced on the DB's node (header documents it) · **EV-9 done** (`screener:v17`→
> v18 same day) · **Auto/FMCG + MIDCAP150 done** (`nse-fetcher:v7`; NSE caps 70 records/call — paginated)
> · **G-20 done** (`factors-fetcher:v1`, first mdjob adopter) · **EV-1 US done**
> (ADR 0025, schema 14, `membership-history` CronJob — US signal work unblocked; India deferred, PDFs).
> House-prescribed next: the EV-0 §5 re-run on point-in-time membership (own session, pair review).
> **Later same day (owner: "get all gaps covered / all famous calculations / sector info"):** the ADR 0026
> valuation layer shipped as `screener:v20` — schemas 15/16/17 applied (`schema-valuation-17` Job), daily
> `stock-valuation` CronJob (the 29th, 02:10): 10-method intrinsic panel (Graham, peer multiple, FCF
> perpetuity, 2-stage DCF, DDM, EPV, residual income, Lynch PEG) for every active symbol →
> `stock_valuations` (canary: 998 symbols, 983 with a fair value), sector→style relevance tables (31
> sectors, 64 weighted metrics with reasons) driving method selection, `sector_metrics` daily aggregates,
> and stock-report now renders sector lens + fair-value panel + "what matters in this sector" + a
> `__sectors__` analysis row (canary: 50 reports + 31 sector rows). v19 burned on a column-name bug
> (stock_prices.symbol), caught by canary. DDM coverage and reported share counts complete with the
> 10-31 EV-9 weekly pass. ALL of it descriptive — composite untouched (ADR 0026 hard rule). Earlier **2026-10-25** (full-project review pass, 3-agent + synthesis; fixes implemented same session:
> pre-commit `@@PW@@` pattern-bypass closed (+2 hook test cases) · `tests/run-all.sh` single entrypoint (AGENTS/CI/CLAUDE.md all point at it) ·
> `mdjob.write_window` now returns the live conn after a retry (+`tests/test_mdjob.py`, 8 tests) · ops-tools: labeled runcmd timeout (rc 5),
> getfile/putfile error handling + exit codes, optional `.ssh_known_hosts` pinning, `_creds` unquoted-whitespace warning, `ev0 --start`
> ISO-date validation, nodewatch flock + token via curl stdin · test coverage: 8 small fetchers + alerts.py + power-review verdict
> (3 new test files, 55 checks) · rule-9 bounds in bot /fii + digest headlines/flows · HTML-safe `trim_html` (bot/digest/alerts) ·
> bot getMe retry + /tmp/bot-alive heartbeat · news-fetcher `time.mktime`→`calendar.timegm` (UTC bug latent off-cluster) · CI pip pins ·
> doc staleness (README git/ADR-count/v16, telegram doc header, schema-job + grafana comments). STAGED, NOT APPLIED → see the three
> new §1 items: manifest hardening apply, telegram v24 rebuild, AR-8 writer role. Earlier **2026-10-20 06:05 UTC** (completion pass: G-22 verified closed (502/503, only VYLR) · EV-9 EDGAR_MAP extended,
> `screener:v16` live, verification pass running · story threshold tuned to 0.82 (ADR 0024 amended) · H-6 depth checked (NSE serves
> 1 day — owner decision) · Auto/FMCG + MIDCAP150 NSE-direct avenue verified to 2015 · telegram v23: /ask carries the 52-week range.
> Earlier 04:20 UTC: **power acceptance PASSED on all four nodes — the pi1 item is closed**; **pi2 rebooted again ~01:56 UTC 10-20**,
> cause unknown, 0 undervolt. Earlier: **2026-10-18 13:10 UTC** (engineering pass done — see §1 first two items). Earlier: **2026-10-18 07:50 UTC** (`screener:v11` live — corporate_actions recording, canary OK; first k3s control-plane backup;
> site-wide power event 10-18 ~06:00 UTC explained — all 5 machines rebooted, 0 undervolt; pi1 formal 24 h window due 10-19 06:20 UTC;
> DQ-3 proven via DB; 10-17 overclaims on DQ-2/AR-2/AR-5 corrected back to open). Previously **2026-10-14 06:49 UTC** (power item **closed as hardware-complete** — owner confirmed pi1 on its own
> supply; live read 06:49 UTC: all four nodes PASS, `dmesg` undervoltage counts 0 on every Pi, pi2 heat WARN only;
> **pi1's 24 h acceptance still open, due ≥ 2026-10-15 05:40 UTC** — see §1. Previously 2026-10-14 06:40 UTC:
> `screener:v10` deployed incl. pi1 — clean under the import burst, DQ-1 split resync live, missed digest refired;
> pi1 hung 02:02 UTC, power-cycled, now on its own supply — new 24 h trial in §1; previously 2026-10-13 04:55 UTC: final power review = pi1 FAIL; DQ-4 repaired, CTVA repaired, stale-feed noise fixed; earlier 2026-10-12 maintenance pass: scheduled runs verified, Grafana alerts retuned and applied, data-quality findings DQ-1/DQ-4/G-22, power review interim = pi1 FAIL; see §1. Previously 2026-10-10: the 2026-10-09→10 outage —
> node-local stats logging deployed per ADR 0011; screener v8 fixes + first
> full-quality run verified 2026-10-09; Grafana screener dashboard live).

Legend: 🔶 pending · ⛔ blocked (by what) · 🔄 recurring · 🤔 needs a decision ·
🧊 icebox (deliberately not doing — kept visible so it stays deliberate)

---

## 0. What completing each open item takes (index, 2026-10-20)

Every open item below, sorted by what unblocks it. "Agent" = any work session can do it unaided once the condition is met.
Detail stays with each item; this is the decision view — keep it in sync when items close.

**Gated on a running job (agent, automatic):**
| Item | Unblock | Then |
|---|---|---|
| ~~G-6 NSE backfill follow-ups~~ | ✅ `nse_fundamentals` cursor cleared 2026-11-01 03:22 UTC; CronJob un-suspended (live + manifest) 2026-11-03; DRY_RUN diff done (facts vs yfinance — different top-50 rankings, same counts: SP500 503/452, SP600 599/539); EV-0 re-run done (nothing passes §5, both universes required); §4.3a coverage updated (5 universes: SP500/SP400/SP600/NIFTY500/NIFTYMICROCAP250) |
| ~~EV-9 verification~~ | ✅ `fundamentals-ev9` completed; stale-count query = 0 stale series / 0 stale symbols 2026-11-03 | tracker tick ✅ |

**Agent-ready, needs only a calm cluster window:**
| Item | What | Effort |
|---|---|---|
| ~~Staged manifest hardening apply~~ | ✅ applied 2026-10-26 (netpol: TX2 kernel lacks ipset — documented no-op for the DB) | done |
| ~~Telegram v24 rebuild~~ | ✅ live 2026-10-26 incl. probe + non-root canary | done |
| AR-8 PGUSER flip | after the owner runs the two §1 commands (Secret + role) | ~30 min |

**Owner hands/decision first — then mostly minutes, not sessions:**
| Item | What it takes from the owner | Agent part after |
|---|---|---|
| Worker heat (pi1/pi2/pi3) | buy + fit heatsinks/fan (pi2 case already off) | read the next power-review WARN column |
| ~~Persistent journald on pi1/pi2~~ | ✅ owner said yes; applied 2026-10-20 (ops manual §8: the RPi `40-rpi-volatile-storage.conf` gotcha) | done |
| ~~Router UDP 51820 forward~~ | ✅ owner checked 2026-10-20: forwarding page EMPTY — no rule exists, closed | done |
| CGNAT claim | ✅ agent-verified 2026-10-20 (NOT CGNAT); one router-WAN-page glance closes it | unblocks pi0 tailnet DNS (~30 min agent work) |
| Phone check of bot commands | 10 min on the phone (incl. `/ask TCS 52 week high`) | fix any layout breakage found |
| Who messaged the bot | confirm the chat id is `sailu` (2 min) | tracker tick |
| Off-site backup | pick destination + encryption, create the account/credentials | build the restic/rclone job + docs (~1 session) |
| k3s restore drill | a spare SSD + ~1 h at the bench | agent drives the §9 steps alongside |
| ~~H-6 FII/DII history~~ | ✅ owner decided 2026-10-26: **forward-only** — no third-party backfill; item closed | done |

**Time-gated — the calendar does the work (agent acts at the date):**
| Item | Due | Effort then |
|---|---|---|
| ~~Story-cluster hand-check at 0.82~~ | ✅ done 2026-10-29 — 105 multi-headline groups checked, all genuine merges | ~1 h review |
| ~~ML-7a narrative watch → ML-7b~~ | ✅ done 2026-11-03 — 17 runs, shortlist notes in out/ml-7b-shortlist-notes.md | review |
| ~~EV-0 §5 re-run on point-in-time membership~~ | ✅ done 2026-11-04 — SP500 uses point-in-time membership (101,931 symbol-months), nothing new passes §5, composite unchanged | 1 session |
| ~~Price-audit retirement~~ | ✅ repair done 2026-11-04 — audit now clean (0 mismatches); needs 4 clean weekly runs (~2026-11-25) | remove one cron line + tracker |
| Grafana FII/sentiment re-tune | ⏸ time-blocked (~2026-12, needs 60 d of history) | ~1 h with real distributions |
| Yearly chunk merge | ⏸ time-blocked (2027-01) | ops manual §11 procedure |

**Per-natural-rebuild (zero marginal cost when an image next changes anyway; never rebuild for these alone):**
AR-2 `mdjob` adoption (API fixed + tested 2026-10-25 — ready to adopt) · AR-5 lock installs (14 images) ·
F401 import cleanups · per-job heartbeat v2 · telegram `stocks.py` `series()` onto the facts path ·
news-fetcher `timegm` fix (in repo 2026-10-25; identical behavior in-cluster, so next natural news rebuild) ·
per-image `securityContext` (runAsNonRoot + drop caps — **telegram canary PASSED with v24 2026-10-26**; roll out to the
other images per natural rebuild, news next) ·
~~telegram v25 riders~~ → **v25 SHIPPED 2026-10-26**: valuation-watch buy alert (ADR 0026 amendment,
owner-approved — entering names, reasoning in the line, ≤5/run; first live lines land with the next
02:45 alerts run), `/sectors` command, fair-value line on `/stock` (16 new tests, 120 in the file;
no-send validation green). Still queued for v26: `stocks.py series()` onto the facts path ·
marketview deep-links from the card (needs the owner's exposure decision first).
Each is ~20 min *inside* an already-happening §6 cycle.
**Done in-repo, rides the next telegram-digest §6 cycle:** `/valuation <symbol>` command
(stocks.py `valuation_text`, bot.py `cmd_valuation`, router/nicknames wiring, 6 new tests in
tests/test_stocks.py) — shows all 15 intrinsic valuation methods + fair value/upside for a stock.
**Also done in-repo:** `/calibration` (stocks.py `calibration_text`, reads `v_latest_calibration`,
factor ICs / archetype returns / MB gate margins / shrunk weights) and `/methods`
(stocks.py `methods_text`, reads `v_valuation_methods`, 34-row catalog with formulas + sources).
Both wired into bot.py COMMANDS + MENU, router SAFE_AI_COMMANDS, and nicknames.

**Projects — each needs its own session(s) plus a decision or ADR (order per §16 priorities):**
| Project | Size | Gate / first decision |
|---|---|---|
| **EV-1 historical index membership** | ✅ US done 2026-10-26 (ADR 0025, `index_membership` live, drift 0) — US signal work UNBLOCKED; 🔶 India half deferred (NSE PDFs, own session) | India: ~1 session |
| ~~**UI / Analysis Explorer**~~ | ✅ BOTH PHASES LIVE 2026-10-26: 4 new Grafana dashboards (24 queries validated as grafana_ro pre-apply; 11 total) + the **marketview** web app in its own repo (ADR 0027, `local/marketview:v4` on pi1, all routes canaried 200, bad input 404s) — port-forward exposure; NodePort/`tailscale serve` = 🤔 owner choice | done |
| ~~EV-9 (roadmap half): more statement lines~~ | ✅ done 2026-10-26, `screener:v17` live, canary 3/3; universe fills on the 10-31 weekly run | done |
| EV-10…14 → EV-17/18 composite v2 | 2+ sessions | data gates CLEARED 2026-10-26 (EV-1 US + EV-9 live); next = EV-0 §5 re-run on point-in-time membership, then per-universe rules = owner |
| ~~G-20 factor returns~~ | ✅ done 2026-10-26 (`factors-fetcher:v1` live, terms verified; EV-0 §5 test-4 extension rides the next scoring change) | done |
| G-21 extra signals | ✅ Amihud/max_daily_ret/idio_vol + industry_momentum added to `ev0.py` + EV-0 §5 tested ✅; net payout yield, distress score need fundamentals/sector data | each must pass EV-0 §5 |
| **Orin Nano STT/TTS voice node** | ~2–3 sessions | Add Orin Nano as k3s worker (taint `hardware=orin`), build `stt-service`/`tts-service` images on TX2, deploy via §6 cycle, wire `bot.py` to voice messages (docs: `docs/orin-nano-stt-tts-runbook.md`) |
| ~~Auto/FMCG + MIDCAP150 fetcher~~ | ✅ done 2026-10-26 (`nse-fetcher:v7`, series live 2015→today) | done |
| ~~ML-0 experiment infra~~ | ✅ done 2026-11-04 — `ops-tools/ml0.py` (walk-forward splitter, leakage test, trial budget, `ml_runs` tracking via `app.ml_runs`); tested end-to-end with ret_12m across 5 universes | ~1 session |
| ~~ML-1 anomaly v1~~ | ✅ done 2026-11-04 — `ops-tools/ml1.py` (bad_bar, feed_drop, row_count_drop, scraper_drop rules; writes to `app.data_anomalies` idempotently; `tests/test_ml1.py` 11 tests all pass) | ~1 session |
| ~~ML-3 vol / ML-5 peers / ML-4 regime~~ | ✅ ML-3 done 2026-11-04 (`ops-tools/ml3.py` EWMA/GARCH/GJR-GARCH → `app.vol_forecasts`; `tests/test_ml3.py` 8 tests) | ✅ ML-5 done 2026-11-04 (`ops-tools/ml5.py` correlation clusters → `app.peer_clusters`; `tests/test_ml5.py` 4 tests) | ML-4 descriptive-only until EV-0 shows value |
| ~~ML-7c story summaries~~ | ✅ done 2026-11-04 (`ops-tools/ml7c.py` LLM summaries → `app.llm_outputs` kind=story_summary; `tests/test_ml7c.py` 3 tests) | needs gateway to run live |
| ML-6 GBM ranker | gated | hard gate: EV-0 + EV-1 + composite v2 pass §5 |
| v2+ architecture (§4) / icebox (§6) | multi-session | deliberately later / deliberately never |

---

## 1. Immediate (next checkpoint: tomorrow's `/health`)

- [ ] 🔶 **Roll out ADR 0033 & Schema 25 (`25-valuation-and-metrics-catalog.sql`) to the cluster (§6 cycle):**
  Code and hermetic unit tests are committed across `marketdata` and `marketview`:
  1. **Apply TimescaleDB Schema 25:** `kubectl apply -f manifests/timescaledb/schema-configmap.yaml` and run the schema job (or trigger one `stock-valuation` job, as `valuation.ensure_schema_and_catalog()` idempotently creates `valuation_methods`, `stock_scientific_metrics`, `calibration_metrics`, additive columns on `stock_valuations` and `sector_metrics`, contract views `v_valuation_methods`, `v_latest_scientific_metrics`, `v_latest_calibration`, and grants `SELECT` to `grafana_ro` and `telegram_app`).
  2. **Update `stock-screener` scripts/image:** Apply the updated `stock-screener` ConfigMap (`valuation.py` 15-method engine + 34-row catalog + `scientific_row()`, `stock_metrics.py` `adj_close` total-return series + `ret_12_1` + `near_high`, `stock_fundamentals.py` `COGS_TAGS` fallback for `gross_profit`, `stock_report.py` dual-lens report) and run a canary `stock-valuation` job.
  3. **Build & roll out `local/telegram-digest:v40`:** Build on TX2, archive tarball to `/mnt/tx2-ssd/image-tarballs/telegram-digest-v40.tar.gz`, import on `pi1`, and apply `manifests/telegram/deployment.yaml` + `manifests/telegram/cronjob.yaml`. Fixes `/valuation` `UndefinedColumn` on `ev_ebitda_peer` — degrades gracefully until Schema 25 is applied.
  4. **Build & roll out `local/marketview:v13`:** Bump `deploy/deployment.yaml` to `local/marketview:v13`, build on TX2, import on `pi1`, roll out, and canary `/stock/AAPL`, `/multibagger`, and `/strategies`.
  5. **Run continuous calibration loop (`ops-tools/classifier_loop.py`):** Run `python3 ops-tools/classifier_loop.py run --horizon 91` from the workstation once `prediction_outcomes` has ≥6 resolved cross-sections to populate `__calibration__` in `stock_reports` and structured rows in `calibration_metrics`.
- [ ] 🔶 **MarketView UI surface extensions for Schema 25 tables (follow-up):**
  `/stock/{symbol}`, `/multibagger`, `/strategies`, and `/reports/__calibration__` already render the dual-lens models; three optional UI enhancements remain:
  1. **`/sectors` (`sectors.html` + `SQLS["sectors"]` in `marketview/app/db.py`):** Expose the 4 new `sector_metrics` columns (`median_practitioner_upside_pct`, `median_academic_upside_pct`, `median_gpa`, `median_f_score`) alongside `median_upside_pct`.
  2. **`/scorecard` (`scorecard.html`):** Render a structured calibration table directly from `v_latest_calibration` (`calibration_metrics`: Factor Rank ICs, Archetype Cohort Excess Returns, Multibagger Gate Marginal Value GMV, and Shrinkage-Proposed Factor Weights) in addition to the `/reports/__calibration__` markdown link.
  3. **34-Row Method & Metric Reference Drawer (`v_valuation_methods`):** Render the full 34-row `valuation_methods` catalog (15 valuations, 8 scientific diagnostics, 5 factor pillars, 2 composites, 2 archetype taxonomies, 2 multibagger screens with formulas and peer-reviewed sources) on `/strategies` or `/scorecard`.
- [ ] 🔶 **Open Data & Accounting Enhancements (`DG-20`, `EV-19`, `EV-20`):**
  1. **Cash & Short-Term Investments (`cash_and_equivalents`, `DG-20`):** Add `CashAndCashEquivalentsAtCarryingValue` / `CashCashEquivalentsAndShortTermInvestments` to `EDGAR_MAP` in `stock_fundamentals.py` and `nse_fundamentals.py` if exact `net_debt = total_debt - cash` is desired for `ev_ebitda_peer`, `damodaran_fcff`, `epv`, and `owner_earnings` (currently uses gross `total_debt` without cash credit by design per ADR 0026 / ADR 0030).
  2. **Post-Earnings-Announcement Drift (`EV-19` PEAD / SUE):** Compute standardized quarterly EPS surprise (`SUE = (EPS_q - EPS_{q-4}) / sigma`) on the filing date using `earnings_calendar` (Schema 19) + quarterly `financial_facts` (`duration_days BETWEEN 75 AND 105`), and test against `ev0.py` §5 acceptance rules.
  3. **Point-in-Time Fundamental Panel Backtest (`EV-20`):** Reconstruct historical Value and Quality factor sleeves at each month-end from first-filed `financial_facts` in `ops-tools/ev0.py` to test fundamental sleeves with the same Newey-West / Fama-MacBeth rigor as price signals.
- [ ] 🔑 **OWNER-RUN: tailnet exposure of marketview + Grafana** (feature wave #3, ADR 0028 §6 — the
  agent permission system refuses to widen network exposure, by design). pi1 is on the tailnet.
  From the workstation (resolves the ClusterIPs, then configures serve on pi1 over ssh):
  ```bash
  export KUBECONFIG=~/.kube/pi-cluster.yaml
  MV=$(kubectl -n market-data get svc marketview -o jsonpath='{.spec.clusterIP}')
  GF=$(kubectl -n market-data get svc grafana    -o jsonpath='{.spec.clusterIP}')
  ssh pi1@pi1 "sudo tailscale serve --bg --https=443  http://$MV:8080 && \
               sudo tailscale serve --bg --https=8443 http://$GF:3000"
  ```
  Then `https://pi1.<tailnet>.ts.net/` (marketview) and `:8443` (Grafana) from any tailnet device.
  If `serve` asks for HTTPS certs, enable MagicDNS + HTTPS in the Tailscale admin console first.
  ClusterIPs survive pod restarts but not Service recreation — re-run the block if a svc is ever
  recreated (`tailscale serve status` shows the config; `tailscale serve reset` clears it).
- [ ] 🔶 **Complete orin2 image import**: orin2 joined 2026-11-06 with zero local/* images; cordoned pending import.
  orin2 joined 2026-11-06 with zero local/* images; cordoned pending import. Find SSH username (check `.hosts.env`), then:
  1. ssh-keyscan orin2 >> .ssh_known_hosts (repo copy)
  2. python3 ops-tools/getfile.py tx2 <user> /home/<user>/marketdata-data-v1.tar.gz /tmp/marketdata-data-v1.tar.gz
  3. python3 ops-tools/putfile.py orin2 <user> /tmp/marketdata-data-v1.tar.gz /home/<user>/marketdata-data-v1.tar.gz
  4. python3 ops-tools/runcmd.py orin2 <user> "echo @@PW@@ | sudo -S /usr/local/bin/k3s ctr -n k8s.io images import /home/<user>/marketdata-data-v1.tar.gz"
  5. Optionally import marketdata-light-v1.tar.gz too.
  6. kubectl uncordon orin2 — verify a one-off job lands on orin2 and Completes.
  Skip orin1 (tainted gpu=orin:NoSchedule, STT/TTS voice only — no market-data images).

- [ ] 🔄 2026-10-27 **Universe expansion IN FLIGHT (ADR 0029): S&P 1500 + Nifty Total Market = 2,251
  names** (`screener:v25`, `yfinance-fetcher:v6` with ^SP400/^SP600/NIFTY_MICROCAP250.NS benchmarks,
  `telegram-digest:v27` universe aliases, `marketview:v5` benchmark map). Fill-in sequence: (1) ✅ universe-refresh → 2,251 active; (2) 🔄 corporate-actions backfill RUNNING (cursor at ABFRL.NS, all 2,248 symbols); (3) ✅ prices backfill COMPLETE 2026-10-28: 1,276/1,289 symbols, 7.2M rows, 5.56M adj_closes, 0 anomalies — ⚠ those adj_closes were computed while (2) was still running; the original ordering invariant (CA before adj_close, so it computes right) means a symbol's adj_close is only trustworthy once (2)'s cursor has passed it (the backfill runs `recompute_adj_close()` per symbol), and the full set only when (2) ends with `cursor cleared`; (4) 🔄 stock-fundamentals backfill RUNNING (cursor at AARTIPHARM.NS, all 2,251 symbols — started early 2026-10-29 ahead of the 10-31 Saturday cadence); (5) ⏳ daily chain (metrics → scores → valuations → reports → predictions) covers new universes once fundamentals land. Mark ✅ when `/screener?universe=SP600` shows ranked rows with valuations. ⚠ Deferred into the NEXT screener/marketview image cycles: microcap benchmark → ^CRSLDX in predictions.BENCH + marketview BENCHMARK. Full-market (~8–11k) deliberately NOT planned.

  **2026-11-08 addition:** forced backfill of all active fetchers ran after image recovery (prices/funds/TA/screener/report/valuation all Completed on pi3); NSE-fundamentals and stock-fundamentals backfills skipped due to NSE rate-limiting and 2251-symbol duration — both will complete on their regular schedules. Data current through Fri 2026-11-06.
- [x] ✅ 2026-10-27 **Feature wave 1–8 deployed (ADR 0028):** prediction scorecard (schema 18,
  `prediction-tracker` 02:40 daily — first canary wrote 1,419 snapshots; outcomes from 2026-11-26),
  earnings calendar (schema 19, weekly Sunday; canary 40 symbols → 25 dates; AAPL Mon 02 Nov on the
  dossier), DQ-5 `accuracy-check` weekly (first full run **ALL CLEAN**: 983 valuation rows 0 bad
  medians/upsides, sector medians 0 off with the engine-mirrored filter, membership drift 0, FRED
  SP500 vs our ^GSPC 10/10 within 0.5%), regime traffic light (digest + /regime + marketview home),
  telegram v26 (earnings digest section + 3-day earnings gate on the valuation watch; no-send
  validated), marketview v4 (/watchlist, /scorecard, dossier badges + closest-mcap peers + earnings
  badge). 32 CronJobs; screener v24 on all 4 nodes. Stooq grew a JS challenge wall → per-stock
  second source needs a free API key (Finnhub/Tiingo) = 🤔 owner choice; FRED benchmark check live.

- [x] ✅ 2026-10-26 **Staged manifest hardening APPLIED** (owner said "deploy all"; cluster-mutation discipline,
  one change verified at a time): grafana probes live (new pod healthy, `/api/health` ok) → all 26 CronJobs
  configured (spec-only; `nse-fundamentals` suspend state preserved) → DB StatefulSet applied in the
  05:11–05:30 UTC gap between intraday and the morning fetch block (pod back in 22 s, `pg_isready` +
  bounded query verified) → netpol applied. **Netpol finding:** NOT enforced on the TX2 — L4T kernel
  `4.9.337-tegra` ships no ipset modules (kube-router hard dep); chains verified present on pi1/pi2/pi3,
  absent on TX2, and the DB is pinned to TX2, so the policy is a documented no-op for the DB until the
  kernel changes or the DB moves (header updated; cross-ns `nc` to :5432 still connects). DB guard remains
  Postgres auth + AR-8 roles. Kept applied — zero cost, activates if placement ever changes.
- [x] ✅ 2026-10-26 **Telegram v24 LIVE (§6 cycle run end-to-end):** built on TX2, tarball archived to
  `/mnt/tx2-ssd/image-tarballs/` + `SHA256SUMS.all` regenerated, imported on pi1, deployment applied WITH
  the liveness probe + **non-root securityContext (per-image canary — PASSED: uid 1000, caps dropped)**;
  bot polling (owner + 2 members), heartbeat 21 s fresh; cronjobs on v24; no-send validation job ran
  `digest.build_message()` against the live DB → `DIGEST-BUILD-OK 873 chars`. First scheduled v24
  digest/alerts: 2026-10-27 cycle — glance at `ALERTS-OK`/`DIGEST-DONE` markers.
- [x] ✅ **AR-8 completion: writer role `pipeline_rw` — DONE 2026-10-30.** Secret `pipeline-db-rw`
  created and role `pipeline_rw` granted (SELECT/INSERT/UPDATE/DELETE on all public tables +
  sequences, default privileges). Flipped the pipeline-fetcher manifests' `PGUSER: postgres` →
  `pipeline_rw` and `timescaledb-auth` → `pipeline-db-rw` (pg-backup, schema-job, restore-drill,
  statefulset stay on `postgres`). **Write path verified 2026-11-03:** INSERT/DELETE into `job_runs`
  as `pipeline_rw` succeeded (`AR-8 write path OK`). `ops-tools/workloads.py --check` passes and
  `bash tests/run-all.sh` is all green.
- [x] ✅ 2026-10-31 **TX2 image store cleaned to the consolidated set** (owner: "remove remaining old images"). Pre-AR-5 tags
  removed from TX2's docker build store one name at a time (never a prune-all): 78 `local/*` images → 3, plus 24 dangling build
  layers and the base images (`python:3.11-slim`, `python:3.12-slim`, `ghcr.io/berriai/litellm-database:main-stable`); 4 stale
  build containers removed first (they blocked the dangling rmi). 7.5 GB → 3.1 GB. Tarball archive pruned to the 6 live images
  (`marketdata-light-v1`, `marketdata-data-v1`, `marketdata-ml-v1`, `litellm-database-1.106.0`, `marketview-v12`,
  `telegram-digest-v34`) — checksums verified `OK` before deleting anything, `SHA256SUMS.all` regenerated and re-verified,
  `SHA256SUMS.pi-only-images` retired; 7.0 GB → 1.4 GB, SSD 21 % → 14 %. TX2's k3s containerd store untouched (light + ml +
  litellm + timescaledb + system images); TX2 schedules only `headline-ner` (light) and `sentiment-scorer` (ml) via the
  `gpu=true` taint, and both completed on schedule after the change. Note for the next §6 cycle: `marketdata-data:v1` and the
  litellm base must be re-pulled/rebuilt on TX2 — the tarballs are the recovery path. Detail: ops manual §6.
- [x] ✅ 2026-10-26 **SSH host keys pinned:** all 5 nodes ssh-keyscanned into gitignored `.ssh_known_hosts`
  (15 keys, 5 hosts); `_creds.host_key_policy` now RejectPolicy; runcmd verified against every node after.

> **Restored 2026-10-12:** commit `c455687` dropped the power-fix item, §2 (Security & hygiene), the `nodewatch` and
> stats-logger records from this file. They are back below (power item and `nodewatch` here, §2 before §4), brought up to date
> from `a4b9567` + the 2026-10-11/12 sessions. Check `git show a4b9567:docs/pending-tasks.md` if anything else looks missing.

- [x] ✅ 2026-10-18 **Maintenance pass (07:00–09:40 UTC) — done:** `screener:v11` (corporate_actions, see DQ-1) · `pipeline-health:v5`
  (weekend-safe stock thresholds, `rbi` feed, installs from its lock; canary `HEALTH-OK fresh=14/14`) · Grafana stale-feed exclusion removed
  (4 rules `health=ok`) · k3s control-plane backup made daily (`pull-k3s-backup.sh`, incl. tls/cred) + `pull-backups.py` daily with safe
  rotation, both via workstation cron · CI: ruff fatal set + shellcheck errors · weekly price audit (2,471 comparisons, **0 mismatches**;
  `out/price-audit-2026-10-18.csv`) · old screener v1–v9 removed on pi1/pi3 · tarball of every live image on the TX2 SSD.
- [x] ✅ 2026-10-18 **Engineering pass (plan: `engineering-plan-2026-10-18.md`; decisions ADR 0014–0020), 09:50–13:10 UTC:** AR-4 DB Guaranteed QoS + probe (17 s
  downtime) · **chunk merge** (stock_prices 3,382 → 107, index_prices 300 → 84 chunks, rows identical, after a verified pre-merge dump;
  per-symbol query planning 34 s → 34 ms — the reason v12's first canary was 2× slower) · `screener:v12`/`v13` (DQ-1 done, DQ-2 done +
  flipped live, G-2a, price anomalies) · corporate-actions + fundamentals full backfills · EV-0 built and run · AR-8 reader roles · news
  unique constraints · manifest-safeguard CI test · dedup retired · UPS decided not needed. Details in each item below.
- [x] ✅ 2026-10-18 **INCIDENT (self-inflicted, recovered, no job failed): image wipe by `crictl rmi --prune`.** While pruning old screener tags,
  `crictl rmi --prune` removed every image not used by a running container — all `local/*` on pi3 and 6 on pi1 (fdi-watch, fiidii, fx, nse,
  rbi, yfinance); a timeout stopped it on pi1. ~09:00–09:27 UTC: pi3 cordoned within a minute (stock-metrics/screener/intraday ran on pi1),
  images restored from TX2 tarballs + `ctr images export` on pi1 and pi2 (load peaked 6.9 → cooled), checksums at every hop, pinned test pods on
  pi3, uncordoned; every Job in the window Succeeded. Rule added to ops manual §8 ("NEVER `crictl rmi --prune`"). **Also during recovery a
  `pgrep -af` on pi2 printed the expanded `@@PW@@` (the Pi sudo password) into the session transcript** — owner informed; whether to rotate is the
  owner's call. Lesson recorded in the same §8 entry.
- [x] ✅ 2026-10-18 **Power resilience — DECIDED: no UPS** (ADR 0019) (owner). Accepted: a mains loss takes every node and the pi0 watcher down together;
  recovery is automatic (verified on the 10-18 outage). Not to be re-proposed.
- [x] ✅ 2026-10-18 **Cluster dedup — RETIRED, not redesigned** (owner delegated the call). Measured: 1–2 GB of user data per Pi on 229 GB SSDs,
  TX2 SSD 12 % used — nothing to deduplicate; the design targeted non-existent drives. Note: `piCluster/cluster/RETIRED.md`.
- [ ] 🤔 **pi2 heat (owner, hardware):** soft-temp flag (`0x80000`/`0x80008`) recurs, max 69.3 °C in the 10-18 review — heatsink/airflow.
  2026-10-20 window: pi1 (66.6 °C) and pi3 (73.1 °C) also hit the soft-temp WARN — consider airflow for all three workers.
  **2026-10-20: owner removed pi2's case** (airflow fix). Baseline right after, 04:34 UTC: **51.5 °C**, `0x0`, load 0.6 (near-idle — the
  test is whether the soft-temp flag stays away under load; read the next power-review WARN column before calling it fixed).
  Also: **pi2 rebooted again ~2026-10-20 01:56 UTC**, cause unknown (0 undervolt events; that boot left no journal).
  ✅ 2026-10-20 **persistent journald now ON for pi1 + pi2** (owner-approved; 200M cap; RPi OS gotcha + rollback in ops manual §8) —
  the NEXT unexplained hang/reboot is diagnosable: read `journalctl -b -1 -e` on the affected node.
- [x] ✅ 2026-10-19 **pi2 old images: MOOT** — verified with `ctr images ls`: pi2 holds no `local/*` images at all (and
  212 G free). The old tracker note (screener v1–v8, health v1–v4, …) is stale; pi2 runs only control-plane system pods.
  (Its disk is
  4 % used, so no pressure; if ever pruned, remove named tags one by one (ops manual §8), in a quiet window, never `--prune`. Note: pi2's copies were the
  only remaining source for fdi-watch/fiidii/fx/rbi during the incident. ✅ 2026-10-18: **every one of the 16 live images now has a tarball on the
  TX2 SSD** (`/mnt/tx2-ssd/image-tarballs/` + `/mnt/tx2-ssd/sentiment-fetcher-v1.tar.gz`; checksums in `SHA256SUMS.all`) — the 9 that existed only on
  Pis were exported from pi3 and verified, plus telegram-digest:v8 and headline-ner:v3. Keep this true at every rebuild (§6).
- [x] ✅ 2026-10-14 **Fix the Pi power supplies — hardware fix COMPLETE** (owner: all Pis moved to separate power
  ~2026-10-12 02:30 UTC; before that pi1/pi2/pi3 shared one multiport block; pi1 given its own supply 2026-10-14).
  **The physical item is closed** — no swap test is needed any more. The 24 h acceptance on pi1 is still open below.
  - [x] ✅ 2026-10-20 **pi1 acceptance PASSED — power item CLOSED** (final verdict sub-bullet below). History: the 24 h rule run 2026-10-13 03:51 UTC was a FAIL on the old supply (4 live
    `0x50005` samples, 36 `dmesg` events — see below). New trial on the new supply:
    `bash ops-tools/power-review.sh --since 2026-10-14T05:39:00Z`, **verdict due ≥ 2026-10-15 05:40 UTC**, needs real
    load (let the CronJobs run). **Read 2026-10-14 06:49 UTC (70 min, all four nodes PASS):** pi1 `0x0` throughout,
    0 live hits, max 50.5 °C, load 1.17, **0 `dmesg` undervoltage events since boot** (pi0/pi2/pi3 also 0). pi2 WARN
    on heat only (`0x80008`, 60.1 °C). Positive, but 70 min is not the verdict.
  - **pi1 hang + new supply, 2026-10-14:** pi1 hung ~02:02 UTC (last stats row 02:00 UTC at `0x50000`, load 0.3; no ping/SSH, kubelet silent; the
    telegram-bot pod sat Pending ~3.5 h, nothing else affected). Owner power-cycled it and reports **pi1 now has its own supply** (exact swap time
    and whether the cable changed: not recorded — owner to confirm). Up since 05:39 UTC: `0x0`, 0 `dmesg` undervoltage events, load ~0.4 (06:11 UTC —
    idle, proves nothing). **New 24 h trial for pi1: `bash ops-tools/power-review.sh --since 2026-10-14T05:39:00Z`, due ≥ 2026-10-15 05:40 UTC** —
    needs real load (let the CronJobs run; a bulk import to pi1 is a fair stress test only once a day of clean idle exists). pi0/pi2/pi3 keep their 10-13 PASS.
    pi2 also rebooted ~02:01 UTC 10-14 (up 3.6 h at 05:35 UTC; `0x80000` heat flag only, 0 undervolt events). **Correlation found 2026-10-14 (stats CSVs):** pi1's last sample is 02:00 UTC and pi2's first
    post-boot sample is 02:05 (uptime 202 s, load 3.1) — pi1 and pi2 failed within minutes of each other while pi0 and pi3 logged normally through it. Pre-10-12 those two shared a supply block, so a common
    cause (shared mains strip/outlet, or pi2's restart load hitting pi1) is plausible but **unproven**; the cause of pi2's reboot is not recoverable — it has **no persistent journal** (`journalctl --list-boots` shows only the current boot).
    Proposal (not applied, owner's call): enable persistent journald on pi1/pi2 so the next hang leaves a trace. Also: the 10-14 02:00 pi1/pi2 loss did not trigger a `nodewatch` alert in anything I read — check `/var/tmp/nodewatch.log` history/cron on pi0 (it is overwritten each run, so only the latest state is visible).
  - **pi1 import stress test, 2026-10-14 06:19–06:21 UTC (new supply, ~40 min after boot):** the 116 MB `screener:v10` tarball push + `ctr import`
    (68 s, load peaked 2.78 — the same burst that gave pi1 `0x50005` on 10-12) sampled every 4 s: **`0x0` throughout, 0 `dmesg` undervoltage events**; a yfinance canary pod on pi1 afterwards also left `0x0`/0 events.
    First strong positive sign for the new supply — still not the verdict (needs the 24 h window above, incl. a quiet stretch and the nightly CronJobs).
  - **Power review re-run 2026-10-18 06:44 UTC (`--since 2026-10-14T05:39:00Z`, ~1,160 samples/node):** pi0 PASS · pi2 PASS (heat WARN, 69.3 °C)
    · **pi1 FAIL and pi3 FAIL on the gap rule only — 0 live hits each, `0x0` throughout.** Cause of the gap, verified 2026-10-18: a **site-wide power
    event ~05:55–06:20 UTC 10-18** — ALL five machines rebooted (`uptime -s`: pi0 06:02, pi2 06:03, pi1 + pi3 together 06:17, TX2 at the same
    time; pi0 is on its own supply, so this was mains/strip, not a Pi PSU). Since boot: `dmesg` undervoltage = **0 on every Pi**. pi1 + pi3 came
    back ~15 min after pi0/pi2 (same minute as each other). **Owner confirmed 2026-10-18: a mains power loss.** `nodewatch` could not alert:
    pi0 went down too (AR-12's single-watcher gap, now observed). **pi1 under load on the new supply: 0 live hits in ~1,160 samples (10-14→10-18)
    and 25/25 `0x0` during the 2026-10-18 v11 import** — the supply fix looks good. Formal clean window: `power-review.sh --since
    2026-10-18T06:20:00Z` — PASS at 07:14 UTC (all four); **verdict due ≥ 2026-10-19 06:20 UTC.** Note: the tool reads only the current
    `stats.csv`, so a weekly logrotate (pi3 rotated at 07:00 UTC 10-18) shortens its view — check sample counts when reading a verdict.
  - **FINAL VERDICT 2026-10-20 04:12 UTC (`power-review.sh --since 2026-10-18T06:20:00Z`, ~46 h, 532–542 samples/node): pi0 · pi1 · pi2 · pi3 all
    PASS — acceptance passed, the power item is CLOSED.** 0 live hits on every node, last hex `0x0` everywhere; `dmesg` undervoltage since boot
    0 / 0 / 0 / 0 (pi1 under the full nightly CronJob load for the whole window). Heat WARNs only (pi1 66.6 °C, pi2 62.8 °C, pi3 73.1 °C max) —
    heat stays open as its own item above. Verdict recorded in ops manual §8. **New finding: pi2 rebooted again ~2026-10-20 01:56 UTC** (`uptime -s`
    2026-10-19 18:56 local), cause unknown and unrecoverable (no persistent journal — the journald proposal is still open, owner's call); healthy
    since (load 0.9, `0x0`, Ready).
  - **Evidence (2026-10-10/11):** pi2 live undervolt+throttle (`0xd0005`, 62 °C) and wedged ~16 h. A 5 V 2 A adapter still gave
    9 `Undervoltage detected` events in 5.5 min at near-idle load. pi1/pi2/pi3 dropped off together while pi0 stayed up; pi2 and
    pi3 logged events within ~0.5 s of each other; pi3 went from clean to ~80–110 events/h from ~11:40 UTC 10-11 (1,364 by
    20:16 UTC) then the counter stopped rising (cause never established). The shared charger is a single point of failure.
  - **Baseline after the rewire (2026-10-12 02:59 UTC, all four Pis up, nodes Ready):** pi0 `0x0`, 0 events · pi1 `0x50000`,
    1 event (power-on transient, none live) · pi2 `0x80000`, 0 events (58 °C; soft-temp flag since boot only; 246 MB avail) ·
    pi3 `0x0`, 0 events. **Since then:** pi1 logged undervolt events during bulk image imports (2 for the telegram v7 import, 3 for
    the screener v9 import; node stayed Ready) — it was sensitive to heavy SSD/IO bursts **on the old supply**.
    **The "no bulk transfers to pi1" restriction is lifted on the new supply** (the 116 MB `screener:v10` push + 68 s import on
    2026-10-14 was clean, `0x0`/0 events) — I would still keep it until the 24 h acceptance lands.
  - **pi3 under the load that wedged it before (2026-10-12 08:30–08:36 UTC):** the first *scheduled* run of the fixed `pg-backup`
    (`BACKUP-OK marketdata-2026-10-12.dump size=171766332 entries=15102`, `.sha256` sidecar, 6 min) wrote ~170 MB to pi3's SSD and
    pi3 stayed `0x0` with 0 undervoltage events. On 10-11 the same write coincided with pi3 dropping off the network on the shared charger.
  - **Review = pass/fail:** run **`bash ops-tools/power-review.sh`** (new 2026-10-12; pulls the CSVs and applies the rule: no live
    `throttle_hex` bit `0x1/0x2/0x4`, no new since-boot undervolt bit, no sample gap > 15 min; heat is a WARN, not a power fail;
    exit 0 = PASS). Also compare `dmesg | grep -ci undervoltage` with the baseline. The CronJobs are already running
    (re-activated 10-12), so this is under real load. Record the verdict per node here and in ops manual §8.
  - **Re-check 2026-10-12 08:41 UTC (`power-review.sh`, 69 samples/node): pi0 PASS · pi2 PASS · pi3 PASS · pi1 FAIL (same 2 samples; exit 1).**
    Per-Pi `dmesg` undervoltage count since boot: **pi1 = 25** (up ~5.7 h; baseline 1 at 02:59; first 02:56 UTC power-on, last stamped 01:22
    host-local ≈ 08:22 UTC if PDT — not verified), **pi0/pi2/pi3 = 0**. Live `get_throttled`: pi0 `0x0`, pi1 `0x50000`, pi2 `0x80000`, pi3 `0x0`;
    pi1 load 0.24 at that moment, so its events are not only the import bursts. The remaining ~18 h of the window cannot rescue pi1
    (the rule fails on any live hit); the physical swap test does not need to wait for 02:59 UTC.
  - **FINAL 24 h REVIEW, 2026-10-12 02:59 → 10-13 03:51 UTC (299 samples/node, no gaps, no node wedged or rebooted): pi0 PASS · pi2 PASS · pi3 PASS · pi1 FAIL — acceptance NOT passed.**
    pi1: 4 live `0x50005` samples (04:35, 04:50, 09:10 UTC on 10-12; **03:10 UTC on 10-13, at load 0.58**) and **36** `dmesg` undervoltage events since boot (pi0/pi2/pi3: **0** each; pi2 max 56 °C, 2.19 load;
    pi3 max load 1.83 incl. the 170 MB backup writes). The dedicated supplies fixed pi0/pi2/pi3; pi1 is the open hardware item. Next = physical: swap pi1's PSU + cable (spare set), re-run
    `bash ops-tools/power-review.sh --since <swap time>` for another 24 h. (The 10-13 pre-migration backup and the DQ-4 rewrite ran on TX2/pi3, not pi1.)
  - **Update 09:46 UTC (82 samples/node): pi0/pi2/pi3 still PASS (pi3 max load 1.83, 0 events); pi1 FAIL with a third live sample `0x50005` at 09:10 UTC —
    at load 0.41, no image transfer running** — and 27 `dmesg` events (was 25 at 08:41). pi1 undervolts at near-idle, so this is its supply/cable
    (or the board), not workload: do the swap test now (spare PSU + cable first). `collect-node-stats.sh` no longer errors (`tail -1` on several files).
  - **Early read, 2026-10-12 02:59 → 08:25 UTC (5.4 h, 65 samples/node):** **pi0 PASS** (max 36 °C), **pi2 PASS** (max load 1.74,
    56 °C, 0 live hits), **pi3 PASS** (0 live hits) — but **pi1 FAIL: 2 samples read `0x50005` (undervolt + throttle live) at 04:35 and
    04:50 UTC**, during the screener-v9 and telegram-v7 image imports (CPU 58–73 %, load 3.35). So pi1 still undervolts under a burst
    **even on its own supply** → suspect pi1's supply or cable specifically (the earlier rewire fixed pi2/pi3/pi0, not pi1). Next:
    the swap-test ladder on pi1 (spare PSU+cable first; skill `node-power-health` §4) — physical, owner. Until then treat pi1 as
    the fragile worker: no bulk imports to it, and note that the daily floating jobs now mostly land on pi1.
  - **Open questions for the owner:** is each Pi's supply truly dedicated (rating, cable length/gauge)? what changed ~11:40 UTC
    10-11 when pi3's events started and later stopped?
  - **Buy/do (if any Pi still faults):** 5.1 V / 3 A supplies + short (≤ 0.5 m), thick (≥ 20 AWG) micro-USB cables for
    **pi2 first, then pi1, then pi0**; keep **one spare** supply+cable set for swap tests. Power pi2's SSD **separately from the
    Pi's 5 V rail** (it is the BOOT drive — keep its data path direct): a powered enclosure or a separate-power Y-cable. A hub in
    the boot path is **unverified on Pi 3 / 3B+** — test on pi3 or a spare first, never first on pi2. Optional: an inline USB power meter.
  - **Method:** the identity-free swap-test ladder (skill `node-power-health` §4): PSU+cable first, then USB-SATA adapter,
    board last. **Never swap boot SSDs between nodes** (hostname, static IP, k3s certs and cluster state travel with the disk).
    *No longer needed for pi1 — the supply is replaced; kept as the ladder for a future fault.*
  - **Open (heat, not power):** pi2 touched the 60 °C soft-temp limit right after boot (`0x80008` live at 02:44 UTC, gone by
    02:52). If it recurs once the node is calm, it needs a heatsink/airflow.
  - **Later (decision, not part of this fix):** one shared regulated 5 V rail with fused branches — architecture review AR-10;
    needs an enclosed supply and careful mains-side wiring. Detail: `architecture-review-2026-10-10.md` AR-10; skill
    `node-power-health`; ADR 0011.
- [x] ✅ 2026-10-14 **Work held back by pi1 — done (06:15–06:40 UTC):**
  - **`screener:v10` built and deployed** (the "image rebuild/import" backlog; imports to pi1 had been avoided since the 10-12 undervolt events). Built on TX2 (55 s); scripts + Dockerfile
    + lock verified byte-identical to the repo (per-file sha256 inside the image); `pip freeze` identical to v9 (only `stock_prices.py` changed); tarball sha256 `cb24473f…` checked at each hop;
    imported on **pi3 (62 s)** then **pi1 (68 s, `0x0` throughout — see the power item)**; live diff of `stock-screener/cronjobs.yaml` = exactly 6 image lines, applied, live spec re-read = v10 on all six.
    `backfills.yaml` bumped too (not applied — Jobs). The Dockerfile now `pip install -r requirements.lock` (AR-5 step 2 for this image).
  - **DQ-1 code half — split detection (done):** `stock_prices.py` pulls `actions=True`; a non-zero `Stock Splits` in the 5-day window re-fetches and rewrites that symbol's full history (idempotent
    DELETE+INSERT; cap 25 symbols/run). New `trade_ts()` stamps `00:00 UTC` on the trade date on **every** write path — the `BACKFILL=max` path used `Ticker.history()` (tz-aware local midnights) and would have
    reintroduced DQ-4 (ADR 0013); now closed. `tests/test_stock_prices.py` (10 tests, mutation-checked, in CI). **Canary on pi1** (forced resync of CTVA + TCS.NS against the live DB): `SPLIT-RESYNC` ×2,
    `PRICES-OK … splits_resynced=2`; CTVA 1856→1857 rows, TCS.NS 6004→6005 (just the new day), 0 non-midnight rows, 0 duplicate trade days, CTVA max close still 14.45. **First scheduled v10 run (stock-prices 06:30 UTC, on pi1): `PRICES-OK mode=daily fetched=997 warn=1 rows=4793 splits_resynced=0` in 8 m 43 s (the 1 warn = the known dead `DUMMYHEG.NS`); 10-12/10-13 = 998 rows each, 0 non-midnight rows, 0 duplicate (symbol, ts) in the last 10 days; pi1 `0x0`, 0 undervolt events after it.** **Not yet exercised on a real split** — the first one
    in the universe is the live proof. **Still open for DQ-1:** `adj_close` + `corporate_actions` table (additive schema, needs a migration), dividends/total return.
  - **Missed `telegram-digest` refired:** the 08:00 IST run (02:30 UTC) failed while pi1 was down; re-run from the CronJob 06:23 UTC → `DIGEST-DONE recipients=3 rc=0` (delivered ~4 h late). The 02:45 alerts run is not due until later today.
  - **Not done, on purpose:** the other "natural rebuild" items (news `DEFAULT_FEEDS`, heartbeat v2, lock files for the other 15 images) — each is a separate image and a separate pi1 import; batch them with the next change to that image rather than rebuilding working fetchers.
- [x] ✅ 2026-10-13 **Fix pass (03:50–04:55 UTC):** final power review (pi1 FAIL — see the power item); **DQ-4 repaired** and **CTVA (DQ-1) repaired** on the live DB (see §1 "Data correctness"); new tools
  `ops-tools/audit-price-adjustments.py` and `ops-tools/sql/2026-10-13-stock-prices-normalise-ts.sql`; stale-feed alert no longer counts `stock_fundamentals` until 10-18; overnight health re-verified (all
  23 CronJobs active; nse-fetcher 18:15, rbi 14:00, fiidii 21:00, telegram-digest 02:30 (`DIGEST-DONE recipients=3`) all Completed; the 10-12 Nifty close replaced the intraday bar). Left alone on purpose:
  anything needing an image rebuild/import (pi1 is still the undervolting node), the physical/owner items, and the large roadmap work.
- [x] ✅ 2026-10-12 **Maintenance pass (09:00–09:50 UTC) — what was verified, changed, found:**
  - **First scheduled runs verified:** `stock-metrics` 09:00 Completed (3 m 27 s) · `stock-screener` 09:15 `SCREENER-OK scored=997 eligible=884 shortlisted=50`
    (on pi3; no duplicate score rows; `stock_scores` now 10-12) · `stock-report` 09:30 `REPORT-OK reports=50 digest=yes` · **`telegram-alerts` 09:45 — its first
    ever scheduled run — `ALERTS-OK 3 message(s) for 3 user(s) rc=0`** (owner + both member rows incl. the group chat: multi-user fan-out works). `pipeline-health`
    08:45 Completed; `pg-backup` 08:30 Completed. `stock-prices` heartbeat is fresh; `stock_scores` turns fresh at the next daily health run (01:45 PDT).
  - **Grafana alerts retuned and applied** (ConfigMap via `apply-grafana-provisioning.sh`, pod restarted on pi3; all 4 rules `health ok`): VIX 25 → **30**, FII −3000 → **−5000**,
    and the **sentiment-drop rule — which was in the repo but had never been applied — is now live**. Evidence and re-tune plan: telegram-bot doc §3.3. Known noise: the
    stale-feed rule is firing (counts `stock_fundamentals` + `stock_scores`) until the data catches up (see the Grafana item below).
  - **Second backup copy refreshed:** `pull-backups.py` pulled `marketdata-2026-10-12.dump` (172 MB, sha256 ok) to `out/backups/`.
  - **CI:** lint step (`py_compile` every tracked `.py`, `bash -n` every `.sh`, the Python-shebang pre-commit hook) and push triggers for `main`, `feat/**`, `fix/**`. First run on that push (#4): green.
  - **New data-quality finding DQ-4** (mixed `stock_prices` timestamp conventions + 4,434 duplicate rows) and evidence for DQ-1 and G-22 — see §1 "Data correctness".
- [x] ✅ 2026-10-11 **`nodewatch` deployed on pi0** — out-of-cluster dead-man's switch. Script in `/usr/local/bin`,
  `/etc/nodewatch.hosts` + `/etc/nodewatch.secrets` (mode 600, root), root cron `*/5` (log overwritten each run at
  `/var/tmp/nodewatch.log`); its own dedicated Telegram bot, separate from the in-cluster `telegram-bot`; delivery test HTTP 200.
  **First real alert 2026-10-11 23:45 UTC (pi3 down) — chain verified end-to-end.** Gotcha: `TELEGRAM_CHAT_ID` must be the numeric
  id, not the bot username ("chat not found"); an empty/absent secrets file makes the script record a state change as handled
  without sending. Open: a second watcher host (AR-12) — pi0 is the only out-of-cluster alert path.
- [x] ✅ **Node-local stats logging** — deployed 2026-10-10 on pi0/pi1/pi2/pi3/TX2 (ADR 0011); the cron entries were missing everywhere and
  were added 2026-10-11, header bug fixed (see AR-12 below and the ADR amendment). Puller: `ops-tools/collect-node-stats.sh`.
- [x] ✅ 2026-10-11 **pi0 → tailnet** (tailscale from the Raspbian apt repo, `--accept-dns=false --accept-routes=false`; node key expires
  2027-04-09; Pi-hole admin reachable over the tailnet). Remaining in §2.

- [x] ✅ 2026-10-11 **Node jobs/responsibilities documented** in `node-roles.md` (all Pis + TX2; pinned vs unpinned
  CronJobs, host cron jobs, blast radius). Live check that day: pi1 **Ready** (telegram-bot Running there), pi2 Ready but
  **cordoned** (undocumented — find out when/why), all 23 CronJobs still suspended.
- [x] ✅ **2026-10-12 Telegram v7 deployed** (DQ-2 fix for `/stock` and the digest tables: flow metrics read full-year yfinance rows, not EDGAR year-to-date
  10-Q rows). Built on the TX2 (code verified identical to the repo, per-file sha256), tarball checksummed at each hop, imported on pi1 (the only node
  that runs it; 2 undervolt events, node stayed Ready), tags bumped on the Deployment + both CronJobs (live diff = exactly 3 image lines), bot rolled
  out cleanly and is listening; `/stock MSFT` renders the full card in the new pod (no message sent — called inside the pod). 96-test suite passes.
  Observed in the old pod's log: a chat that is not on the allow-list messaged the bot (`/status`) and was ignored — the allow-list is working;
  worth knowing who that was.
- [x] ✅ **2026-10-12 Telegram multi-user — every user has their own setup (ADR 0012, `telegram-digest:v8`).** Owner = the Secret's chat id; members =
  `bot_users` rows, managed from the owner's chat with `/adduser <id> Name`, `/rmuser <id>`, `/users` (no Secret edit, no restart). `watchlist` is now keyed
  `(chat_id, symbol)` — each person's holdings, `/watch`, digest watchlist section and watchlist alerts are private to their chat; digest and alert Jobs fan
  out one message per user (a member who blocked the bot only logs a WARN). An unknown chat is still never answered, but the owner gets one access notice
  per chat per day with a ready `/adduser` line. Schema `09-multiuser.sql` applied (watchlist was empty; idempotent; refuses to guess an owner if it has
  rows; tested against a throwaway Postgres in 4 cases). Verified live in the new pod against the real DB with a throwaway member (cleaned up): stranger
  silent + owner notified once + stranger `/adduser` blocked; member `/watch` private; member cannot `/adduser`; removed member gets 0 replies; digest and
  alerts `main()` give each user only their own content. `tests/test_multiuser.py` 38 tests (allow-list and watchlist-scoping mutation-checked); added to CI.
  **The Secret's `chat-id` is the owner's PRIVATE chat again** (restored from `telegram-bot-prev-chat-id`; it had been pointed at a group, which made the whole
  group the owner). **Progress 2026-10-12 08:31/08:32 UTC: the owner ran `/adduser` twice** — `bot_users` now has 2 active rows, both named `sailu`, one
  private chat and one *group* chat (a group shares one watchlist — consider `/rmuser` on it if separate lists are wanted); `watchlist` is still
  empty. Not verified: that each has sent `/start` (the log shows one `/start` answered). Any further people: same flow, then `/start`. Open extension: per-user digest time and alert threshold. Side notes: the schema Job could not be re-run under its own name (delete guard on
  `timescaledb…`) — ran as `schema-multiuser-09`; `timescaledb-schema-09` (broken first attempt, suspended) and `telegram-bot-prev-chat-id` (rollback copy of the
  old owner id) remain in the namespace.
- [x] ✅ **2026-10-12 Screener v9 rolled out (DQ-2 + DQ-3 fixes).** Image content verified byte-identical to the repo scripts (sha256 per
  file), tarball (checksummed at each hop) imported on pi1 (v9 was already on pi3; 3 undervolt events during the import, node stayed
  Ready), all six screener CronJobs bumped v8 → v9 (live diff = exactly 6 image lines), canaries run **on pi1 itself**:
  `stock-screener` → `SCREENER-OK scored=994 eligible=885 shortlisted=50`; `stock-report` → `REPORT-OK reports=50 digest=yes`; no duplicate
  score rows. **Top-50 diff** (`out/v9/top50-{before-v8,after-v9}.csv`, gitignored): Nifty 500 50/50 identical (its fundamentals were already
  full-year yfinance rows → no change); **S&P 500 only 25/50 overlap** (top-10 moved from MU/SNDK/APA/PTC/NEM/HPQ/WDC/EOG/DVN/TRV to
  INCY/ALL/SNDK/NEM/TRV/MU/APA/PTC/PGR/PM; mean growth_score −0.06 on the overlap) — the intended effect of no longer comparing year-to-date
  figures with full years. `stock_fundamentals` DQ-3 (NameError) is unit-tested only; **its first real validation is the Sat 2026-10-17
  06:00 PDT full pass** (watch for `FUNDAMENTALS-OK` with a `failed=` count and a Completed Job). US price data ended 10-08 after the outage; **recovered — verified 2026-10-12 08:25 UTC:
  S&P 500 through Fri 10-09 (506 symbols), Nifty 500 through 10-12 (494)** (Monday's US close lands in tonight's run).
  `stock_fundamentals` is still stale (last 10-04) until the Sat 10-17 pass. **`pipeline-health` ran 08:45 UTC (Completed, 22 s): `stock_prices` is now
  fresh; the only stale feeds are `stock_scores` (last 10-09 — the 09:15 UTC `stock-screener` run should fix it) and `stock_fundamentals` (last 10-04, until 10-17).** Previously: History of the 2026-10-10 second wedge:** Sequence: v9 built on
  TX2 and imported on pi3 (63 s), then pi1's tarball push/import → API `ServiceUnavailable`, pi1 `ctr` EOF; pi1/pi2 SSH
  banner resets with ping alive, pi3 off the network; pi2 + pi3 later came back (~17:41 UTC, "up 0 min"); **pi1 still
  NotReady with no ping at 17:50 UTC — needs a physical power check.** Cause unproven (a 116 MB push + import on
  Pis with a known PSU fault is the prime suspect — avoid bulk transfers until the power fix is accepted).
  At 17:44 UTC **all 23 CronJobs were suspended** (all 23 were active before; list in
  `/tmp/opencode/cronjob-suspend-before.txt`) — un-suspend **staggered**, not all at once, once pi2 load < 2 and
  pi1 is back. Live CronJobs referenced `screener:v8` until 2026-10-12; `screener:v9` exists only in TX2 docker, pi3's containerd and
  tarballs (TX2 `/home/ahytx2/screener-v9.tar.gz`, workstation `/tmp/opencode/`, and a leftover copy in `/home/pi1/`).
  Resume the v9 rollout (DQ-2/DQ-3 fixes) only after: 4 nodes Ready, pi2 calm, power fix accepted.
  **Update 2026-10-11 ~23:45 UTC (autonomous work session):**
  - ✅ **`pg-backup` fix (AR-11) applied and canary-proven** — `tests/test-pg-backup.sh` 21/21; live canary
    `BACKUP-OK marketdata-2026-10-11.dump size=171611578 entries=14935`, atomic rename + `.sha256`, the 0-byte
    10-10 dump and the 393 KB 10-03 dump pruned. Fresh dump copied to `out/backups/` (sha256 matches). CronJob stays
    **suspended** (applied manifest kept `suspend=true`).
  - ✅ 15 stale failed Jobs from the first outage deleted.
  - 🔴 **pi3 dropped off the network again (~23:42 UTC, no ping, NotReady)** minutes after the backup canary (pg_dump
    writing ~164 MB to its SSD) plus a 165 MB read — same trigger pattern as the pi1-push wedge. **Power is NOT
    fixed**; pi1/pi2/pi3 are still on the shared 105 W block. pi1, pi2, TX2, pi0 are up; DB, Telegram bot (pi1) up;
    Grafana (pinned pi3) and pg-backup (pinned pi3) unavailable until pi3 returns. Needs a physical check/power cycle.
  - Staged un-suspend **started and rolled back**: `yfinance-fetcher` un-suspended once, its catch-up pod hit
    `ErrImageNeverPull` on pi1 (see next), then re-suspended. **All 23 CronJobs are suspended again.**
  - 🔶 **Image gap on pi1:** `yfinance-fetcher:v5` and `nse-fetcher:v4` exist on pi3 but NOT on pi1 (unpinned jobs that
    schedule on pi1 fail with `ErrImageNeverPull`). Fix = import both on pi1 (or pin to pi3) **before un-suspending**;
    avoid bulk pushes until the power fix. `screener:v9` is on pi3 only.
  - ✅ (repo only, **not deployed**) Telegram `stocks.py` `series()` now uses full-year yfinance rows for flow metrics
    (same DQ-2 flaw as the screener); new test, 96/96 pass. **Deployed 2026-10-12 as `telegram-digest:v7`** (see the v7 item in §1).
  - Order when pi3 is back and power is fixed: import missing images (pi1) → un-suspend in stages (fetchers → ner/
    sentiment → ta → screener chain) with `dmesg`/load checks → `screener:v9` import on pi1 + apply + canary + top-50 diff.

- [x] ✅ 2026-10-12 **2026-10-09→10 outage recovery — complete except power acceptance:** all 4 nodes Ready, DB Running, all 23 CronJobs
  un-suspended (staggered), missed runs refired (fiidii, rbi, news, sentiment, ner, universe-refresh, health) — details in the 10-12 entry
  below. The root cause (undervoltage) is only *claimed* fixed until the 24 h acceptance in the power item passes.
- [x] ✅ 2026-10-12 **Telegram v6 running** — bot pod Running on pi1 (`local/telegram-digest:v6`), `COMMANDS['nifty']('/nifty')` returns the
  card (22,531.65, "12 Oct close"), `telegram-digest` ran in-window and sent. ✅ Spot-checked 2026-10-13: the `nse-fetcher` run overwrote the intraday bar — `^NSEI` 10-12 is now 22,363.35 (was 22,531.65).
- [x] ✅ 2026-10-12 **News feeds repaired** — diagnosed: Moneycontrol (both feeds) 302-redirect to a login-consent wall for every user-agent, Reuters
  (`feeds.reuters.com`) no longer resolves. Fixed with a `FEEDS` env override in `manifests/ingestion/news/cronjob.yaml` (no image rebuild): kept
  ET + Business Standard, added LiveMint, Hindu BusinessLine (India) and CNBC, MarketWatch (global). Canary: 6/6 feeds parse, 160 headlines, 127 new
  rows. Note the source mix changed (affects sentiment comparability across the 10-12 boundary). The script's `DEFAULT_FEEDS` still lists the dead feeds
  — update it at the next natural rebuild.
- [x] ✅ 2026-10-12 **Import the new fetcher images on pi1** — `yfinance-fetcher-v5` and `nse-fetcher-v4` imported, canaried on pi1, CronJobs
  active. Side effect: pi1 logged undervolt events during both transfers (power item in §1).
- [x] ✅ 2026-10-10 **Index long-term reference** (v5): 1/3/5-year averages +
  low/normal/high reading on `/index` `/nifty` `/sensex` `/gold`; index history
  backfilled to 5 years (47,408 rows, no duplicates). Detail: telegram-bot doc
  §1a "v5 / v6". 95 unit tests.
- [x] ✅ 2026-10-10 **Index history back to 1985** (292,387 rows, 38 tickers,
  no duplicates, 299 chunks). Fetchers commit one calendar year per
  transaction (`yfinance-fetcher:v5`, `nse-fetcher:v4`, canaried on the live
  CronJob spec); `index_prices` creates one-year chunks from now on (approved,
  recorded in `01-schema.sql`). Jobs `backfill-yfinance-deep` (250,309 rows,
  3.5 min) and `backfill-nse-deep` (42,078 rows, 53 s). Cards verified against
  the live DB. `tests/test_index_fetchers.py` (4 tests).
- [x] ✅ 2026-10-26 `NIFTYMIDCAP150.NS` pre-Yahoo history backfilled from NSE (`backfill-nse-direct` Job,
  `nse-fetcher:v7`): the series now runs 2015-01-01 → today (2,915 rows; the 999 NSE rows sit strictly
  before Yahoo's 2019-01-14 first bar — rule-4 ownership verified, 0 overlap). Close-only early rows carry
  OHLC/volume NULL.
- [x] ✅ 2026-10-14 **pi2 reboots under shared-charger power — closed with the power fix.** Six+ reboots on
  2026-10-10 (each took API + cluster DNS + the bot down); the cause was the shared multiport block, and pi2 is on a
  dedicated supply since ~2026-10-12 02:30 UTC — it PASSed the 24 h review (10-12 02:59 → 10-13 03:51, 0 events).
  Caveat still open: pi2 rebooted ~02:00 UTC 10-14 with the cause not established (`0x80000` heat flag only, 0
  undervoltage events), and it is the one node with a heat WARN (`0x80008`, 60.1 °C at 06:49 UTC) — watch for a
  heatsink/airflow.
- [x] ✅ 2026-10-10 **Telegram v4 live** (same day as v3): table layout,
  `/index`, YTD everywhere, valuation repriced to the latest close,
  `/multibagger`, confidence score on every recommendation. 85 unit tests;
  canary `ALERTS-OK`. Detail: telegram-bot doc §1a "v4".
- [ ] 🔶 Phone check of the v4 commands (`/help`, `/stock TCS`, `/index nikkei`,
  `/world`, `/top growth sp500`, `/multibagger`, `/watch add …`, `/digest`) —
  only log-verified so far; table widths assume ~38 monospace characters.
- [ ] 🤔 Screener value factor uses weekly Yahoo ratio snapshots (P/E, P/B,
  EV/EBITDA, market cap from Saturday's `stock-fundamentals` run), not the
  day's close. Reprice inside `stock_screener.py` the way `/stock` now does?
- [ ] 🤔 Multibagger screen is unbacktested. Possible now for the S&P 500 (EDGAR
  history with filing dates since 2007), not for the Nifty 500 (4 annual points,
  no filing dates). Superseded by the replacement screen in the evidence roadmap §3.
- [x] ✅ 2026-10-10 Schema Job rerun (`08-watchlist.sql` → table `watchlist`;
  `^RUI` in ticker_names). `yfinance-fetcher:v4` live (pi1 + pi3 imports),
  1y backfills run for both index fetchers: `^RUI` 251 rows, `^CRSLDX` (Nifty
  500) 246 rows, no duplicate (ticker, ts). Grafana "Derived Metrics"
  dashboard applied (file present in the pod; not yet checked in the UI).
- [x] ✅ 2026-10-12 **FX history backfill** — 2,050 daily closes (10 pairs × 205 days, 2025-12-15 → 2026-09-29) via a one-shot Job on the
  existing `fx-fetcher:v1` image with the script in a ConfigMap (no rebuild; `manifests/ingestion/fx/fx-backfill.py` + `backfill-job.yaml`).
  Verified: 0 duplicate (pair, ts), all at 00:00 UTC like the live daily rows, and the bot's own YTD query now returns a year-start for all 10
  pairs (e.g. USDINR +6.68 % YTD). First attempt failed on `with conn:` closing the connection (psycopg 3) — fixed with `conn.transaction()`.

- [x] ✅ **Telegram digest bot** — deployed + validated 2026-10-09 (ADR 0009).
  08:00 IST (only job with explicit timeZone), pinned pi1; Secret `telegram-bot`
  applied out-of-band; test digest delivered to chat; first scheduled run
  2026-10-10 08:00 IST. Rollout gotchas recorded as finds F29–F32.
  Detail: findings Tier 1 #6, §3; ADR 0009; `manifests/telegram/`.
- [ ] 🔶 **Grafana alerting tuning** — Telegram contact point + policy tree +
  rules deployed AND live-fire-validated 2026-10-09 (FII rule fired on real data: −12,944 Rs cr).
  **2026-10-12: retuned and applied — VIX 30, FII −5000, sentiment-drop rule now live (4 rules, health ok).** Remaining: re-tune FII/sentiment at ~60 days of
  history (only 8 FII days and ~2 weeks of sentiment exist) — **the only remaining part.**
  **Stale-feed noise — FIXED 2026-10-18:** the self-expiring `stock_fundamentals` clause is removed from the rule (the 10-17 pass proved out:
  heartbeat fresh, last_data 10-17), applied via `apply-grafana-provisioning.sh`, Grafana restarted on pi3, all 4 rules `health=ok`, stale-feed
  `inactive`. Root cause of the weekend noise found and fixed in `pipeline-health:v5` (stock_prices/stock_scores were 48 h → paged every Sunday;
  now 96 h like the other price feeds — the policy written in the script's own header). Detail: findings Tier 1 #1; architecture §12.4; ADR 0009.
- [x] ✅ **Telegram two-way interface** — deployed + validated 2026-10-09:
  listener Deployment `telegram-bot` (pi1, image v2, long-poll getUpdates —
  CGNAT/F10 rules out webhooks), single-user allow-list, backlog drain on
  start, command menu registered via setMyCommands. Commands validated from
  the phone: /status /nifty /digest /scores, zero errors. Full set: /status
  /vix /nifty /sensex /gold /fii /scores /digest /help. Strata-backed chat
  (doc §4 Tier 3) remains blocked on Strata's 127.0.0.1-only bind +
  workstation-uptime decision.
  Detail: `market-data-telegram-bot.md` §1/§3.6/§4; ADR 0009.
- [x] ✅ **Entity/event extraction on headlines** — done 2026-10-09 (ADR 0010):
  `headline-ner` CronJob, TX2 at 00:45 PDT, image v3. Gazetteer-first
  (deterministic ticker_names matching, no model) → `headline_entities`
  (instrument-linked; 323 rows over the 563-headline history) +
  `headline_events` (16-type keyword taxonomy; 163 rows). False-positive
  stoplist tuned from measured canary data (v1→v3: 380→323). Model-based NER
  stays on the 3090 layer (roadmap §7). Feeds findings #10 (per-company
  sentiment) — buildable anytime. Detail: ADR 0010; ops manual §3/§4.
- [x] ✅ **RBI G-Secs / repo rate feed** — done 2026-10-09, no third-party feed
  needed: `rbi-fetcher` (07:00 PDT) scrapes the official RBI mobile "Current
  Rates" widget → 7 policy rates into `interest_rates` (IN_REPO 5.50 etc.),
  5 G-Sec tenor buckets + 3 T-bill cut-offs into `bond_yields`
  (IN_GS_10Y 7.29 on first run). Writes its own `pipeline_heartbeat` row
  ('rbi'). India yields panel added to the Bonds dashboard. Detail: findings
  Tier 2 #8; ops manual §4/§12.
- [x] ✅ 2026-10-26 **Nifty 500 / Auto / FMCG index sources — ALL restored.** Nifty 500 via Yahoo `^CRSLDX`
  (2026-10-09); Auto/FMCG via the NSE-direct leg in `nse-indices.py` (`nse-fetcher:v7`, fiidii cookie
  pattern): daily ^CNXAUTO/^CNXFMCG rows in the 11:15 run (canary 11/11), full 2015→today OHLC backfilled
  (2,925 rows each). Naming decision recorded: ticker = series identity, not source — CNX-family names
  next to ^CNXIT/^CNXPHARMA. Found + fixed on the way: the API caps 70 records/call (year asks silently
  truncate to mid-April) — `nse_range()` paginates with a truncation-resume guard, tested. AR-5 rider:
  the nse Dockerfile now installs from requirements.lock. v5/v6 were burned on the way (tz-mix bug,
  the 70-cap) — tags bumped per rule, broken images removed from the Pis, tarball store holds v7 only.
- [ ] 🔶 Per-job heartbeat v2 + fetcher-internal upstream guards — lands with
  the next NATURAL image rebuilds (don't rebuild 9 working images just for
  this). NEW jobs start on the v2 pattern directly (rbi-fetcher already writes
  its own 'rbi' row). ✅ 2026-10-18 `pipeline-health:v5` adopted `rbi` into its FEEDS (checks `max(ts)` of the
  `IN_*` interest-rate rows, 96 h) — a dead rbi job is now visible (its self-row alone could never go stale).
  `job_runs` table + `manifests/_common/mdjob.py` are ready for per-job records. Detail: roadmap §3.1/§3.2.
- [x] ✅ 2026-10-18 `news_sentiment`/`raw_headlines` unique constraints — **live, no rebuild needed** (`11-news-constraints.sql`). The premise
  "needs ON CONFLICT first" was checked and is false: news-fetcher already skips (headline, source, UTC day) and the scorer commits all inserts +
  `scored` flags in ONE transaction, so no re-run can collide. Indexes mirror those keys exactly (md5(headline) — no btree size limit); 0 existing
  conflicts; both verified to reject a duplicate. The 24 same-headline pairs on different days are feed republications and stay (decision recorded).

### Architecture review follow-ups (2026-10-10 — recommendations, none applied)

Full evidence and rationale: `architecture-review-2026-10-10.md`. Order = its §5 phases.
- [x] ✅ **AR-1 (Phase 0)** CronJob safeguards — **applied 2026-10-12 02:5x UTC to all 23 CronJobs (since un-suspended — live and protecting them)** (22 applied by
  this session after a `kubectl diff` review showing only the 4 safeguard fields; `pg-backup` was already on the new spec).
  Live values verified (`concurrencyPolicy Forbid`, `startingDeadlineSeconds`, `activeDeadlineSeconds`, TTL 604800); values +
  rationale in ops manual §3. Effect on resume: only runs due inside each job's start window fire — no 23-job catch-up storm.
  Remaining: canary the estimated deadlines (`headline-ner`, `stock-screener`, `stock-report`, Telegram) on their first live runs.
  **Checked 2026-10-12 08:25 UTC:** live values are in place (`Forbid`, start 3600 s; active deadlines headline-ner 1800, stock-screener 3600,
  stock-report 1200, telegram-alerts 600). `headline-ner` ran on schedule at 07:45 UTC and Completed inside its window; the others have
  only run as canaries — their scheduled slots are 09:15 / 09:30 / 09:45 UTC (telegram-alerts has **never** been scheduled yet).
  **DONE 2026-10-12 09:50 UTC — the deadlines held:** screener 23 s (limit 3600), report 12 s (1200), `telegram-alerts` 14 s (600), `stock-metrics` 3 m 27 s, all Complete
  with their OK markers (details in the maintenance-pass item above). Only `stock-fundamentals` (Sat 10-17) is still to be seen under its deadline.
  Earlier note: verify those three after 09:50 UTC today: `kubectl -n market-data get jobs` and the OK markers.
  **2026-10-12 08:46 UTC:** none of the three is due yet (09:15 / 09:30 / 09:45 UTC); `pipeline-health` ran on schedule at 08:45 and Completed, and `pg-backup`
  ran on schedule at 08:30 — `lastScheduleTime` for `stock-metrics` (due 09:00), `stock-screener`, `stock-report`, `telegram-alerts` still 10-10 / empty.
- [ ] 🔶 **AR-2/AR-3 (Phase 1)** — **groundwork only (2026-10-18):** `job_runs` table live (`06d-job-runs.sql`, SCHEMA-OK);
  helper module `manifests/_common/mdjob.py` (`connect()`, `write_window()` with one reconnect-retry, `job_run()` context
  manager that records rows/status/error and never fails the job on bookkeeping errors). Named `mdjob`, not `joblib`, so it
  cannot shadow the PyPI package. **No image uses it yet** — adopt per image at its next natural rebuild (copy into the build
  context), then the Grafana stale-feed rule can read `job_runs`. Untested against a real DB: add a test when the first image adopts it.
- [x] ✅ **AR-5 step 3 — image consolidation (~16 → 3 by dependency profile): COMPLETE 2026-10-30.** Built `local/marketdata-light:v1`, `local/marketdata-data:v1`, `local/marketdata-ml:v1` on TX2; tarballs archived + `SHA256SUMS.all` regenerated; imported on TX2 (light + ML) and pi1/pi3 (light + data). All 36 CronJobs + backfills + history Jobs updated to new images with `command:` entrypoints. Telegram image kept separate (pinned pi1). Old per-fetcher Dockerfiles/requirements.lock deleted; old images removed from nodes one by one (never `--prune`). `workloads.py --check` passes; `tests/run-all.sh` all green; canary-validated headline-ner (TX2) and fx-fetcher (pi1).
- [x] ✅ 2026-10-18 **AR-6/AR-7 — intent delivered, file move declined.** (ADR 0020) The goal ("safeguards live in one place") is now enforced by
  `tests/test_manifest_safeguards.py` in CI: all 23 CronJobs must carry Forbid / starting + active deadlines / TTL / history ≤ 3/1 / backoffLimit,
  `Never` pull for `local/*`, resource limits, no inline password/token env. The `src/ images/ deploy/ db/` move + `kubectl apply -k` was NOT done:
  it renames every path the runbooks, CI, `workloads.py` and two months of docs use and changes the owner's apply workflow, for no runtime benefit.
  Revisit only if image consolidation (AR-5 step 3) makes a restructure happen anyway.
- [x] ✅ 2026-10-18 **AR-8 — reader roles live.** (ADR 0017) `10-roles.sql` creates `grafana_ro` (SELECT on all tables + future ones via default
  privileges, `default_transaction_read_only = on`) and `telegram_app` (SELECT all + INSERT/UPDATE/DELETE on `watchlist`, `bot_users` only).
  Passwords generated straight into Secrets `grafana-db-ro` / `telegram-db` (never printed; set via stdin). Verified: grafana_ro reads, every write
  fails; telegram_app writes only its two tables. Grafana datasource + `telegram-bot` Deployment + `telegram-digest`/`telegram-alerts` CronJobs
  switched (env only, no rebuild); Grafana 4 rules `health=ok` + datasource query OK; bot `/nifty` card OK in the new pod; `pg_stat_activity` shows
  `grafana_ro`. Not done on purpose: a writer role for the 18 pipeline jobs (they also need DDL-free writes on every table — low gain, 23-job blast
  radius; revisit with AR-2 adoption). Fixture tests: added for stock_prices, financial_facts, EV-0, pull-backups, manifest safeguards.
- [ ] 🔶 **AR-9 (Phase 2)** GitHub Actions CI **written 2026-10-12** (`.github/workflows/ci.yml`: docs/manifest drift check, all unit + shell tests,
  manifest sanity, gitleaks; no secrets; verified step-by-step in a clean venv) — **has run on GitHub: run #1 failed (nodewatch test needed the local hosts file — fixed in `f3e203d`), #2 and #4 (the push of 2026-10-12, lint included) are green — docs/manifest drift, all unit + shell tests, manifest sanity and the full-history gitleaks scan**. Lint
  (syntax: `py_compile` + `bash -n`) added 2026-10-12 and the workflow now also runs on `feat/**` / `fix/**` pushes. ✅ 2026-10-18: real linters added —
  `ruff==0.17.1` on the fatal set `E9,F63,F7,F82` (syntax + undefined names, the DQ-3 bug class) and `shellcheck -S error`; both clean locally.
  Not enforced yet: F401/F841 (6 unused imports in fetchers baked into live images — clean each at its next rebuild: fdi-watch `os`, fx-fetcher
  `pandas`, stock_fundamentals `json`, telegram bot.py `timedelta`/`timezone`/`IST`). `tests/test_pull_backups.py` added to CI. Not yet run on GitHub
  (nothing pushed). Remaining: a branch/worktree per session so WIP is never committed by the wrong session.
- [x] ✅ 2026-10-18 **AR-4 — DB Guaranteed QoS + readiness probe live.** requests = limits = 1 CPU / 2 Gi (QoS `Guaranteed`, so the
  kubelet evicts anything else on the TX2 first), `pg_isready` readiness probe (no liveness — a restart during a long vacuum would hurt more than
  help). Applied 09:50 UTC in a quiet slot: 17 s total DB downtime, 0 restarts, data verified. Bot liveness not added: a dead bot process
  already restarts by itself; a hung long-poll needs a heartbeat in `bot.py` (do it at the next telegram image change).
- [ ] 🤔 **AR-10 (Phase 3, decisions/hardware)** ~~control-plane `state.db` backup~~ ✅ first copy 2026-10-18 (below), stronger
  control-plane host, DB-host/GPU-host separation, off-site backups, schedule timezone
  (decide before 2026-11-01, ADR 0002 revisit).
  - ✅ 2026-10-18 **control-plane backup, one-off:** on pi2 (load 0.44) Python's `sqlite3` online-backup API copied `state.db` (pi2 has no
    `sqlite3` CLI; the API gives a consistent copy while k3s runs), `PRAGMA integrity_check` = ok, 2,169 kine rows; plus the server `token`.
    Archive pulled to `out/backups/k3s/k3s-cp-backup-2026-10-18.tar.gz` (gitignored, mode 600 — **holds the cluster join token**), sha256
    `851c663e…` identical on both ends, removed from pi2 `/tmp`. ✅ **Recurring since 2026-10-18:** `ops-tools/pull-k3s-backup.sh` (adds
    `server/tls` + `server/cred` so a rebuilt pi2 keeps the cluster CA; load gate: skips if pi2 load1 > 3; keeps 14), workstation cron 03:30 local;
    restore steps in ops manual §9. 🔶 **Restore never rehearsed** — drill it on a spare SSD before relying on it. Cron needs the workstation on at 03:30.
  - Off-site copy: **needs an owner decision** — no cloud tool or remote is configured on the workstation (no rclone/restic/aws/b2). Pick a
    destination + encryption (e.g. `restic` to B2/S3, or rclone crypt); dumps hold `bot_users` chat ids and the k3s archive holds the join token.
- [ ] 🔶 **AR-11 (Phase 0)** fix `pg-backup` — **applied + canary-validated 2026-10-11 ~23:40 UTC (not by this session — applied
  by the owner or another session; I only found it)**: live spec carries the new script; pi3 holds `marketdata-2026-10-11.dump`
  (171 MB) + `.sha256` (`sha256sum -c` OK 2026-10-12), the 0-byte 10-10 file and the 10-03 dump are gone. The CronJob is still
  un-suspended 2026-10-12 with the rest. **Restore drill DONE 2026-10-12 (off-cluster) and re-run 2026-10-17 (in-cluster):** the 171 MB 10-11 dump restored into a
  local `timescale/timescaledb:2.30.1-pg16` container in **30 s, 0 errors**; re-run 2026-10-17 in-cluster via `restore-drill-job.yaml`
  in **289 s (≈5 min)**, RESTORE-DRILL-OK, index_prices row counts match (live=292,577 = drill=292,577). RTO now ≈5 min at current size.
  **Second copy:** `ops-tools/pull-backups.py` run 2026-10-17: pulled marketdata-2026-10-17.dump (173 MB, sha256 ok) and
  marketdata-2026-10-16.dump (172 MB, sha256 ok) to workstation; newest verified dump 22.2 h old (within 36 h limit).
  ✅ 2026-10-18: daily at 03:00 local from workstation cron (`pull-backups.py --prune-local 7` → `out/logs/pull-backups.log`); new opt-in
  `--prune-local N` rotates only dumps dated ≥ 2026-10-18 whose sidecar verifies (older/hand-kept copies such as the 10-13 pre-migration dump are
  never touched; `tests/test_pull_backups.py`, 3 tests). Workstation `/home` is 94 % full — that is why rotation exists.
  Remaining: off-site copy (owner decision), and an
  in-cluster staleness alert (the Grafana `stale-feed` rule only counts `fresh=false` rows from the health job's own FEEDS, so a self-written row
  cannot go stale — needs a rule change or a health-job rebuild; run `pull-backups.py` from cron meanwhile). `tests/test-pg-backup.sh` 21/21.
- [x] ✅ **AR-12 (Phase 1)** — re-verified 2026-10-12 04:30 UTC: all five hosts logging every 5 min, `nodewatch` cron present on pi0. Repair/verify the 5-min stats logger (one sample on pi0/pi1/pi3; **all five hosts fixed 2026-10-11**: each
  root crontab was empty — entry `*/5 * * * *` added on pi0/pi1/pi2/pi3/TX2 (all logged at 23:10 UTC), so the 24 h
  acceptance record runs from ~23:10 UTC 10-11. Second bug found and fixed the same day: the script wrote its CSV header
  only if the file did not exist, so after each weekly logrotate (empty file left behind) the header was skipped —
  now `-s` (non-empty); header prepended to the four Pi files; rotation case tested on pi0; the script is now in the
  repo as `ops-tools/node-stats-log.sh`. pi2 note: 133 MB available + 77 MB swap in use at 23:07 UTC (watch)) — the power fix's 24 h acceptance depends on it; `nodewatch` on pi0 deployed 2026-10-11 (see §3); decide the watcher /
  power-controller host (pi0 vs MCU vs smart plugs; relays normally-closed; external dead-man's switch).

### Prioritised implementation order (value × effort — `stock-screener-implementation-plan.md` §16)

P0 do first · P1 next · P2 after · P3 later. Ratings are my priors; re-rate after the first EV-0 run.
- [ ] 🔶 **P0 (in order):** **G-1/DQ-1** adjusted prices + corporate actions → **G-2a** weekly membership snapshots, **EV-2**
  keep fundamentals snapshots, **G-3/PV-0** prediction ledger + outcome resolver (parallel; design in `prediction-validation-and-training-2026-10-10.md`) → **EV-0 + ML-0** test harness → **EV-1** historical
  membership. Prerequisite: power + backup fixes (§1, AR-11).
- [ ] 🔶 **P1:** ~~**G-6** India fundamentals source~~ ✅ decided + built 2026-10-18 (NSE filings, ADR 0021 — see History backfills) · **EV-3/4/5/7** price signals under
  EV-0 · **EV-9 → EV-10…13** statement lines + signals · **ML-2** store embeddings now · **PV-1** model-health dashboard + alerts.
- [ ] 🔶 **P2:** **EV-17/18** composite v2 + measured confidence (after EV-0 results) · **EV-14/15/6/8** · **G-9** US insider +
  short interest · **G-10** India promoter pledge/holding · **EV-16** portfolio rules + risk dashboard · **ML-1/3/5/7a** ·
  **G-12** breadth/sector rotation · **G-13** macro series · **G-18** second price source · **PV-2/PV-3** extend the ledger (sentiment, alerts, confidence) + drift monitor · **TP-0/TP-1** training infrastructure (registry, shadow, promotion) + scheduled refits.
- [ ] 🔶 **P3:** **ML-4/6/7b–d** · **G-11** India delivery/bulk/OI · **G-16** filing-text analytics · **G-17** mid/small-cap
  universe · **G-19** 13F holdings · **TP-2** GBM quarterly retrain + gate.

### LLM layer (2026-10-18 — ADR 0022, 0023; gateway repo `../llm-gateway`)

- [x] ✅ 2026-10-18 **Shared LLM gateway built and running** (separate repo `piCluster/projects/llm-gateway`, ADR 0001/0002 there): LiteLLM
  1.106.0 on the TX2 (namespace `llm`, 642 MiB), capabilities `fast-json`/`general`/`reasoning`/`long-context`/`embed` with cross-family failover,
  NVIDIA rpm capped at 30/min, per-project keys (marketdata: free capabilities only, 10 req/min), own `litellm` DB. `ops/smoke.sh` → SMOKE-OK
  (failover, key scoping 403, rate limit 429); OpenAI SDK from the workstation via NodePort :30400 OK. Gotcha recorded: `model_info.tier` is
  reserved by LiteLLM. Repo on GitHub since 2026-10-19: `git@github.com:ahemanthyadav/llm-gateway.git` (`main`).
- [x] ✅ 2026-10-18 **ML-7a daily market note — built (live 2026-10-19, see below).** `manifests/llm/narrative.py` (CronJob `llm-narrative`,
  07:40 IST, runs on the pipeline-health image + ConfigMap — no build): facts JSON (public data only) → gateway `fast-json` → every number must
  appear in the facts (one retry naming bad numbers) → `llm_narratives` (status ok/invalid/unavailable). Digest `telegram-digest:v9+` shows it only
  when status = ok (`DIGEST_NARRATIVE=1`). Canary: path proven up to NVIDIA ("api_key must be set"). 11 tests.
- [x] ✅ 2026-10-18 **Forgiving `/stock` `/news` `/watch` lookup** (ADR 0023, `telegram-digest:v10`, fixed in v13): fuzzy spelling correction (no AI) →
  AI only for nicknames, validated against the universe → `/watch` never writes on a guess. Live in the bot pod: "infosis" → INFY.NS,
  "microsft" → MSFT, "hdfc bnk" → HDFCBANK.NS; "facebook" falls back gracefully until the key exists. 10 resolver tests on a real-universe sample.
  Also in v9/v10: telegram image installs from a full frozen `requirements.lock` (AR-5), unused imports removed from `bot.py` (F401).
  Image now v22 (v13→v22: resolver hardening, factcheck shared module + tolerance ladder, /ask).
- [x] ✅ 2026-10-19 **NVIDIA key placed; live.** `set-provider-key.sh` → `smoke.sh --live` SMOKE-OK (all 5 capabilities). Found live:
  many catalogue models are "not found for account" → chains rebuilt from a probe (gateway README); the reasoning primary
  returned EMPTY text at 400 tokens → `enable_thinking: false` on `fast-json` in the gateway + client headroom (narrative 1500).
  **First real ML-7a note: status ok, attempt 1** (Nifty/Sensex/VIX/flows/rupee — numbers and flow directions verified by hand).
  Symbol lookup fixed after live tests (ADR 0023 amendment, image v13): facebook → META, big blue → IBM, hul → HINDUNILVR.NS.
- [x] ✅ 2026-10-19 **Bake-off ran** (10 trading days × 5 candidate groups, scored with the full number+direction check):
  fast-json.2 (gemma-4) 100 % first-try / 11.7 s · general.2 100 % / 11.7 s · general (gpt-oss-20b) 100 % / 9.9 s ·
  fast-json (nemotron-lightning, thinking off) 90 % / 1.8 s · fast-json.3 90 % / 15.0 s. **Decision: keep the current order** —
  after the built-in retry the top four all reach 100 % final pass, and the current primary is ~6× faster with no JSON-fence quirk;
  reorder only if narrative quality regresses (gateway ADR 0002 has the numbers). Temp bake-off key cleaned up.
- [x] ✅ 2026-10-20 **Story-grouping threshold tuned: `STORY_SIM=0.82`** (env on the `news-embed` CronJob, applied; ADR 0024 amended with
  the evidence). Measured NN-distance distribution over 1,651 vectors + manual pair review: 0.86 caught only the near-duplicate spike
  (49 merges); the 0.10–0.16 distance band is same-story rewrites; 0.20+ unreviewed, so not 0.75. Accepted cost: date-bearing daily
  templates ≤ 3 days apart can merge. Existing story_ids keep (only NULL rows group).
  - [x] ✅ 2026-10-30 Telegram value labelling (this session): digest facts block labels "Sentiment (7-day avg of scored headlines)" and "{flow} net, latest day (Rs cr)"; quote table headers "Close / Day chg / YTD chg"; /stock card adds 52-week high/low, labels "Position in 52-w (% low→high)", "P/E (as reported)", "P/B (as reported)", screener scores labelled as percentile ranks with confidence meaning; /top tables use "Composite / YTD chg / Conf"; /track uses "Pick ret / Univ ret / Won"; /multibagger table uses "Score / Rev / EPS / ROE / Conf" with a footnote explaining Rev/EPS = revenue/earnings CAGR over fiscal years on file, ROE = return on equity, Score = growth 35 + quality 25 + valuation 15 + momentum 15 + size 10 (0-100), Conf = history 30 + earnings confirmed 20 + margin above gate 15 + cash flow 15 + ROE as reported 10 + price in upper 52-w half 10. All 138 stocks tests + full suite green.
  - [x] ✅ 2026-10-29 Story-cluster hand-check at STORY_SIM=0.82 — 105 multi-headline story groups checked, all genuine same-story merges (republications + same-event coverage across dates), no false merges. 2,884 embeddings, all with story_ids, 0 without. ML-7c story summaries next.
- [x] ✅ 2026-11-03 **ML-7a narrative watch → ML-7b review complete.** 17 runs (10-19 → 11-04): 13 ok (76.5%), 4 invalid (flow direction sign ×2, pct rounding ×1), 1 unavailable (gateway transient). Shortlist notes in `out/ml-7b-shortlist-notes.md`.
- [x] ✅ 2026-11-03 **ML-7b per-symbol narration implemented.** Fixes deployed: explicit flow direction labels (`fii_direction`/`dii_direction`) in facts JSON, widened pct tolerance (0.01→0.1) in `factcheck.py`, content-hash cache to skip unchanged symbols. CronJob `ml7b-per-symbol` (Saturdays 02:45 PDT, after stock-screener) writes to `llm_narratives` (kind=`per_symbol`, PK now `(ts, kind, symbol)`). Gateway rate limit (10 req/min) handled with 7s pause between calls; ~10 symbols/min processed. Schema migration `24-llm-narratives-symbol.sql` applied (symbol column + composite PK).
- [x] ✅ 2026-10-19 **Direction check live** (factcheck.py, shared by narrative.py and the bot): a direction word ("rose", "inflow",
  "sold"…) that contradicts the sign of the number it introduces fails the check and triggers the same retry; "fell to 18.4" style
  levels are skipped; the live 2026-10-19 note passes. 15 narrative tests.
- [x] ✅ 2026-10-19 **Gateway monitored + backed up**: pipeline-health v6 adds `llm_narrative` (last status-ok note, 56 h page) and
  `llm_gateway` (readiness probe) to `pipeline_heartbeat`; pg-backup dumps the `litellm` DB too (verified LITELLM-BACKUP-OK).
- [x] ✅ 2026-10-19 **ML-2 embeddings live** (ADR 0024): `news-embed` CronJob 01:00 UTC, pgvector `headline_embeddings`, all 1,651
  headlines embedded day one; serves `/ask` news lookup + story grouping. Semantic search + story digests = next (see ML-2 above).

### History backfills (found 2026-10-18 — coverage table: architecture §4.3a)

- [ ] 🔶 **G-6 India fundamentals from NSE filings — BUILT, backfill running** (ADR 0021). `nse_fundamentals.py` (`screener:v15`): three
  eras (archive HTML 2005–11, JSON 2012–24 incl. banks, integrated XBRL 2025→), consolidated preferred, all filings of a period kept,
  filing-date point-in-time; 11 tests on real captured filings (`tests/fixtures/nse/`). Canary in-cluster (TCS / HDFCBANK / DIXON): 259
  filings, 1,580 facts, 0 warn; TCS revenue FY2005 ₹9,749 cr → FY2026 ₹2,67,021 cr, FY2014 ₹81,809 cr (two parser bugs found by the
  canary and fixed before the full run: net sales vs gross income; restated filings dropped). Screener reads `nse_cons` + EDGAR, TTM from
  summed quarters where no YTD exists. **Run history: 2026-10-18 16:30 UTC → 2026-10-19 (23 h): 357/495 symbols, 24,662 filings, 153,978 rows, partial —
  hit MAX_MINUTES with the cursor kept. Resumed 2026-10-19 (`backfill-nse-fundamentals-resume`, manifest in
  `manifests/stock-screener/backfills.yaml`); the first resume hit its 2.5 h Job deadline (script cursor kept, nothing lost —
  the deadline now covers the script budget). **~100 symbols left, running (cursor at RVNL.NS). When a run logs
  `cursor cleared`:** (1) un-suspend CronJob
  `nse-fundamentals` (remove `suspend: true`, apply); (2) screener DRY_RUN diff for NIFTY top-50 (facts vs yfinance) before
  trusting India ranks — the live screener already reads it; (3) re-run EV-0 with India fundamentals; (4) update the coverage
  row in architecture §4.3a with final counts. Known gaps: ~13 symbols have 404-dead archive URLs on NSE's side (logged WARN,
  skipped) — e.g. HEGAM, COFORGE, ARE&M, KPIL, CEMPRO; re-run for them after NSE fixes, or accept the gap.
- [x] ✅ 2026-10-19 **`/ask` live** (`telegram-digest:v23`): free-text questions answered from DB facts only — subject found
  word-by-word via the forgiving resolver, facts JSON (public data only, news via `headline_embeddings` semantic search) →
  gateway `general` → every number AND direction checked by the shared `factcheck.py` (percent forms of given values and
  rounding/truncation to ≤0.01 tolerated; metric-key numbers like "12-month" allowed). Two failed checks or gateway down →
  the facts table is shown instead; unmapped questions are refused, never guessed. Live: TCS outlook, news infosys, usdinr,
  fii flows all pass the check. Tuning that made it reliable is factcheck's tolerance ladder (4 image iterations).
  ✅ 2026-10-20 **v23: stock facts now carry the real 52-week range** — `high_52w`/`low_52w` (bounded `max(high)/min(low)`
  on `stock_prices`, 52 w; 997/998 symbols) and `pos_52w` sent as `position_in_52w_range` (a live answer had misread it as
  "35% above the low"). Full §6 cycle: per-file sha256 identical, tarball `55a47c49…` checked at every hop + saved to the
  TX2 store (SHA256SUMS.all regenerated, 40 tarballs), imported on pi1 (21.8 s, `0x0`), applied (diff = 3 image lines),
  canary in the new pod: facts show 3350.0/1976.8 for TCS.NS and the gateway answer quotes them, factcheck ok. 8 ask tests.

- [x] ✅ 2026-10-19 **H-1 FX history done** — `manifests/ingestion/history/` (ConfigMap + Job per mode, `history_backfill.py`): 58,222
  rows, USDJPY from 1996, INR pairs from 2003/06 (Yahoo depth per pair); only pre-live dates written, ts = 00:00 UTC.
- [x] ✅ 2026-10-19 **H-2 US bond yields done** — ^TNX/^FVX/^TYX/^IRX from 1990 (36,884 rows) + FRED DGS2/1976, DGS10/1962, DGS30/1977.
  India G-Sec history has no stable source (RBI widget is current-only) — stays forward-only.
- [x] ✅ 2026-10-19 **H-3 Fed funds done** — DFF from 1954-07-01 (26,390 rows). RBI policy-rate history: forward-only unless a source is found.
- [x] ✅ 2026-10-19 **H-4 technical indicators done** — full history for all 38 index tickers (2.6 M rows), bar-for-bar identical to
  the live `ta-compute.compute()` (unit-tested); seamless with live rows (verified on the ^NSEI overlap).
- [x] ✅ 2026-10-19 **H-5 stock metrics done** — 5 years of weekly snapshots, latest-bar-of-week, live `stock_metrics.compute()`
  itself (2.39 M rows). Historical *scores* wait for EV-1 + point-in-time fundamentals.
- [x] ✅ 2026-10-19 **Chunk fix** (found by the backfills): fx/yields/rates/ta/metrics tables were still on 7-day chunks — decades of
  history created ~3,500 tiny chunks and DFF's COPY failed on "out of shared memory". Same fix as stock_prices (10-18): 1-year chunks for
  new data (06e), fx_rates/bond_yields 1990–2025 merged per year with the existing merge script; rows + sums verified identical before/after.
- [ ] 🤔 **H-6 FII/DII history** — ✅ depth CHECKED 2026-10-20: NSE's `fiidiiTradeReact` serves exactly ONE day (probed live) and no
  official NSE bulk archive exists; the only sources are third-party aggregator pages (Stockezee/Upstox day-by-day tables, no CSV)
  and a static 2018–24 research dataset (Mendeley). Decision for the owner: accept forward-only (like news), or adopt a third-party
  source with its quality/terms caveats. Our own history accrues daily since 2026-10.
- Not backfillable (record so nobody tries): news/sentiment (RSS is current-only), intraday beyond ~2 years, membership snapshots
  (needs EV-1), India fundamentals beyond ~4 years (G-6).

### Data correctness — prerequisite for EV-0 and every ML backtest (found 2026-10-10)

- [ ] 🆕 2026-10-26 **P2 · DQ-5 recurring accuracy cross-checks** — proposed after the on-screen-values review
  (which found+fixed F33 chart.js and F34 news-matching, finds §1.1b; the values themselves verified clean:
  Graham/peer/fair-value/upside recomputed exact from inputs for AAPL/ALL/HDFCBANK, closes & yfinance ratios
  consistent with public quotes within date tolerance, S&P membership dates AAPL 1982-11-30 / MSFT 1994-06-01 /
  ALL 1995-07-13 all correct). Candidate job `accuracy-check` (weekly, SELECT-only + one assertions report row):
  1. **Second-source price check**: rotating ~25-symbol sample, `stock_prices.close` vs Stooq free CSV
     (`stooq.com/q/d/l/?s=aapl.us`) on matching dates; flag |Δ| > 0.5%.
  2. **Internal consistency SQL**: `fair_value == median(method cols)` recomputed in SQL; `upside_pct` from
     fair/close; `market_cap/close` (shares) drift < 2% day-over-day without a split row; `pe × eps(yf TTM) ≈ close`
     where both legs are yfinance-sourced.
  3. **Sector-median recompute**: `sector_metrics.median_pe` vs a fresh `percentile_cont(0.5)` over members.
  4. **Membership spot-check**: `index_membership` current set == `universe_constituents` (drift guard already
     exists at write; this reads it back daily).
  Display note (no code change yet): the dossier "Fundamentals (latest)" table mixes EDGAR filing-basis rows
  (eps 21.73 for ALL) with yfinance TTM snapshots (pe 5.07 ⇒ TTM eps ≈ 50) — correct data, two bases; consider
  a basis column or footnote in marketview.

- [x] ✅ 2026-10-18 **P0 · DQ-1 (G-1) corporate actions / adjusted prices — DONE.** (ADR 0014) History: CTVA proven 2026-10-12 and repaired 10-13
  (`audit-price-adjustments.py`); split detection + resync in v10 (10-14); `corporate_actions` recording in v11 (10-17).
  **Completed 2026-10-18 in `screener:v12`/`v13`:** (1) **own total-return `adj_close`** (`adj_series()`: close × Π(1 − div/prev close) over later
  dividends; never Yahoo's Adj Close) recomputed per symbol after full-history writes or a dividend in the daily window; (2) **full actions
  backfill** (`backfill-corporate-actions`, 998/998, 0 warn: 58,866 dividends / 858 symbols, 2,030 splits / 630 symbols; plus the 3 delisted
  symbols WBD/PSKY/CTVA) — **0 NULL `adj_close` of 6,343,487 rows; `adj_close ≤ close` everywhere**; sanity: 10-year total − price return
  ≈ dividend yield (KO 3.4 %/yr, XOM 4.75 %, AAPL 1.1 %, BRK-B 0.0); (3) **`price_anomalies` writer** (`jump_gt_40pct`, `history_restated`;
  restated history now resyncs even without a split flag; the 2 newest bars are exempt); 22 tests. v12 canary: `PRICES-OK fetched=997
  anomalies=0 adj_updated=152223`. The weekly audit can retire after 4 clean weekly runs with the restatement detector live.
  Downstream (not done, separate decision): point `stock_metrics.py` returns/vol at `adj_close` — EV-0 shows small rank effects (§5a).
- [x] ✅ 2026-10-13 **P0 · DQ-4 `stock_prices` timestamp conventions + 4,434 duplicate rows — REPAIRED.** Found 2026-10-12: pre-10-02 history was stamped with exchange-local
  midnight (US `04:00`/`05:00` UTC, NSE `18:30` UTC of the *previous* day — 99.7 % of the 6.34 M rows) while newer rows are `00:00` UTC on the trade date; the overlap left 4,434 duplicate
  symbol-days (987 symbols, 09-28 → 10-02) and made every old NSE `ts::date` one day early (e.g. HEGAM.NS's 09-07 bar stored 09-06). **Fix (approved by the owner's "fix all that can be done"):**
  `ops-tools/sql/2026-10-13-stock-prices-normalise-ts.sql` — step 1 deleted the 4,434 duplicates, step 2 rewrote 6,320,102 rows (987 symbols, per-symbol transactions, each verified inserted == deleted)
  to `00:00` UTC of the trade date (`date_trunc('day', ts + 6 h)`). **Process:** fresh verified dump first (`marketdata-2026-10-13.dump`, 04:06 UTC, pulled to `out/backups/`); rehearsed on a local
  restore of the 10-12 dump (5.5 min, counts and dates verified); live run 04:08→04:43 UTC on the TX2 DB (~35 min, DB pod peaked 1.75 GiB of 2, no failures, nodes stayed Ready), then autovacuum
  cleaned up for ~10 min (a `count(*)` during that time was slow — cancelled). **Verified live:** 6,338,993 rows = 6,343,427 − 4,434; **0** non-midnight rows; 0 duplicate trade days; HEGAM.NS 09-07 = 272.20
  (matches Yahoo). **Decision recorded as ADR 0013.** **Step 3 (writer change) turned out unnecessary:** the current daily writer already produces `00:00` UTC rows (every row since 10-05 is), so the convention cannot drift back. Readers
  (`stock_metrics` row-count lookbacks) are now consistent; tomorrow's `stock-metrics` run is the first to score on the repaired series. Note: the Nifty 500 bar for "today" is a partial intraday bar until the
  next run overwrites it — harmless. Rollback if ever needed: restore from the pre-migration dump (no inverse UPDATE exists).
- [x] ✅ 2026-10-18 **P0 · DQ-2 fundamentals period + point-in-time — DONE (screener reads it live).** (ADR 0015) Problem (2026-10-10): EDGAR YTD
  quarters collapsed onto one key (median 3-y revenue CAGR −17.8 % vs +8.8 %) and `filing_published_at` was the latest re-filing.
  **Fix:** `financial_facts` writer in `stock_fundamentals.py` (v12): every 10-K/10-Q value keyed by duration and first filing date, repeated
  comparatives dropped (MSFT check: FY2024 NI $88.1 bn first filed 2024-07-30, not 2026; 2,189 raw entries → 829 facts); yfinance values with
  `filed` = first day observed. Full pass 2026-10-18: `FUNDAMENTALS-OK done=998/998 failed=0` → **305,422 EDGAR facts / 502 US companies,
  27,258 yfinance facts / 997 symbols.** Screener reader (v13, `FUND_SOURCE=facts`): EDGAR full-year series + TTM FCF (annual + YTD − prior
  YTD), point-in-time `filed <= as_of`, **stale series (> 460 days) fall back to yfinance**. Dry-run diff before flipping: NIFTY top-50 50/50,
  SP500 top-50 46/50 (changes = fresher 10-Ks, e.g. MU FY2026, and TTM FCF). **Flipped live 2026-10-18** (env on CronJob `stock-screener`;
  rollback = `FUND_SOURCE=yfinance`). Bug caught by the diff and fixed before the flip: CBOE's EDGAR revenue stops in 2017 (tag change) →
  scored on a 9-year-old CAGR in v12's dry run; v13 guard. Logs: `out/screener-dry-{yfinance,facts}.log` (gitignored).
  **Follow-up (EV-9) — map extension DONE 2026-10-20 (`screener:v16`), verification pass running:** stale series re-measured (100:
  17 revenue / 54 capex / 29 net income), every stale company's companyfacts probed for which fresh 10-K/10-Q tags it actually files,
  and `EDGAR_MAP` extended append-only: revenue += `RevenueFromContractWithCustomerIncludingAssessedTax`, `RegulatedAndUnregulated
  OperatingRevenue` (utilities), `RevenuesNetOfInterestExpense` (banks); net_income += `ProfitLoss`, `NetIncomeLossAvailableToCommon
  StockholdersBasic`; capex += `PaymentsToAcquireProductiveAssets`, `…OtherProductiveAssets`, `PaymentsForCapitalImprovements` (REITs).
  Rejected on semantics: M&A/securities payments, `CapitalExpendituresIncurredButNotYetPaid` (accrual). Canary on real filings
  (DUK/MS/CAT/AMZN): 0 rows lost, each target healed to 2026 periods. First-wins is per (metric, end, duration) key, so old series
  are untouched (new unit test). Known unfixable: `VMRK` (no fresh tag), `ARES` capex, financial companies with no capex by nature.
  §6 cycle: per-file sha256 identical, tarball `fe692042…` at every hop + TX2 store, imported pi1+pi3, 7 CronJobs bumped (diff = 7
  image lines). One-off Job `fundamentals-ev9` running on pi3 — **verify stale counts drop (~100 → ~35) when it completes**.
  Telegram `stocks.py` `series()` still reads the old path (own image).
- [x] ✅ 2026-10-17 **P0 · DQ-3 weekly fundamentals job — PROVEN LIVE.** Fix shipped in v9 (`failed` initialised; `upsert()` keeps the earliest
  `filing_published_at`). The Sat 2026-10-17 run (`stock-fundamentals-29870700`, 13:00→14:40 UTC, Complete) is the first real pass on it.
  Its pod log was lost (likely in the 10-18 site-wide power event), so the proof is DB-side: `job_state.stock_fundamentals` cursor = NULL
  at 14:40:55 UTC (the clear that used to NameError) and `pe`/`pb` now hold 2 distinct snapshot dates (10-04, 10-17) — fundamentals refreshed.
- [x] ✅ 2026-10-26 **P1 · G-20** factor returns LIVE (`factors-fetcher:v1`, monthly CronJob `0 11 2 * *`, the
  27th job): US Ken French 5-factors+MOM (1963→, MOM 1927→) + India IIM-A survivorship-adjusted four-factor
  library (1993-10→; WML→MOM, MF→MKT_RF as published) into `factor_returns` — 7,674 rows, canary verified.
  Terms checked 2026-10-26: Ken French page carries a bare copyright (standard research-use practice,
  attribution in the fetcher header); IIM-A asks for the Agarwalla–Jacob–Varma (2013) citation — recorded in
  the fetcher header. **First AR-2/AR-3 adopter**: mdjob `write_window` + `job_run` (first `job_runs` row:
  ok/7674). The release-stamped IIM-A filename is scraped from the index page each run.
  Still open from this item: extend EV-0 §5 test 4 to "beyond the standard factors" (an ev0.py change —
  do it with the next scoring change, EV-0 discipline).
- [ ] 🔶 **P2 · G-21** extra signals testable from data on hand: net payout yield (Boudoukh et al. 2007), distress score,
  Amihud illiquidity (2002), max daily return / idiosyncratic volatility (Bali et al. 2011), industry momentum
  (Moskowitz & Grinblatt 1999) — citations from memory, verify; each must pass EV §5.
- [ ] 🔶 **P2 · G-22** S&P symbols without EDGAR rows — **root cause found 2026-10-12: not a `cik` gap.** All 503 active S&P rows have a CIK; 10 lack EDGAR
  rows: `A, AAPL, ABBV, ABNB, ABT, ACGL, ACN` (the first 7 alphabetically — they missed the first full pass) and `SKYD, TWLO, VYLR` (added by `universe-refresh` after it). SEC
  `companyfacts` answers HTTP 200 with data for 9 of the 10 (e.g. AAPL 441 annual/quarterly facts); only `VYLR` has none (new spin-off). The weekly job re-processes the whole
  universe, so **the Sat 10-17 pass should fill them — verify then** (active S&P symbols with EDGAR rows should rise from 493 to 502); if not, run a
  `BACKFILL=1` canary for those symbols. ✅ **Verified 2026-10-20: 502/503 active S&P symbols have EDGAR rows**; the only gap is `VYLR`
  (SEC has no companyfacts for it yet — new spin-off, SEC-side, re-check in a quarter). CLOSED.

### Evidence-based signals (plan 2026-10-10 — `evidence-based-signals-roadmap.md`)

- [x] ✅ 2026-10-18 **EV-0 signal test harness — built and run.** (ADR 0018) `ops-tools/ev0.py export|run` (6 synthetic-data tests in CI; export via the
  read-only `grafana_ro` role): monthly Spearman IC + Newey-West t, halves, quintile spread net of 0.2/0.4 % costs, turnover, Fama-MacBeth
  with the composite's price inputs as controls. First run 2001–2026, 9 price signals × 2 universes × {adj_close, close}: **nothing passes §5
  (both universes required)**. NIFTY 500: 12-1 momentum IC 0.045, t 4.2, +13.5 %/yr net — strong; S&P 500 momentum ≈ 0 and low-vol/low-beta
  *negative* = survivorship artefact of today's constituents. `pos_52w` adds nothing after controls. Full table + reading: evidence roadmap §5a.
  **Composite unchanged** (per §5). Next: EV-1 (blocking for US), then composite v2 (EV-17) — per-universe rules are an owner decision.
- [x] ✅ 2026-10-18 **EV-2** — statement history now accumulates in `financial_facts` (EDGAR: one row per first filing / restatement;
  yfinance: a new row only when Yahoo's value changes, `filed` = first day observed). Ratio rows already keep their fetch-date `ts`
  (`pe`/`pb` hold 10-04 and 10-17 snapshots).
- [x] ✅ 2026-10-18 **G-2a weekly membership snapshots** — `universe_refresh.py` (v12) appends `universe_membership_snapshots` every Sunday;
  first snapshot seeded 2026-10-18 (503 SP500 + 495 NIFTY500). Survivorship-free backtests become possible only as this accrues (EV-1 still
  needed for the past).
- [ ] 🔶 **EV-3/EV-4/EV-5/EV-7** — EV-3/4/5 **tested 2026-10-18** (EV-0 §5a): 12-1 momentum passes on NIFTY only; near-high and beta fail
  both. Not added to `stock_metrics` until composite v2 is decided. EV-7 (index trend flag) is a display item, not a score — not started.
- [ ] 🔶 **EV-1 historical index membership — now the BLOCKING item for US signals** (EV-0 shows survivorship dominates S&P results).
  Sources: S&P 500 change history; NSE reconstitution circulars. Snapshots accrue weekly from 2026-10-18 (G-2a) but cannot fill the past.
- [ ] 🔶 **EV-9** more statement lines (total assets, gross profit, operating cash
  flow, shares) → **EV-10…EV-14** profitability, investment, accruals, issuance,
  F-score / G-score.
- [ ] 🔶 **EV-17** composite v2: replace the growth factor with profitability +
  investment, equal weights. **EV-18** replace the heuristic confidence score with
  measured IC and hit rate.
- [ ] 🔶 **EV-1** historical index membership (removes survivorship bias);
  **EV-6/EV-8/EV-15/EV-16** risk-scaled and residual momentum, post-earnings
  drift, portfolio rules.

### ML roadmap (plan 2026-10-10 — `ml-roadmap-2026-10-10.md`; reuses EV-0, never bypasses EV §5)

Labels = research backing of the idea (legend in the roadmap): 🟢 research-backed · 🟡 conditional · 🔵 engineering
practice · ⚪ unproven here · ⚠️ citation not verified.

- [x] ✅ **AI-assisted stock name resolution** — `manifests/telegram/nicknames.py` (nickname → symbol map, zero network), `manifests/telegram/resolver.py` (nickname → fuzzy → AI gateway layers), `manifests/telegram/ml0_splitter.py` / `ml0_leakage.py` (ML-0 experiment infra). `tests/test_nicknames.py` / `tests/test_router.py` / `tests/test_resolver.py` all green.
- [x] ✅ **Natural-language command routing** — `manifests/telegram/router.py` (free text → command + args), `manifests/telegram/bot.py` (unknown text auto-routed; `/route` command for explicit testing), `manifests/telegram/Dockerfile` (router.py + nicknames.py copied into image).
- [x] ✅ **Command trigger simplification** — users can type "tcs price", "facebook quote", "what's nifty at", "show me fii flows" instead of memorising /command syntax. Router checks nicknames → phrases → keywords → single words.
- [x] ✅ 2026-10-31 **Symbol-aware routing** (image `telegram-digest:v37`, deployed pi1) — `/status cub`, `/movers cub`, `/top tcs stock`, `/statsus cub` all reach `/stock`. Router got a DB-backed last layer (`bot.symbol_lookup` → `stocks.pick_one_ex`, so the live universe wins over the static map), `ARG_COMMANDS` so `/news|/watch|/top` keep their arguments, `router.symbol_from_args` for the pre-dispatch check, longest-nickname-prefix matching, and an exact-command match against `bot.COMMANDS` (not only the keyword map). Nickname entries that pointed at dead symbols were realigned (CUB, ETERNAL, TMPV/TMCV, POLICYBZR, NYKAA, RBA, LTF, KARURVYSYA, UJJIVANSFB, UTIAMC) and four no-longer-listed ones dropped. Tests: `tests/test_router.py` (26 cases, fake lookup, no DB); live resolver verified in the v37 pod.
- [ ] 🔶 **ML-2 🔵** news embeddings + story clustering — already LIVE
  (`headline_embeddings` + `news-embed` CronJob since 2026-10-19). Remaining:
  threshold tuning, ML-7c story summaries.
- [ ] 🔶 **ML-4 ⚪** regime model — descriptive only until EV-0 shows out-of-sample
  difference; needs longer index history.
- [ ] 🔶 **ML-5 🟡** peer clusters — `app.peer_clusters` table created in schema 23;
  workstation batch → table. Tests against official sectors; must pass EV-0 §5.
- [ ] 🔶 **ML-6 🟡** GBM ranker — `app.model_scores` table created in schema 23;
  **hard-gated** on EV-0 + EV-1 + composite v2 passing EV §5; weakest fit for
  large caps, expect small/null.
- [ ] 🔶 **ML-8 🔵** TX2 stays CPU; DB move (AR-10) frees it for ML.
- [x] ✅ **LLM provider key placed — verified 2026-10-30.** Provider key present in the gateway secret,
  gateway `ready=1/1`, readiness probe 200, all 12 models/capabilities listed via authenticated
  `/v1/models` (fast-json/.2/.3, general/.2, reasoning/.2, long-context/.2, embed/.2, selftest/.2).
  Provider free tier (ADR 0022) is live; model ids come from the bake-off.

## 2. Security & hygiene (this week) — restored 2026-10-12

- [x] ✅ **Secrets kept out of git** — done 2026-10-10: single gitignored local store `.secrets.env` (template
  `.secrets.env.example`); docs/tools carry no values (`@@PW@@` token + `ops-tools/_creds.py`); secret manifests +
  `validation/grafana/` gitignored; agents denied read/edit of `.secrets.env` in `opencode.jsonc`. Credential rotation is
  explicitly NOT a priority (owner decision 2026-10-10). Detail: ops manual §10.
- [ ] 🔶 **Verify the CGNAT claim** — 2026-10-10 measurement: the public IP this LAN egresses with EQUALS the router's WAN IP
  (docs concluded CGNAT from the opposite). ✅ **Re-measured 2026-10-20** (two IP-echo services, compared programmatically, never
  printed): egress STILL equals the stored router WAN_IP, 10 days apart; the old EXTERNAL_SEEN_IP matches neither — the original
  CGNAT conclusion rested on a stale value. **Working conclusion: NOT CGNAT.** Remaining: one glance at the router's WAN page (or a
  phone-off-Wi-Fi check) to close it. Consequence already actionable: the UDP 51820 item below is now a **security action**, not
  housekeeping. Detail: ops manual §12.9. **Gates the pi0 DNS item below.**
- [ ] 🔶 **pi0 DNS over the tailnet (ad-block on the move)** — pi0 is on the tailnet and the admin page works, but a DNS query from
  pi1 over the tailnet timed out: Pi-hole `dns.listeningMode` is `LOCAL`. Needs a decision on the listening mode (after the CGNAT
  check) and the tailnet nameserver set in the admin console. Detail: ops manual §12.6.
- [x] ✅ 2026-10-20 **Router UDP 51820 port-forward — CLOSED: verified ABSENT.** Owner checked the router admin panel: the
  port-forwarding page is empty — no rules exist, so the "stale WireGuard forward" in the old docs no longer describes this router.
  Nothing was reachable; nothing to remove. (If the panel has a UPnP active-mappings table, one glance there is a free extra check.)
  Detail: roadmap §11; ops manual §12.9.
- [x] ✅ **Find out who messaged the bot — resolved 2026-10-31.** It was `sailu`; `bot_users` already has 2 active rows named `sailu` (one private chat, one group chat). Allow-list confirmed working (the stranger chat was ignored). Item closed.
- [x] ✅ 2026-10-12 pi2 cordon dated: taint `node.kubernetes.io/unschedulable` `timeAdded` = 2026-10-10 06:11:25 UTC (during the outage
  recovery). Reason inferred from timing, not recorded — owner may confirm; **keep it cordoned** (1 GB control plane).

## 4. v2+ (target architecture — build on v1's tables)

- [ ] Sector-relative 6-component scoring + classifications. Detail: target
  architecture §5–§6.
- [ ] Style tags + investor lenses (Phase 3.5; DCF lens deferred to Phase 5).
  Detail: target architecture §13.
- [ ] Market-regime layer (Phase 3.6) — FRED-sourced series (T10Y2Y, HY OAS,
  Buffett indicator) buildable anytime via the existing rates-fetcher
  pattern. Detail: target architecture §14.
- [ ] RTX 3090 AI layer (WoL) — LLM summaries, embeddings at scale, RAG.
  Detail: roadmap §7; findings Tier 3 #13.
- [ ] GraphRAG — after entity extraction has produced reliable history.
  Detail: target architecture §8; findings Tier 3 #14.
- [ ] 🤔 Optional depth decisions (no action unless wanted): FMP free tier for
  US fundamentals history; LLM narration hook for reports; Timescale columnstore
  compression on `stock_prices`.

## 5. Recurring (never "done" — calendar items)

- [ ] 🔄 Daily: `/health` checklist (ops manual §7). Automatic: 23 CronJobs.
- [ ] 🔄 Weekly (until the daily split detection ships — DQ-1): `PSQL_CMD=… python3 ops-tools/audit-price-adjustments.py audit`; repair any mismatch with `emit-repair <SYM>`
  (reviewed SQL, one transaction per symbol). Last run 2026-10-13: only CTVA, repaired.
- [ ] 🔄 Monthly (1st): fdi-watch logs DPIIT links → 5-min manual INSERT.
- [ ] 🔄 Yearly (January): merge the previous year's weekly chunks of `stock_prices`/`index_prices` (ADR 0016;
  `ops-tools/sql/2026-10-18-merge-weekly-chunks.sql`, verified dump first). Due 2027-01 for the 2026 chunks.
  Detail: ops manual §11.
- [ ] 🔄 Quarterly: timed restore drill (`restore-drill-job.yaml`), after any
  schema/backup change too. Detail: ops manual §9.
- [ ] 🔄 After ANY TX2 apt work: re-verify iptables wrappers (ops manual §8).
- [ ] 🔄 eMMC >85% alert → prune TX2 docker cruft (ops manual §7 step 7).
- [x] ✅ 2026-10-10 TX2 k3s containerd store moved to the SSD (bind mount), eMMC
  copy + 19 stale tarballs deleted: eMMC 84% → 54%. Detail: ops manual §2.
- [x] ✅ 2026-10-10 `local/headline-ner:v3` was missing from the TX2 k3s store
  (`ErrImageNeverPull`); imported from the tarball, the pending job Completed.

## 6. Icebox 🧊 (deliberately declined — revisit triggers noted)

- JetPack 5 reflash (lifts CUDA/cgroup ceilings; recovery-mode, full revalidation)
- Longhorn/OpenEBS · Loki · Argo/Tekton · Citus (pi2 1 GB ceiling — ADR 0008)
- 4th Pi worker · streaming ingestion · public-website/VPS front
  Detail: roadmap §10; findings icebox; ADRs 0008.

---

*When something here completes, record the ✅ here first, then update the
owning doc (schedule tables, gap lists, rollout statuses) in the same session —
this tracker and the docs must never disagree.*
