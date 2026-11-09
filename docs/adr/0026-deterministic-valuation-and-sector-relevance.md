# 0026. Deterministic intrinsic valuations + sector metric relevance; never in the composite without EV-0

- Date: 2026-10-26
- Status: accepted

## Context
The owner wants every stock to carry actual valuation calculations and a per-sector view of
which metrics matter (P/E is meaningful for staples, a trap at commodity peaks, and nearly
meaningless for banks — where P/B x ROE is the real pair). The pipeline had only relative
multiples vs universe medians. Options: an LLM writing valuations (hallucination risk, gateway
budget, unreproducible), a single DCF (one set of assumptions pretending to be truth), or a
panel of the classic deterministic methods with every assumption pinned.

## Decision
1. **A panel of famous methods, all stored** (`stock_valuations`, schema 15; computed daily by
   `valuation.py`, screener toolkit): Graham number, sector peer multiple, FCF perpetuity,
   two-stage DCF, Gordon DDM, Greenwald EPV, residual income (justified P/B), Lynch PEG.
   Each is NULL when its inputs don't apply; `fair_value` = **median of the available methods**
   (robust to any one method's bias), `methods` records how many spoke.
2. **Pinned assumptions** (changing any means amending this ADR): discount rate = latest 10y
   (DGS10 / IN_GS_10Y) + **4.5% ERP**; terminal growth **2.5%**; DCF = 5 years of FCF at the
   revenue CAGR clamped to [0%, 15%]; DDM growth clamped to [0%, min(8%, r−1%)]; EPV uses mean
   of ≤5 annual EBITs, **25% tax**, nets total debt and carries **no cash credit** (cash is not
   ingested — conservative by construction); residual-income multiple capped at 8; PEG growth
   clamped [5%, 25%]; shares = market_cap / close until EV-9 `shares_outstanding` coverage
   completes. Histories come from point-in-time `financial_facts` annual rows.
3. **Sector relevance as data** (schema 16): `sector_styles` maps every
   `universe_constituents.sector` (both GICS and NSE taxonomies) to a style;
   `metric_relevance` weights each metric 0–3 per style **with the reason as text**. Weight 0 =
   actively misleading. The tables are curated in the schema ConfigMap — edit there, re-apply.
4. **The relevance tables drive the engine**: styles with `fcf`=0 skip the FCF/DCF legs (bank
   FCF is not economics); styles with `pe`≤1 and `pb`≥3 value on sector-median P/B x BVPS
   instead of P/E x EPS. Reports and the bot read the same tables for "what matters here".
5. **Sector aggregates** (`sector_metrics`, schema 17): daily medians (P/E, P/B, EV/EBITDA,
   ROE, margin), equal-weighted 6m/12m returns and median member upside per (universe, sector);
   rendered as a `__sectors__` report row.
6. **Hard rule: all of this is DESCRIPTIVE.** No valuation output or relevance weight enters
   `stock_scores`, the composite, alerts or `/multibagger` without passing EV-0 §5 — the same
   bar as every signal (ADR 0018 discipline).

## Consequences
- Every active symbol gets a reproducible fair-value panel daily; disagreement between methods
  is visible instead of averaged away (the per-method columns are kept).
- Known biases are explicit: EPV conservative (no cash), peer multiples inherit sector-wide
  mispricing, DDM only speaks for payers, Graham undervalues asset-light growth. The median
  composite tempers but does not remove them — reports label it "not advice".
- The relevance seed is a curated opinion. It lives in versioned SQL where a review can argue
  with it line by line, and upserts on re-apply.
- Dividend coverage (DDM) and reported-share counts complete with the EV-9 weekly pass
  (2026-10-31); until then DDM speaks for few names and shares are implied.

## Amendment (2026-10-26, owner-approved): the valuation watch alert

The owner asked for notifications "when a high-confidence stock makes sense to buy, with
reasoning". This amends consequence #6 for ALERTS ONLY — scores/composite/multibagger
remain EV-0-gated. `stocks.alert_valuation` (telegram v25) adds shared lines to the daily
change alert when a name ENTERS the set: screener-eligible, rank ≤ 50, fair-value upside
≥ 25% from ≥ 4 methods with ≥ 3 methods individually above the close (agreement, not just
a skewed median). Stateless entry detection (today's valuation run vs the previous one),
≤ 5 lines per run, each line carrying the reasoning as a data echo (fair vs close, method
agreement, rank, composite, sector lens) and the "not advice" label.
