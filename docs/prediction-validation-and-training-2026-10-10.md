# Prediction Validation & Training Pipeline — 2026-10-10

> **Status:** PV-0 live via `prediction_snapshots` + `prediction_outcomes` (`predictions.py`, ADR 0028, 2026-10-27);
> Continuous Classification & Factor Validation Loop live via `ops-tools/classifier_loop.py` (ADR 0033, 2026-11-08).
> Remaining items PV-2…PV-3 and TP-0…TP-2 are tracked in `pending-tasks.md`.
> **Purpose:** one cross-cutting pipeline so that **every daily output that makes a claim** — a score, a rank, a
> forecast, a regime label, a classification, a narrative — is (1) logged immutably when it is made, (2) resolved against
> what actually happened, (3) scored against a baseline, and (4) monitored for decay and drift; and so that **components
> that learn** are retrained offline, gated, run in shadow, and only then promoted.
> **Relation to other plans:** it implements the "forward-test ledger" (G-3) and the measurement side of the EV roadmap
> (`evidence-based-signals-roadmap.md`, acceptance test §5) and the ML roadmap (`ml-roadmap-2026-10-10.md`, ML-0 / ML-9).
> It changes no signal's acceptance rule. Research labels as in the ML roadmap: 🟢 research-backed · 🟡 conditional ·
> 🔵 engineering practice · ⚪ unproven here · ⚠️ citation not verified.
> **Backing: 🔵 Engineering practice** for the plumbing; the *evaluation method* (out-of-sample testing, counting trials,
> deflating results) is **🟢** — Bailey, Borwein, López de Prado & Zhu and Bailey & López de Prado (verified, ML roadmap §9)
> show why in-sample/hold-out results mislead and why the number of trials must be tracked. Live forward testing is the
> practical antidote because nothing in it can be tuned after the fact.

## 1. Principles

1. **Write-once.** A prediction is recorded when it is made, with the information available *then*, and is never updated.
   (This is a deliberate exception to the house DELETE+INSERT writer pattern of ADR 0005, which would rewrite history on a
   re-run — see §4 and §10.)
2. **Judge against a baseline, not against zero.** Every kind has a pre-registered baseline and metric.
3. **Pre-register thresholds** (what counts as "working", "degraded", "promote") before looking at results.
4. **Low power is the default.** Monthly-horizon predictions give ~12 independent observations a year; the live ledger
   *monitors and detects decay*, it does not prove skill quickly. Acceptance of a *new* signal stays with the EV-0 backtest
   (§5 of the EV roadmap); the ledger keeps everyone honest afterwards.
5. **Correct returns first.** Outcomes are computed from adjusted, total-return prices — this pipeline **depends on G-1 / DQ-1**
   (corporate actions and dividends). Resolving outcomes on today's raw `close` would score predictions against wrong numbers.
6. **Models never train on the cluster.** Training runs offline on the workstation; Pis only log, resolve, score and serve.
7. **Optional and non-blocking.** If any step fails, the daily digest, report and scoring still run.

## 2. Inventory — what is "predicted" today and what is planned

| Output | Where produced | Claim being made | Horizon | Outcome to compare | Primary metric | Baseline | Status |
|---|---|---|---|---|---|---|---|
| **Composite score / rank / shortlist** | `stock_scores`, `stock_report` (daily) | Higher-ranked names outperform | 1 / 3 / 6 / 12 months | Excess return vs universe equal-weight and the index | Rank IC (Spearman) per date, top-minus-bottom quintile spread, top-25 hit rate vs median, turnover | Equal-weight composite; random | **Live (implicit claim)** |
| Factor scores (value, quality, growth, momentum, risk) | `stock_scores` columns | Each factor ranks future returns | same | same | Per-factor rank IC | Composite; other factors | **Live** |
| **Report "confidence" score** | `stock_report` | "How sure" — heuristic, not a probability (EV roadmap §3) | 3 months | Whether high-confidence names beat low-confidence names | Calibration of confidence vs hit rate | Constant confidence | **Live** |
| **FinBERT sentiment** | `news_sentiment` | Tone of a headline | 1–5 trading days | Next-day signed and absolute return after the headline; plus a hand-labelled sample | Accuracy / macro-F1 on labels; event-study mean return | Neutral; lexicon sentiment | **Live** |
| Headline entities and events | `headline_entities`, `headline_events` | "This headline concerns X / is event type Y" (a classification) | n/a | Hand-labelled sample | Precision / recall | Keyword-only | **Live** (deterministic, ADR 0010) |
| Daily change alerts, Grafana alerts | `telegram-alerts`, Grafana rules | A threshold or change occurred (not a forecast) | n/a | Whether the condition held on re-check | Precision of alerts (false-alarm rate) | n/a | **Live** |
| EV signals (12-1 momentum, 52-week-high, profitability, …) | EV roadmap | Signal ranks future returns | 1 month | Next-month excess return | Monthly IC, t-stat, quintile spread after costs | Existing composite | **Planned** |
| **Composite v2** | EV-17 | Beats composite v1 | 1–12 months | as composite | as composite | Composite v1 | **Planned** |
| Anomaly flags | ML-1 | "This bar / feed / node is abnormal" | next run | Confirmed bad data / confirmed outage | Precision, recall on injected + known events | Fixed thresholds | **Planned** |
| Volatility forecasts | ML-3 | Next-period volatility | 1 / 5 / 21 days | Realised volatility | QLIKE, MAE | EWMA, 30-day realised | **Planned** |
| Regime label | ML-4 | Current regime (descriptive) | n/a | Does conditional factor return differ out of sample? | Stability and conditional IC difference | No regime | **Planned** |
| Peer clusters | ML-5 | These names are similar | n/a | Co-movement out of sample vs official sector | Explained correlation | Official sector | **Planned** |
| GBM return ranker | ML-6 | Ranks future returns | 1 month | Next-month excess return | Incremental IC over composite v2 | Composite v2 | **Planned (gated)** |
| LLM narratives | ML-7 | Statements about today's data | n/a | Facts JSON | % claims verified; human spot-check | Template text | **Planned** |

Technical indicators (RSI, MACD, bands) are descriptive and are **not** logged as predictions; if one is ever used as a signal it
enters through the EV roadmap like any other.

## 3. Lifecycle

```
 daily job ── emits ──► predictions (write-once ledger) ──► [horizon passes] ──► outcome-resolver
      │                         │                                                     │
      │                         └─ features_hash / data_as_of (what was known then)   ▼
      │                                                                     prediction_outcomes
      ▼                                                                              │
 model_pointer ◄── promote ◄── gate ◄── shadow period ◄── candidate ◄── training ◄──┤
  (champion version)                      (logged, not shown)     (offline, §8)       ▼
                                                                     eval_metrics + drift_metrics ──► Grafana "Model health" + Telegram alert
```

## 4. Schema (additive; natural keys + unique indexes; `migration` skill, backup first)

| Table | Key | Content |
|---|---|---|
| `predictions` | unique (`kind`, `subject`, `as_of`, `horizon_days`, `model_version`) | `id`, `kind` (e.g. `composite`, `factor:momentum`, `sentiment`), `subject` (symbol / index / feed), `universe`, `as_of`, `horizon_days`, `value`, `rank`, `confidence`, `model_version`, `features_hash`, `source_ref` (e.g. the `stock_scores` row), `meta` JSON, `created_at`. **Write-once: `INSERT … ON CONFLICT DO NOTHING`; no UPDATE/DELETE grants for the writer role.** |
| `prediction_outcomes` | unique (`prediction_id`) | `resolved_at`, `realized`, `benchmark`, `excess`, `status` (`resolved` / `censored` / `error`), `note` (delisting, corporate action, missing price) |
| `eval_metrics` | unique (`kind`, `model_version`, `universe`, `window`, `metric`, `as_of`) | metric value, `n`, confidence band |
| `drift_metrics` | unique (`kind`, `feature`, `as_of`) | PSI / KS vs the training window; prediction-distribution shift |
| `model_registry` | unique (`model_name`, `version`) | artifact hash + location, trained-through date, params, data hash, git SHA, metrics, `status` (`candidate` / `shadow` / `champion` / `retired`), `notes` |
| `model_pointer` | `model_name` | the current champion version (rollback = move the pointer) |

Volume is small: about 1,000 composite rows a day and ~6 outcome rows each — a few million rows a year, trivial next to
`stock_prices`. Hypertables on `as_of`.

## 5. Daily jobs (CronJobs, house pattern: `job_runs`, heartbeat, AR-1 safeguards, canary-validated)

| Job | When | What | Where |
|---|---|---|---|
| **prediction-logger** (a shared `log_predictions()` call inside each producing job, not a separate pass) | right after the producer | Writes the immutable row(s) with `features_hash` and `as_of` | the producing Pi job |
| **outcome-resolver** | after prices land (≈ 23:45 node-local) | Finds matured predictions; computes excess return on **adjusted total-return** prices; marks `censored` on delisting or missing data; idempotent (unique on `prediction_id`) | Pi, ~256 Mi |
| **eval-metrics** | after the resolver | Per kind/model/universe/window metrics (§6) | Pi |
| **drift-monitor** | daily | PSI/KS of features and predictions vs the training window | Pi |
| **model-health alert** | after metrics | Telegram only when a pre-registered threshold is crossed; weekly digest line otherwise | Pi |

A Grafana **"Model health"** dashboard shows rolling IC with bands, hit rates, calibration, drift and the champion/challenger
comparison.

## 6. Validation rules per kind (pre-registered thresholds are fixed in the ADR, §10)

| Kind | Resolve | Metrics | Minimum sample before any conclusion | Review trigger |
|---|---|---|---|---|
| Ranking (composite, factors, EV signals, ML-6) | Excess return at 1/3/6/12 months; **overlapping horizons** make consecutive observations dependent, so use overlap-robust standard errors (Newey–West ⚠️ standard practice, not re-verified here) | Rank IC mean and t-stat, quintile spread, top-25 hit rate, turnover, cost-adjusted spread, split by universe and by half-sample | ≥ 12 monthly cross-sections per horizon; do not read results earlier | Rolling 12-month IC below zero, or below the EV-0 backtest band |
| Confidence / probability outputs | Binary: did the name beat the median? | Calibration curve, Brier score ⚠️ | ≥ 200 resolved predictions per bin group | Systematic over-confidence |
| Sentiment | Next-day return after the headline; labelled sample | Accuracy, macro-F1; event-study mean and t-stat | 200 labelled headlines; ≥ 1,000 headlines for the event study | Accuracy below the lexicon baseline |
| Classifications / alerts / anomalies | Confirmed vs not | Precision, recall, false alarms per week | 50 reviewed cases | > 1 false alert per week, or recall below target |
| Volatility forecasts | Realised volatility over the horizon | QLIKE, MAE vs EWMA and 30-day realised | 250 trading days | Loses to the baseline over 6 months |
| Regime / clusters | Out-of-sample conditional comparison | Stability; conditional IC difference | ≥ 5 years of history | No out-of-sample difference |
| LLM narratives | Facts JSON | % claims verified (target 100 %), human spot-check of 10 a week | 30 consecutive outputs | Any unverified number reaching users |

**Cautions (apply everywhere):** a short live record cannot prove skill; the **number of variants tried is tracked** (ML-0) and results
are deflated for it; a benchmark must match the universe (index-relative and equal-weight-relative are both reported); costs are
deducted in every spread; censored (delisted) names are reported, not dropped; the ledger measures *this system's* live behaviour, so
it does not remove survivorship bias from *backtests* — EV-1 does that.

## 7. What is and is not trained

| Component | Trained or refit? | Cadence | Notes |
|---|---|---|---|
| FinBERT (sentiment) | **No** — pretrained, used as is | never | Evaluate against labelled sample; fine-tuning only if it loses to the baseline (not planned) |
| Sentence embeddings (ML-2) | **No** — pretrained | never | — |
| Volatility models (ML-3) | **Refit** parameters | weekly | EWMA/GARCH/GJR-GARCH; refit is cheap, runs on a Pi or the workstation |
| Regime model (ML-4) | **Refit** | monthly | Only if it passes the gate |
| Peer clusters (ML-5) | **Re-cluster** | quarterly | — |
| Anomaly thresholds (ML-1) | **Recalibrate** | quarterly | Against the labelled/known events |
| Composite weights (EV-17) | **Not trained** — equal weights | only on EV-0 evidence | Changing weights is an experiment, gated like a model |
| GBM ranker (ML-6) | **Train** | quarterly, or on a decay trigger | Hard-gated (EV §5) |

## 8. Training pipeline (offline, workstation; applies to every learned component)

1. **Dataset build:** point-in-time features as of each date (fundamentals by `filing_published_at`; adjusted prices — G-1) exported to
   Parquet with a **data hash**; the same code builds features for scoring, so train and serve cannot diverge.
2. **Split:** walk-forward on an expanding window, purge/embargo around the label horizon, **final two years held out untouched** until the
   end of the study; never shuffle.
3. **Train a candidate within a fixed search budget** (ML-0 trial counter; stop at the budget derived from the data length).
4. **Evaluate** against the current champion and the baselines on walk-forward out-of-sample data, after costs, split by universe.
5. **Gate** — the EV roadmap §5 acceptance for ranking models; pre-registered thresholds for others. Failing a gate ends the run; the
   result is recorded in `ml_runs` so it is not retried.
6. **Register** the artifact (`model_registry`: hash, trained-through, params, data hash, git SHA, library lock) with status `candidate`.
7. **Shadow:** the candidate scores every day, logged to `predictions` under its own `model_version` but **not shown** to users, for at
   least one full horizon cycle (≥ 3 months for monthly-horizon models).
8. **Promote** only if shadow results hold; a human approves; move `model_pointer`. The first promotion of each model gets an ADR.
9. **Rollback:** move the pointer back; the previous champion's artifact is retained.
10. **Retire** models that decay below the review trigger; keep their predictions for audit.

**Compute placement:** train on the workstation (CPU is enough for tabular models; the RTX 3090 is not needed); artifacts (a few MB) live
outside git on a backup-grade disk with the hash in the registry; scoring CronJobs load the **champion** via the pointer. Images follow the
AR-5 profiles (an `ml-light` profile without torch).

## 9. Phasing and priority (scored with the matrix in the screener plan §16)

| Pri | ID | Item | Value | Effort | Depends on |
|---|---|---|---|---|---|
| **P0** | **PV-0** (generalises G-3) | `predictions` + `prediction_outcomes`, the shared `log_predictions()` call, and the outcome-resolver for the **composite and factor scores** first | High | M | **G-1** (adjusted prices); `migration` skill; AR-2 shared module helpful |
| **P1** | **PV-1** | `eval_metrics` + Grafana "Model health" + threshold alerts (composite first) | Medium | S | PV-0 |
| **P2** | **PV-2** | Extend the ledger to sentiment (event study + labelled sample), alert precision, headline classification precision, report confidence | Medium | M | PV-0; a hand-labelled sample |
| **P2** | **PV-3** | Drift monitor (PSI/KS) for features and predictions | Medium | M | PV-0, ML-0 |
| **P2** | **TP-0** | Training infrastructure: dataset snapshotter, splitter (shared with ML-0), `model_registry`/`model_pointer`, shadow mode, promotion/rollback procedure | Medium | M | ML-0; first learned model |
| **P2** | **TP-1** | Scheduled refits for ML-3 (weekly), ML-4 (monthly), thresholds (quarterly), as those packages land | Medium | S each | ML-3/4/1, TP-0 |
| **P3** | **TP-2** | GBM quarterly retrain with gate + shadow | Medium | L | ML-6, EV-0, EV-1, composite v2 |

**Order of work:** G-1 → PV-0 (start logging composite predictions *immediately after G-1*, because history cannot be recovered) → PV-1 →
the P2 items as the models they serve appear.

## 10. Decisions needed / ADR candidates

1. **Write-once ledger as an explicit exception to ADR 0005** (DELETE+INSERT): why, and the `ON CONFLICT DO NOTHING` + no-UPDATE-grant rule.
2. **Pre-registered thresholds** per kind (§6) — fixed before results exist.
3. **Promotion policy:** shadow length, who approves, rollback, retention of retired models.
4. **Benchmarks:** which index per universe, plus the equal-weight baseline.
5. **Labelled samples:** who labels (you), how many, where stored, refresh cadence.

## 11. Acceptance for the pipeline itself

- **Resolver tests with fixtures:** a split, a dividend, a spin-off, a delisting, a missing price and a holiday gap each resolve to the
  expected number or `censored`. (Needs G-1.)
- **Immutability test:** re-running a producer and the logger does not change existing rows.
- **Idempotency:** re-running the resolver and metrics changes nothing.
- **Golden metric tests:** IC, quintile spread and Brier on a small synthetic set with known answers.
- **Shadow isolation:** a shadow model's predictions never appear in digests, reports, the bot or the composite.
- **Replay:** the ledger/resolver run over a historical window reproduces the EV-0 harness numbers within tolerance.

## 12. Risks and caveats

| Risk | Mitigation |
|---|---|
| Wrong outcomes from unadjusted prices | G-1 first; fixtures for corporate actions |
| Reading noise as skill (or decay) from a short record | Minimum samples (§6), overlap-robust errors, deflation by trial count, no action on short windows |
| Overfitting the thresholds after seeing results | Pre-register (ADR); keep the held-out window untouched |
| Ledger growth or load on a fragile cluster | Small volume; Pi CronJobs ~256 Mi; AR-1 deadlines; off-peak |
| Scope creep into "ML for everything" | Only components that actually learn enter §8; the rest are validated, not trained |
| False sense of validation | The ledger monitors; acceptance of new signals stays with EV-0/§5 |

Not investment advice. Effort labels are estimates; thresholds and sample sizes above are starting points to be fixed in the ADR.

---

## 13. As-Built: Continuous Classification & Factor Validation Loop (`ops-tools/classifier_loop.py`, ADR 0033 — 2026-11-08)

While `predictions.py` (Schema 18, ADR 0028) records daily `screener_rank` and `valuation_upside` snapshots and resolves 30/91/182-day adjusted excess returns into `prediction_outcomes`, `ops-tools/classifier_loop.py` closes the feedback loop across all four classification layers:

1. **Factor & Sector Rank IC Audit (`evaluate_factors`):**
   - Joins resolved `prediction_outcomes` (`kind = 'screener_rank'`) with point-in-time `stock_scores` (`value_score`, `quality_score`, `growth_score`, `momentum_score`, `risk_score`, `composite`) and `universe_constituents.sector`.
   - Computes rolling cross-sectional Spearman Rank IC, IC-IR (`mean(IC)/std(IC)`), hit rate, and top-minus-bottom quintile excess return spread (`Q5 - Q1`) globally and within each `(universe, sector)`.
   - Emits automated warnings when any factor decays (`mean_ic < -0.02` over $\ge 3$ cross-sections) or exhibits sector-specific inversion (`ic < -0.10`, e.g., when un-neutralized leverage/multiples distort Financials).
2. **Stock Archetype Cohort Validation (`evaluate_archetypes`):**
   - Classifies every historical `(symbol, ts)` factor vector using the unified 11-archetype taxonomy (`Quality Compounder`, `GARP`, `Deep Value`, `Momentum Leader`, `High-Growth Speculative`, `Defensive Quality`, `Turnaround / Cyclical`, `Value Trap`, `High-Risk / Stressed`, `Lagging / Weak`, `Balanced`).
   - Aggregates realized excess return, median excess return, hit rate, and 10th-percentile tail drawdown (`tail_p10_ret`) per cohort, and checks three pre-registered structural hypotheses:
     - `Quality Compounder` mean excess return $>$ `Lagging / Weak`
     - `Deep Value` mean excess return $>$ `Value Trap`
     - `Defensive Quality` 10th-percentile tail return $>$ `High-Risk / Stressed`
3. **Multibagger Gate Marginal Value Attribution (`evaluate_multibagger_gates`):**
   - Evaluates all 6 multibagger gates (`growth`, `return on equity`, `debt`, `size`, `valuation`, `trend`) by comparing the realized excess return of candidates passing all gates against **single-gate near-misses** (stocks passing 5 of 6 gates and failing only gate $k$).
   - Computes $\text{GMV}_k = \overline{R}_{\text{passed}} - \overline{R}_{\text{near-miss}(k)}$ and flags any gate with $\text{GMV}_k < -2\%$ as over-restrictive.
4. **Bayesian Shrinkage Adaptive Factor Calibration (`propose_factor_weights`):**
   - Blends baseline prior weights $W_0 = (0.25, 0.25, 0.20, 0.20, 0.10)$ with empirical positive-IC-IR weights via sample-size shrinkage $\lambda = \min(0.50, N / (N + 12))$.
   - Enforces ADR 0018 safety bounds: minimum sample gate (`MIN_CROSS_SECTIONS = 6`), per-factor tilt cap (`MAX_TILT = 0.05`), factor floor (`MIN_WEIGHT = 0.05`), and exact normalization to `1.0000`.
   - Persists the deterministic Markdown report to `stock_reports` (`symbol = '__calibration__'`, viewable in MarketView at `/reports/__calibration__`) and `out/classifier_loop/`.
