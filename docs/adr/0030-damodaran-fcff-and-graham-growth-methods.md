# 0030. Two more famous intrinsic methods: Damodaran FCFF and Graham's growth formula

- Date: 2026-10-27
- Status: accepted (deployed: `screener:v26`, surfaced in marketview v7 / telegram v30 / Grafana / reports)

## Context
The owner asked for "valuation of Aswath Damodaran etc" on the ADR 0026 panel.
The panel had 8 methods; the most famous omissions computable deterministically
from ingested data are Damodaran's FCFF story-to-numbers DCF and Benjamin
Graham's revised (1974) growth formula.

## Decision
Two columns added to `stock_valuations` (schema 20, additive), both NULL when
inputs are missing, both feeding the fair-value median like every other method:

1. **`damodaran_fcff`** — two-stage FCFF with Damodaran's *fundamental growth*
   identity (growth must be paid for): reinvestment rate = g / ROC, where
   ROC = NOPAT / (book debt + equity) and NOPAT = latest annual EBIT × (1−25%).
   Five years at the revenue-CAGR g (clamped [0,15%], same as the house DCF),
   terminal at 2.5% with the stable reinvestment g_T/ROC. Discounted at WACC:
   ke = 10y + 4.5% ERP (the house rate), kd = (10y + 2% synthetic spread) ×
   (1−tax), market-value weights (E = mcap, D = total debt). Firm value minus
   debt, **no cash credit** (same conservative stance as EPV — cash isn't
   ingested). Guards: NULL unless EBIT>0, equity>0, ROC and WACC > terminal g;
   reinvestment capped at 0.9; negative equity value → NULL.
2. **`graham_growth`** — V = EPS × (8.5 + 2g%) × 4.4 / Y, Graham's revised
   formula. g = EPS CAGR clamped [0, 15%]; Y (AAA corporate yield) proxied as
   the 10y + 1% since corporate curves aren't ingested. Distinct from the
   existing `graham` (the √(22.5·EPS·BVPS) Graham *number*).

## Consequences
- The panel is 10 methods; `methods` counts and the median absorb the new
  columns automatically, as do the valuation-watch agreement gate (two more
  CASE legs), reports, marketview (tuple indices shifted +2 in templates),
  the Grafana method barchart, and the DQ-5 median recompute.
- Assumption honesty: the 2% debt spread and the +1% AAA proxy are documented
  synthetic inputs, not market observations — both named here and in the
  script header so nobody mistakes them for data.
- Historical rows keep NULLs in the new columns; the panel is append-only
  from 2026-10-28's run (plus today's rerun).
