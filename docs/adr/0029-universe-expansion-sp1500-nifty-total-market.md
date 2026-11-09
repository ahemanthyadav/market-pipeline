# 0029. Universe expansion: S&P 1500 + Nifty Total Market (2,251 symbols)

- Date: 2026-10-27
- Status: accepted (deployed: `screener:v25`, `yfinance-fetcher:v6`, `telegram-digest:v27`, `marketview:v5`)

## Context
The owner asked whether the setup could cover "all stocks listed in India and
US". Assessment: full-market (~8–11k symbols) breaks on free-source rate
limits (the weekly fundamentals window), a multi-day backfill, and small-cap
data quality — not on hardware (DB at 2 GB of 125 GB free). The agreed middle
step: **S&P 1500** (500 + MidCap 400 + SmallCap 600) **+ Nifty Total Market**
(Nifty 500 + Microcap 250) ≈ 2,251 names, ~2.3× current scale.

## Decision
1. **Additive universes, not relabeling**: three new universe values —
   `SP400`, `SP600`, `NIFTYMICROCAP250` — beside the untouched `SP500` /
   `NIFTY500`. Score/valuation history and ranks stay continuous; ranking
   stays within-universe (a #40 small cap is not diluted by mega caps, which
   is the point of the expansion for the multibagger screens).
2. **Sources**: Wikipedia constituents tables for SP400/SP600 (header-driven
   lxml parse — the three pages order columns differently), NSE archives CSV
   for the microcaps (same format as nifty500, carries Industry). Missing
   CIKs filled from the SEC's official `company_tickers.json`, so the EDGAR
   fundamentals leg covers all 1,506 US names. Live parse at decision time:
   503 + 400 + 603 + 496 + 249, 100% US CIK coverage.
3. **Benchmarks** (probed live, all on Yahoo): `^SP400`, `^SP600`,
   `NIFTY_MICROCAP250.NS` added to the yfinance index fetcher; wired into
   predictions.BENCH and marketview BENCHMARK. Discount rates keyed by
   MARKET (US/IN prefix), not universe.
4. **Flagship vs expansion**: alerts (movers), regime legs and digest quote
   tables stay on SP500/NIFTY500. The valuation watch and screener/top/
   multibagger commands cover all five (rank gate is within-universe;
   `/top growth sp600`, `microcap` aliases added). Universe-refresh hard-fails
   only if a FLAGSHIP universe would be empty; expansion universes degrade
   stale-beats-empty.
5. **Capacity changes**: job deadlines raised (prices 3h, fundamentals 9h,
   metrics 40m, valuation 30m, universe-refresh 30m); prices backfill gained
   `ONLY_MISSING=1` (skip symbols with >300 stored bars) so expansion
   backfills never re-pull the existing universe from Yahoo.
6. **Backfill order** (adj_close correctness): corporate-actions backfill for
   the new symbols FIRST (dividend history feeds the adj recompute), then
   `backfill-stock-prices-max` with ONLY_MISSING (~12h, resumable cursor),
   then the Saturday fundamentals run fills statements; metrics → scores →
   valuations → predictions follow on the normal daily chain.

## Consequences
- ~2,251 active names; stock_prices grows to roughly 13–14M rows (~2.3 GB) —
  monitor the daily dump size and pg-backup runtime once, then forget it.
- Small-cap yfinance coverage is thinner: more NULL fundamentals, fewer
  valuation methods per name, more "no earnings date" rows. The screener's
  coverage gate and `methods` count already handle partial data honestly.
- The full-market step beyond this requires a bulk EOD provider + fetcher
  sharding (assessment recorded in pending-tasks; not planned).
