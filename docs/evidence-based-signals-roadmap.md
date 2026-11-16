# Evidence-Based Signals Roadmap

> **Status:** plan, written 2026-10-10. Nothing here is built. Items are tracked in
> `pending-tasks.md` as EV-0…EV-18; accepted ones follow the usual build cycle
> (`pipeline-dev` → `canary-validate` → docs).
> **Purpose:** (1) which additional signals with strong research backing this pipeline
> can add, in build order; (2) for every piece graded weak or none today, the
> best-supported replacement.
> **What "backing" means here:** published in a peer-reviewed finance or accounting
> journal, replicated by independent authors, and observed outside the original
> sample (other countries or later years). A book, a broker note or a rule of thumb
> is "practitioner", not backing.
> Companion docs: `stock-screener-implementation-plan.md` (the v1 formulas this
> revises), `market-data-telegram-bot.md` §1a (where the outputs are shown).

## 1. The one rule: measure before adding

Published evidence says a signal worked on someone else's data. It does not say it
works on this universe, at this rebalance frequency, after costs. Most published
anomalies fail replication once tiny illiquid stocks are down-weighted (Hou, Xue &
Zhang 2020), and on average a published signal loses roughly a quarter of its return
outside the original sample and more than half after publication (McLean & Pontiff
2016).

So every item below enters the composite only after it passes EV-0's test **on this
database**. The data to do that already exists, which was not obvious before:

| Data on hand (measured 2026-10-10) | What it allows |
|---|---|
| Daily prices: S&P 500 — 465 of 503 symbols with 10+ years (from 1990); Nifty 500 — 321 of 494 with 10+ years (median start 2007) | Every price-only signal can be backtested over decades, today |
| US fundamentals from EDGAR: quarterly + annual since 2007, ~60 periods per symbol, **with filing dates** (`filing_published_at`) | Point-in-time fundamental signals can be backtested for the S&P 500 |
| India fundamentals from Yahoo: 4 annual points per symbol, no filing dates | Fundamental signals for the Nifty 500 **cannot** be backtested yet (EV-9) |
| Valuation ratios (`pe`, `pb`, `ev_ebitda`, `market_cap`): latest weekly snapshot only | Historical valuation must be rebuilt from price × EDGAR per-share data (US) |

**Correction to earlier notes:** the Telegram doc and tracker said the multibagger
screen "cannot be backtested — no point-in-time history". That is true for the
Nifty 500 and false for the S&P 500, where EDGAR history with filing dates exists.

**Standing limitation — survivorship.** Both universes are *today's* constituents.
A backtest over them only contains companies that survived and grew enough to be in
the index now, which inflates every result. Until historical constituent lists are
added (EV-1), treat backtests as useful for *ranking* signals against each other,
not as estimates of achievable return.

## 2. Build order

Effort: S = under a day, M = a few days, L = a week or more. "Have" = data already
in the database.

### Phase 0 — measurement harness (prerequisite for everything)

| ID | Item | Why it has backing | Data | Effort |
|---|---|---|---|---|
| EV-0 | **Signal test harness**: monthly cross-sectional rank correlation between a signal and next-month return (the information coefficient), its t-statistic, quintile spread, turnover, and results split by decade and by universe | Standard methodology since Fama & MacBeth (1973). Acceptance bar follows Harvey, Liu & Zhu (2016): because hundreds of signals have been tried, require **t > 3**, not 2 | Have (prices; EDGAR for US fundamentals) | M |
| EV-1 | ✅ **US DONE 2026-10-26** (ADR 0025, `membership-history` CronJob, `screener:v18`) — point-in-time S&P 500 intervals in `index_membership` from Wikipedia's constituents + changes tables; canary: 891 intervals, 503 open, drift 0 vs universe_constituents, spot-checked against the 2026-10-06 changes. Comprehensive ~2012→, selected changes to 1976; 6 ticker-rename danglers WARNed and dropped. **India deferred** (NSE circulars are PDFs — own session; forward history accrues via universe_membership_snapshots). US EV-0 re-run now unblocked | Removes survivorship bias from EV-0 | New feed | M |
| EV-2 | **Keep every weekly fundamentals snapshot** instead of overwriting, starting now | Builds the point-in-time history India lacks; costs nothing today, unrecoverable later | Schema: key snapshots by fetch date | S |

### Phase 1 — price-only signals (data on hand, testable immediately)

| ID | Signal | Evidence | Change needed | Effort |
|---|---|---|---|---|
| EV-3 | ✅ **DONE 2026-11-08** (`stock_metrics.py`, ADR 0033) — **12-1 momentum**: return from 12 months ago to 1 month ago | Jegadeesh & Titman (1993); found in 20 emerging markets including India (Rouwenhorst 1999) and across asset classes (Asness, Moskowitz & Pedersen 2013). The most recent month is skipped because it *reverses* (Jegadeesh 1990) | Computed daily as `ret_12_1` in `stock_metrics.py` and surfaced in `/stock`, `stock_report.py`, MarketView dossiers, and the Academic SQM `/multibagger` screen | S |
| EV-4 | ✅ **DONE 2026-11-08** (`stock_metrics.py`, ADR 0033) — **Nearness to the 52-week high**: close ÷ 52-week high | George & Hwang (2004) — explains much of momentum and does not reverse later | Computed daily as `near_high` in `stock_metrics.py` and surfaced in `stock_report.py` | S |
| EV-5 | **Low risk**: 1-year volatility and market beta | Ang, Hodrick, Xing & Zhang (2006) for volatility; Frazzini & Pedersen (2014) for beta | Volatility exists (`vol_180d`); add beta against the universe's index | S |
| EV-6 | **Risk-scaled momentum**: scale momentum exposure down when its recent volatility is high | Barroso & Santa-Clara (2015); Daniel & Moskowitz (2016) on momentum crashes | Applies at portfolio level (EV-16) | M |
| EV-7 | **Index trend filter** as a regime flag: index above its 10-month average | Moskowitz, Ooi & Pedersen (2012) time-series momentum, across 58 futures markets | `index_prices` has it; show in digest and `/index` | S |
| EV-8 | **Residual momentum**: 12-1 momentum of the part of return not explained by the market | Blitz, Huij & Martens (2011) — about half the volatility of plain momentum | Needs EV-5's beta | M |

### Phase 2 — fundamental signals

| ID | Signal | Evidence | Data | Effort |
|---|---|---|---|---|
| EV-9 | ✅ **DONE 2026-10-26 / 2026-11-08** (`screener:v17`, DG-11 COGS fallback) — **more statement lines** in `stock_fundamentals.py`: total assets, gross profit (direct XBRL tag + `total_revenue - COGS` fallback), current assets and liabilities, shares outstanding, dividends paid (positive-outflow convention, `_norm`); OCF added to the Yahoo leg for `financial_facts` (accruals input) | Prerequisite for EV-10…EV-14 | Yahoo statements carry all of them; EDGAR for the US back to 2007 | M |
| EV-10 | ✅ **DONE 2026-11-08** (`stocks.py`, `stock_report.py`, `marketview`, ADR 0033) — **Gross profitability**: gross profit ÷ total assets (`GP/A`) | Novy-Marx (2013). As strong as value, and negatively correlated with it, so the two combine well | Surfaced in `/stock` Scientific Diagnostics, shortlist reports, and MarketView dossiers (descriptive until EV-20 panel test) | S |
| EV-11 | ✅ **DONE 2026-11-08** (`stocks.py`, `marketview`, ADR 0033) — **Investment (asset growth, CMA)**: year-on-year growth in total assets, *lower is better* | Cooper, Gulen & Schill (2008); the investment factor in Fama & French (2015) | Surfaced in `/stock` Scientific Diagnostics & Strategy Lab | S |
| EV-12 | ✅ **DONE 2026-11-08** (`stocks.py`, `stock_report.py`, `marketview`, ADR 0033) — **Accruals**: (net income − operating cash flow) ÷ total assets, lower is better | Sloan (1996) | Surfaced in `/stock` Scientific Diagnostics, shortlist reports, and MarketView dossiers | S |
| EV-13 | ✅ **DONE 2026-11-08** (`stocks.py`, `marketview`, ADR 0033) — **Net share issuance**: change in shares outstanding, lower (buybacks) is better | Pontiff & Woodgate (2008); Daniel & Titman (2006) | Surfaced in `/stock` Scientific Diagnostics & Strategy Lab | S |
| EV-14 | ✅ **DONE 2026-11-08** (`stocks.py`, `marketview`, ADR 0033) — **Piotroski F-score** (9 yes/no tests of improving profitability, leverage and efficiency) + **Altman Z''-score** + **Academic SQM Multibagger screen** | Piotroski (2000); Mohanram (2005); Altman (1968, 2000); Asness et al. (2018) | Surfaced in `/stock`, `/multibagger` (side-by-side with Practitioner Lynch screen), and MarketView | M |

### Phase 3 — earnings momentum

| ID | Signal | Evidence | Data | Effort |
|---|---|---|---|---|
| EV-15 | **Post-earnings drift**: standardised surprise = (this quarter's EPS − same quarter last year) ÷ its historical volatility; returns keep drifting in the surprise's direction for about 60 days | Ball & Brown (1968); Bernard & Thomas (1989). One of the oldest and most replicated anomalies | US: quarterly EPS + filing dates in EDGAR today. India: needs quarterly results feed | M (US) / L (India) |

### Phase 4 — combining and sizing

| ID | Item | Evidence | Effort |
|---|---|---|---|
| EV-16 | **Portfolio rules**: hold 30–50 names, cap any sector, rebalance monthly, deduct trading costs in every test | DeMiguel, Garlappi & Uppal (2009) — equal weighting beats optimised weights out of sample; Novy-Marx & Velikov (2016) — costs erase high-turnover signals | M |
| EV-17 | **Composite v2** (see §3) | Fama & French (2015) five factors; Asness, Frazzini & Pedersen (2019) quality | M |
| EV-18 | **Measured confidence** (see §3) | Grinold & Kahn (1999) information coefficient; calibration is standard forecast verification | M |

## 3. Replacements for the weak and unbacked pieces

| Today | Grade | Problem | Replace with | Backing of the replacement |
|---|---|---|---|---|
| **Growth factor** — 3-year revenue growth + 1-year EPS growth, 20% of the composite | Weak | Past growth does not predict returns, and fast balance-sheet growth predicts *lower* returns | Split in two: **profitability** (EV-10 gross profitability, plus ROE) and **investment** (EV-11, lower is better) | Strong — these are the two factors Fama & French added in 2015 because they subsume growth-style effects |
| **Composite weights** 25/25/20/20/10 | None | Chosen by hand, never tested | **Equal weight** across value, momentum, profitability, investment and low risk, each a z-score. Change weights only if EV-0 shows a factor fails on this data | Strong for the default: equal weighting is hard to beat out of sample (DeMiguel et al. 2009). Value and momentum are negatively correlated, so combining them cuts risk (Asness et al. 2013) |
| **Momentum inputs** — 6m, 12m, range position, 200-day ratio, equally averaged | Mixed | 12-month return includes the reversing last month; range position is not the documented signal | EV-3 (12-1) + EV-4 (nearness to high) | Strong |
| **RSI** shown on `/stock` and `/index` | Convention only | No robust evidence it predicts | Keep as a description, never as a score input. If a short-horizon signal is wanted: **1-month reversal** (Jegadeesh 1990) — but it trades so often that costs usually consume it | Strong statistically, weak after costs. Recommendation: do not add |
| **Multibagger gates** — 15% growth, ROE 15%, PEG < 2, largest 30% excluded | Practitioner | Thresholds are judgement; PEG has no academic support; source books study survivors | **Small × quality × momentum**: bottom half by size, top third on profitability and low investment (EV-10, EV-11), positive 12-1 momentum (EV-3), then rank by **G-score** (EV-14) | Strong per ingredient. Size only pays among quality companies (Asness, Frazzini, Israel, Moskowitz & Pedersen 2018); G-score is the published tool for picking winners among growth stocks (Mohanram 2005) |
| **PEG ratio** | None | — | **Earnings yield** (EBIT ÷ enterprise value) | Strong as a value measure; enterprise multiples outperform P/E (Loughran & Wellman 2011) |
| **Confidence score** — coverage 40, factor agreement 30, rank stability 30 | None | Weights invented; it is not a probability and says so | Two measured numbers per list: the signal's **information coefficient with its t-statistic** from EV-0, and the **realised hit rate** of past picks at this score level (from `/track`), with a confidence interval. Keep data coverage as a separate "data completeness" figure | This is measurement, not a model — it is what the track record supports |
| **FII flow vs Nifty correlation** | None at 7 sessions | Sample far too small | Backfill FII/DII history (NSE publishes years of it), then report the 60-session correlation only when it is statistically distinguishable from zero | Moderate: foreign flows are persistent and move *with* returns, but predict them weakly (Froot, O'Connell & Seasholes 2001). Present as context, not a signal |
| **Headline sentiment** (FinBERT) as a gauge | Moderate, short-lived | Media tone predicts returns for days, then reverses (Tetlock 2007) | Keep for alerts and context. Do not add to the composite | — |
| **Valuation from weekly snapshot** in the screener's value factor | Sound signal, stale input | Up to a week old | Reprice in `stock_screener.py` as `/stock` already does | Already tracked |

## 4. What the composite looks like after Phases 1–2

| Factor | Inputs | Direction |
|---|---|---|
| Value | Earnings yield (EBIT/EV), book-to-price, free-cash-flow yield | Higher is better |
| Momentum | 12-1 return, nearness to 52-week high | Higher is better |
| Profitability | Gross profit/assets, ROE | Higher is better |
| Investment | Asset growth, net share issuance, accruals | **Lower** is better |
| Low risk | 1-year volatility, beta | Lower is better |

Equal weights. Sector-relative ranking (already planned as v2 scoring) applies on top.

## 5. Acceptance test for any signal (EV-0, written before testing)

> **Implemented 2026-10-18** as `ops-tools/ev0.py` (tests: `tests/test_ev0.py`). Criterion 4 uses price controls
> only until point-in-time value/quality history exists for both universes. Results: §5a.

A signal joins the composite only if, on this database:

1. Its monthly information coefficient has **t > 3** over the full history;
2. the sign is the same in each half of the sample and in both universes (or the
   exception is explained);
3. the top-minus-bottom quintile return stays positive after a cost of 0.2% per
   trade (US) and 0.4% (India);
4. it adds information beyond the factors already in — its coefficient stays
   significant when the existing five are controlled for.

Signals that fail stay out, and the result is recorded here so they are not retried.

## 5a. EV-0 results — first run 2026-10-18 (`out/ev0/report-2026-10-18.md`)

Monthly, 2001-01 → 2026-09 (290 NIFTY / 309 S&P months), next-month total return, liquidity floor = month's 20th
percentile of 63-day median dollar volume, costs 0.4 % / 0.2 % per trade. Universe = today's constituents.

| signal | NIFTY 500 IC (NW t) · net/yr | S&P 500 IC (NW t) · net/yr | §5 verdict |
|---|---|---|---|
| **ret_12_1 (EV-3)** | **0.045 (4.2) · +13.5 %** | 0.006 (0.6) · −1.0 % | FAIL — passes on NIFTY alone (t>3, both halves, net>0, FM t 4.8) |
| ret_12m (in composite) | 0.040 (3.5) · +11.6 % | 0.004 (0.4) · −1.2 % | FAIL (S&P) |
| ret_6m (in composite) | 0.028 (2.9) · +6.6 % | −0.006 (−0.7) | FAIL |
| pos_52w (in composite) | 0.026 (2.5) · −3.0 % | −0.013 (−1.3) | FAIL — FM t negative in both after controls |
| sma200_ratio (in composite) | 0.025 (2.4) · +5.9 % | −0.009 (−0.9) | FAIL |
| low_vol (in composite) | 0.015 (1.3) | −0.025 (−1.9) | FAIL |
| near_high (EV-4) | 0.027 (2.4) | −0.020 (−1.8) | FAIL |
| low_beta (EV-5) | 0.026 (1.9) | −0.019 (−1.3) | FAIL |
| rev_1m (reversal) | 0.009 (1.2) · −16.9 % | 0.015 (1.7) · −5.7 % | FAIL — 78 % turnover, costs consume it (as §6 predicted) |

**Reading, not decisions:**
1. **India: 12-1 momentum is real on this data** and beats the plain 12-month return the composite uses (skipping the last
   month helps, as Jegadeesh 1990 predicts). It is the strongest candidate for composite v2 (EV-17) — for NIFTY 500.
2. **S&P 500 results are not interpretable yet.** Low-vol and low-beta come out *negative*, the opposite of the literature:
   today's constituents include firms that were small, volatile winners years before they joined the index, so "volatile
   then, high return later" is built into the sample. **EV-1 (historical membership) is now the blocking item** for any US
   price signal. `universe_membership_snapshots` only starts accruing from 2026-10-18.
3. `pos_52w` adds nothing once the other inputs are controlled (FM t −2.5 / −2.7): first candidate to drop in v2.
4. Total return (`adj_close`) vs price-only `close`: differences are small for price signals (e.g. NIFTY 12-1 IC 0.045 vs
   0.043) — DQ-1 matters more for return/volatility *levels* than for these ranks.
5. Per §5 nothing enters the composite from this run; the composite is unchanged (ADR 0018). Re-run after EV-1, and per-universe
   rules (criterion 2 "or the exception is explained") are an owner decision for composite v2.

## 5b. EV-0 re-run 2026-10-27 — SP500 point-in-time (`out/ev0/report-2026-10-27.md`)

Same harness; **SP500 now uses EV-1 point-in-time membership** (ADR 0025, 891 intervals,
101,428 symbol-months) — the 2026-10-18 run's blocking item, resolved. SP400/SP600 appear
for the first time but are **doubly unreliable** (today's constituents AND the ADR 0029
price backfill was mid-run): ignore them until EV-1 covers them and the backfill settles.
NIFTY500 is unchanged (still today's constituents).

| signal | NIFTY 500 (biased) IC (NW t) · net/yr | S&P 500 (point-in-time) IC (NW t) · net/yr | §5 verdict |
|---|---|---|---|
| **ret_12_1 (EV-3)** | **0.045 (4.2) · +13.6 %** | 0.001 (0.1) · −3.5 % | FAIL — India-only, exactly as in 5a |
| ret_12m (in composite) | 0.040 (3.6) · +11.7 % | −0.002 (−0.2) · −4.6 % | FAIL |
| ret_6m (in composite) | 0.028 (2.9) · +6.7 % | −0.011 (−1.3) · −6.2 % | FAIL |
| pos_52w (in composite) | 0.027 (2.5) · −2.9 % | −0.015 (−1.4) · −9.6 % | FAIL |
| sma200_ratio (in composite) | 0.026 (2.4) · +6.0 % | −0.014 (−1.5) · −6.7 % | FAIL |
| low_vol (in composite) | 0.016 (1.4) | −0.017 (−1.3) | FAIL |
| near_high (EV-4) | 0.028 (2.4) | −0.020 (−1.7) | FAIL |
| low_beta (EV-5) | 0.027 (2.0) | −0.014 (−0.9) | FAIL |
| rev_1m (reversal) | 0.008 (1.1) | 0.017 (1.9) · −5.6 % | FAIL — costs still consume it |

**Reading, not decisions (pair review before anything changes):**
1. **The survivorship fix did NOT rescue US price momentum.** With point-in-time membership
   the SP500 ICs stay ~zero-to-negative (ret_12m −0.002 vs 0.004 biased; ret_6m −0.011 vs
   −0.006). 5a's hope that EV-1 would make the US leg interpretable is answered: it is now
   interpretable, and the answer is that **large-cap US price momentum has no edge on this
   data since 2001** — consistent with the post-publication momentum decay literature, not
   a data artifact.
2. **India's 12-1 momentum result is unchanged and strong** (t 4.2, net +13.6 %, FM t 4.8),
   but its universe is still survivorship-biased — EV-1 India is now the decisive item for
   the composite-v2 question, since the one remaining live candidate rests on biased data.
3. Low-vol/low-beta flipped from "opposite of the literature" (5a) to merely ~0 on the
   honest SP500 sample — the anomaly in 5a was indeed the survivorship artifact 5a §2
   predicted. They still fail §5.
4. Implication to weigh at composite v2 (owner decision, EV-17): the composite's 20 %
   momentum weight has supportive evidence only for India; for the US it is noise on this
   harness. No change made — per §5 and ADR 0018 nothing enters or leaves the composite
   from a report alone.

## 5c. EV-19 / EV-20 — point-in-time fundamentals, first run 2026-11-12 (`out/ev0/report-ev19-20-2026-11-12.md`)

`ops-tools/ev0.py export-pit` + `run-pit` (tests: `tests/test_ev0.py` `PointInTimeFacts`). Same
`evaluate()`/`verdict()` as the price signals. Facts are used only from the day after their
**first** filing (restatements dropped); EPS and share counts are split-adjusted to today's basis
via `corporate_actions`; Q4 and YTD-only cash-flow quarters are derived as YTD − prior YTD.
EDGAR history starts with XBRL (filed ≥ 2009-04) → ~195 US months; NSE results from 2005.
Event date = `filed` (10-Q/10-K or NSE results date, usually after the press release);
`earnings_calendar` only holds forward dates (from 2026-10-27) so it cannot date past events.

| signal | NIFTY 500 (biased) IC (NW t) · net/yr · FM t | S&P 500 (point-in-time) IC (NW t) · net/yr · FM t | §5 verdict |
|---|---|---|---|
| **SUE (EV-19)**, held 91 days | 0.028 (2.7) · +0.9 % · 1.3 | 0.007 (1.0) · −1.6 % · 2.1 | FAIL — closest is NIFTY (both halves +, net > 0, but t < 3 and FM t 1.3) |
| **Value sleeve (EV-20)** | 0.013 (1.2) · +6.0 % · 2.7 (E/P only) | 0.004 (0.4) · −1.8 % · −0.1 | FAIL |
| **Quality sleeve (EV-20)** | 0.014 (0.6) · −7.1 % (97 months, partial sleeve) | 0.006 (0.7) · −0.9 % · 0.6 | FAIL |
| components (S&P 500) | — | E/P 0.004 (0.5) · B/P −0.011 (−0.9) · EBIT/EV 0.005 (0.6) · **FCF yield 0.017 (1.8) · +2.8 %** · ROE 0.012 (1.2) · op margin 0.003 (0.3) · −D/E −0.003 (−0.3) | all FAIL |

**Reading, not decisions:**
1. **Nothing passes §5**; per §5 and ADR 0018 the composite is unchanged. No component reaches
   |t| > 2 on the honest SP500 sample; the best US component is FCF yield (t 1.8, net +2.8 %/yr),
   which is also the most consistent across SP400/SP600 (t 1.7–2.1).
2. The composite's value and quality sleeves — tested for the first time — carry **no measurable
   edge on the S&P 500 since 2009** at monthly horizon. SP400/SP600 (today's constituents, biased)
   show op margin and quality *negative* (t −2.0…−2.4), the same survivorship pattern 5a saw for
   low-vol.
3. PEAD on the filing date is weak in the US (expected: large caps, post-publication decay, and
   the 10-Q lags the announcement). The NIFTY result (t 2.7) is the only one near the bar and rests
   on a survivorship-biased universe and ~117 names/month — worth a re-run once EV-1 India exists.
4. India sleeves are partial (no balance sheet or share count in the NSE results feed; PBT
   coverage thin before 2019) — the India quality result is not a test of the screener's sleeve.

## 6. Deliberately not adding

- Chart patterns, MACD or Bollinger-band rules as predictors — no robust evidence.
- Analyst price targets — biased upward, poor predictors.
- Any machine-learned return forecast without a multi-year out-of-sample test under
  §5. The data volume here (about 1,000 stocks) is small for that.
- Daily FII flow or headline sentiment as timing signals — see §3.

## 7. Caveats that no amount of evidence removes

- **Evidence is thinner for India.** Most studies are US or developed-market.
  Rouwenhorst (1999) and later emerging-market work find value, momentum and size
  there too, but with fewer years and more noise.
- **These are portfolio effects.** They were measured on hundreds of stocks held for
  decades. A shortlist of twenty will often disagree with the average for years.
- **Costs and taxes** are outside every academic number above.

## 8. References

Ang, Hodrick, Xing & Zhang (2006), *J. Finance* · Asness, Frazzini, Israel, Moskowitz
& Pedersen (2018), *J. Financial Economics* · Asness, Frazzini & Pedersen (2019),
*Review of Accounting Studies* · Asness, Moskowitz & Pedersen (2013), *J. Finance* ·
Ball & Brown (1968), *J. Accounting Research* · Barroso & Santa-Clara (2015),
*J. Financial Economics* · Bernard & Thomas (1989), *J. Accounting Research* · Blitz,
Huij & Martens (2011), *J. Empirical Finance* · Cooper, Gulen & Schill (2008),
*J. Finance* · Daniel & Moskowitz (2016), *J. Financial Economics* · Daniel & Titman
(2006), *J. Finance* · DeMiguel, Garlappi & Uppal (2009), *Review of Financial
Studies* · Fama & French (2015), *J. Financial Economics* · Fama & MacBeth (1973),
*J. Political Economy* · Frazzini & Pedersen (2014), *J. Financial Economics* · Froot,
O'Connell & Seasholes (2001), *J. Financial Economics* · George & Hwang (2004),
*J. Finance* · Grinold & Kahn (1999), *Active Portfolio Management* · Harvey, Liu &
Zhu (2016), *Review of Financial Studies* · Hou, Xue & Zhang (2020), *Review of
Financial Studies* · Jegadeesh (1990), *J. Finance* · Jegadeesh & Titman (1993),
*J. Finance* · Loughran & Wellman (2011), *J. Financial and Quantitative Analysis* ·
McLean & Pontiff (2016), *J. Finance* · Mohanram (2005), *Review of Accounting
Studies* · Moskowitz, Ooi & Pedersen (2012), *J. Financial Economics* · Novy-Marx
(2013), *J. Financial Economics* · Novy-Marx & Velikov (2016), *Review of Financial
Studies* · Piotroski (2000), *J. Accounting Research* · Pontiff & Woodgate (2008),
*J. Finance* · Rouwenhorst (1999), *J. Finance* · Sloan (1996), *The Accounting
Review* · Tetlock (2007), *J. Finance*.

Citations are from memory of well-known papers and were not re-checked against the
journals when this was written; verify a reference before relying on a specific
figure from it.

## 8. Candidates queued by the Strategy Lab (ADR 0031) — acceptance tests written before testing

- **EV-19 — Post-earnings-announcement drift (PEAD).** Build: earnings surprise =
  actual quarterly EPS (financial_facts, first-filed) vs trailing 4-quarter mean;
  signal = standardized surprise on the filing date, held 60 trading days.
  ACCEPTANCE (§5, unchanged): NW IC t > 3 monthly; same sign both halves and both
  flagship universes; net of 0.2%/0.4% costs at the implied ~quarterly turnover;
  FM t > 2 against the existing five. Prerequisites: earnings_calendar (live,
  schema 19) + point-in-time financial_facts (live, DQ-2). Blocked only on build effort.
- **EV-20 — Fundamental quality/value tested as PANELS.** The composite's quality
  and value sleeves have never been through EV-0 (only price signals have). Build:
  reconstruct the sleeve scores at each past month-end from first-filed
  financial_facts (US; India lacks point-in-time statements until EV-1/G-6
  mature). Same §5 acceptance. This is the honest test behind the Strategy Lab's
  Tier-1 claims for quality and value on OUR data.
