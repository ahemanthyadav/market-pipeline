# 0028. Accountability layer: prediction scorecard, earnings calendar, regime display, DQ-5 accuracy job

- Date: 2026-10-27
- Status: accepted (deployed same day: `local/screener:v24`, `local/telegram-digest:v26`, `local/marketview:v4`)

## Context
The pipeline computes claims (screener ranks, fair values) and now alerts on
them (ADR 0026 amendment), but nothing recorded whether the claims come true,
nothing knew when a company reports next, and data accuracy was checked once,
by hand (the 2026-10-26 review that caught F33/F34). The owner asked for the
feature wave 1–8 (watchlist surface, scorecard, exposure, earnings calendar,
backtest view, accuracy job, dossier context, regime light).

## Decision
1. **Prediction scorecard** (schema 18, `predictions.py`, CronJob 02:40):
   daily write-once snapshots of `screener_rank` and `valuation_upside` at
   each source's LATEST run day; outcomes resolved at 30/91/182 calendar days
   against our own adjusted closes, benchmark-relative (^GSPC/^CRSLDX).
   Outcomes are INSERT-only (`ON CONFLICT DO NOTHING`) — history cannot be
   rewritten. Surfaced at marketview `/scorecard` (decile excess-return
   tables; an interim rank-decile check on stored score history until real
   outcomes accrue).
2. **Earnings calendar** (schema 19, `earnings_calendar.py`, weekly Sunday):
   yfinance next-earnings dates for all active constituents. Consumers: the
   digest ("Earnings this week": watchlist + top-25), the dossier badge, and
   a NEW valuation-watch gate — entries reporting within 3 days are
   suppressed (`VAL_EARNINGS_GAP_DAYS`); a surviving setup re-enters later.
3. **Regime traffic light** — three deterministic legs per market: breadth
   (share of universe above its 200d SMA), benchmark vs its own 200-bar mean,
   3-month factor momentum (stale legs drop out, IN MOM lags by design).
   DISPLAY ONLY (digest line, `/regime`, marketview home) — never a score
   input without EV-0, same discipline as ADR 0026.
4. **DQ-5 accuracy job** (`accuracy_check.py`, weekly Sunday): valuation
   medians + upsides recomputed in SQL, sector median P/E recomputed with the
   engine's exact filter, membership-vs-universe drift, freshness, and a
   second-source check of our ^GSPC closes against FRED's official S&P 500
   CSV (key-free). Writes the `__accuracy__` report row. **Stooq was the
   intended per-stock second source but added a JS proof-of-work wall
   (2026-10)**; a per-stock second source now needs a free API key
   (Finnhub/Tiingo) — owner decision, tracked in pending-tasks. We do NOT
   masquerade as a browser to get around bot walls.
5. **Watchlist surface**: the bot's existing chat-private `/watch` data gets
   a read-only marketview page (symbols + aggregate P&L, NO chat ids — the
   ask.py privacy rule extends to the web app).
6. **Exposure** stays an owner-run step: `tailscale serve` on pi1 (both
   marketview and Grafana) — the agent permission system correctly refuses
   to widen network exposure; commands live in pending-tasks §1.

## Consequences
- 32 CronJobs; scorecard outcomes start landing 2026-11-26 (30d), the
  182-day table only in April 2027 — the page says so rather than showing
  thin numbers as truth.
- The valuation watch can now stay silent on a name solely because earnings
  are imminent; that is intended and documented in the bot doc.
- prediction_snapshots grows ~1.4k rows/day (~0.5M/year) — plain table,
  trivial next to stock_prices' 6.3M.
