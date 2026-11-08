# 0033. Classification logical soundness, continuous validation loop, and deterministic-first command routing

- Date: 2026-11-08
- Status: accepted

## Context

1. **Stock archetype classification drift and catch-all bias:** `stock_archetype(factors)` in `manifests/telegram/stocks.py`, `manifests/stock-screener/scripts/stock_report.py`, and `marketview/app/main.py` had drifted in their branch definitions and relied on a final `else: return "Balanced"` catch-all. As a result, a classic **Value Trap** (cheap on Value ≥ 65th percentile, but bottom-tier Quality < 35th and negative/weak Momentum < 40th) or a broadly deteriorating company (mean factor score < 38th percentile with no standout factor ≥ 65th) fell through the positive archetype gates and was labeled **"Balanced"**.
2. **Multibagger endpoint-CAGR trough bias and GICS sector mismatch:**
   - `cagr(points)` in `stocks.py` and `marketview/app/multibagger.py` computed growth solely from `points[0]` (newest) and `points[-1]` (oldest). A cyclical company whose earnings spiked in the middle year and collapsed >15% in the most recent fiscal year (e.g., `100 → 320 → 160`) still showed a >15% 3-year CAGR off the depressed base and passed the growth gate.
   - `multibagger_candidate()` exempted `sector != "Financial Services"` from the `D/E <= 1.0` gate, matching Yahoo/NSE naming for India (`NIFTY500`), but US constituents (`SP500`, `SP400`, `SP600`) use GICS `"Financials"`, causing US financial compounders to fail the debt gate on normal deposit/funding leverage.
3. **No continuous validation loop for archetypes, factors, or multibagger gates:** While `predictions.py` (ADR 0028) records daily `screener_rank` and `valuation_upside` snapshots and resolves 30/91/182-day adjusted excess returns into `prediction_outcomes`, nothing evaluated whether:
   - Individual factor scores (`value`, `quality`, `growth`, `momentum`, `risk`) maintain positive cross-sectional Spearman Rank IC across universes and within sectors.
   - Stock archetypes (`Quality Compounder`, `GARP`, `Deep Value`, `Defensive Quality`) empirically outperform `Value Trap`, `High-Risk / Stressed`, and `Lagging / Weak` cohorts.
   - Each of the 6 multibagger gates adds positive **Gate Marginal Value (GMV)** relative to single-gate near-misses.
4. **Telegram AI integration and wrong-command handling:** Several Telegram commands had limited AI context (`ask.py` lacked screener, sector, regime, scorecard, and earnings facts; `/stock` and `/news` did not surface `per_symbol` / `event_brief` narratives from `llm_narratives`), `/watch tesla` failed when `add` was omitted, and typing the wrong command for a valid intent returned a static error instead of routing to the command that can fulfill the request.

## Decision

### 1. Dual Practitioner vs Academic Stock Archetypes & Composite (`stock_archetype` & `academic_archetype`)
Both the existing **Practitioner Profile** (25/25/20/20/10 weights) and the **Academic Research Lens** (equal-weighted 1/N 20/20/20/20/20 per DeMiguel et al. 2009 + peer-reviewed factor interactions) are computed and displayed side-by-side across `manifests/telegram/stocks.py`, `manifests/stock-screener/scripts/stock_report.py`, and `marketview/app/main.py`, with `archetype_comparison()` explicitly explaining the difference between the two lenses:
- **Practitioner Archetypes (`stock_archetype`):** `Quality Compounder`, `GARP`, `Deep Value`, `Momentum Leader`, `High-Growth Speculative`, `Defensive Quality`, `Turnaround / Cyclical`, `Value Trap`, `High-Risk / Stressed`, `Lagging / Weak`, and `Balanced`.
- **Academic Factor-Interaction Archetypes (`academic_archetype`):**
  - `QMJ + BAB Compounder (Asness 2019 / Frazzini 2014)`: `qu >= 0.70 and r >= 0.60 and mo >= 0.50`
  - `Piotroski-Filtered Value (Piotroski 2000 / Novy-Marx 2013)`: `v >= 0.70 and qu >= 0.60`
  - `Value × Momentum Crossover (Asness et al. 2013)`: `v >= 0.65 and mo >= 0.65`
  - `Mohanram G-Score Growth (Mohanram 2005)`: `g >= 0.70 and qu >= 0.60`
  - `Jegadeesh-Titman Momentum (Jegadeesh & Titman 1993)`: `mo >= 0.75 and r >= 0.45`
  - `Conservative Low-Vol (van Vliet 2018 / Ang 2006)`: `r >= 0.75 and qu >= 0.55 and v >= 0.45`
  - `Unprofitable Glamour Risk (Lakonishok 1994)`: `g >= 0.65 and v < 0.35 and qu < 0.50`
  - `Distress / Value-Trap Puzzle (Campbell et al. 2008)`: `(v >= 0.65 and qu < 0.35 and mo < 0.40) or (qu < 0.35 and r < 0.35)`
  - `Bottom-Quintile Factor Lag`: `mean(factors) < 0.38 and max(factors) < 0.65`
  - `1/N Factor-Neutral (DeMiguel 2009)`: `else`

### 2. Dual Practitioner vs Academic Multibagger Screens (`multibagger_candidate` & `multibagger_academic_candidate`)
In both `manifests/telegram/stocks.py` and `marketview/app/multibagger.py` (preserving the strict sync contract):
- **Practitioner Lens (Lynch / PEG-Growth):** Kept intact with two soundness fixes: `cagr(points)` rejects ≥3-point series where the newest fiscal year contracted >15% below the prior year (`new < points[1][1] * 0.85`), and `FINANCIAL_SECTORS = ("Financial Services", "Financials")` exempts both NSE and S&P financials from `D/E <= 1.0`.
- **Academic Lens (SQM: Size × Quality × 12-1 Momentum — Asness et al. 2018, Novy-Marx 2013, Sloan 1996, Jegadeesh & Titman 1993, George & Hwang 2004, Loughran & Wellman 2011):** Replaces multi-year CAGR extrapolation and backward-looking `PEG <= 2.0` with:
  1. `size_pct <= 0.70` (SMB size control)
  2. `roe >= 15%` or `operating_margin >= 12%` (Profitability RMW)
  3. **Hard `fcf > 0`** (Sloan 1996 accruals filter)
  4. `leverage <= 1.0` (`FINANCIAL_SECTORS` exempt — QMJ safety)
  5. `ret_12_1 > 0` (12m return skipping 1m reversal), `pos_52w >= 0.50`, and `sma200_ratio >= 1.0`
  6. `fcf_yield >= 1.5%` or `0 < pe <= 40` (Cash/Earnings yield)
  7. Equal `25/25/25/25` 1/N pillar weights (DeMiguel et al. 2009).
- Both Telegram `/multibagger` and MarketView `/multibagger` display both tables/lists side-by-side, highlight **Consensus Picks (passing both lenses)** vs **Practitioner-only** vs **Academic-only**, and explain why the two lenses differ.

### 3. Scientific Accounting & Factor Diagnostics (`EV-3`, `EV-4`, `EV-10`–`EV-14`, `Altman Z''`)
Using the EV-9 statement lines (`total_assets`, `gross_profit`, `ocf`, `shares_outstanding`, `current_assets`, `current_liabilities`, `operating_income`) and daily adjusted price series, we compute and surface the following peer-reviewed diagnostics in `stock_metrics.py`, `stocks.py` (`scientific_diagnostics`), `stock_report.py`, and MarketView (`/stock/{symbol}` & `/strategies`):
1. **12-1 Momentum (`ret_12_1`, EV-3) & 52-Week High Proximity (`near_high`, EV-4):** Computed daily in `stock_metrics.py` (`px[-22] / px[-253] - 1` and `close / max_252d_close`).
2. **Gross Profitability (`GP/A = gross_profit / total_assets`, EV-10):** Top-of-income-statement economic profitability before SG&A/R&D expensing (Novy-Marx 2013).
3. **Conservative Investment / Asset Growth (`CMA = YoY total_assets change`, EV-11):** Flags aggressive balance-sheet expansion vs disciplined capital allocation (Cooper, Gulen & Schill 2008; Fama & French 2015).
4. **Operating Accruals (`(net_income - ocf) / total_assets`, EV-12):** Separates cash-backed earnings (`< 0%`) from accrual-inflated earnings (`> +5%`, Sloan 1996).
5. **Net Share Issuance (`YoY shares_outstanding change`, EV-13):** Measures net buybacks (`< 0%`) vs equity dilution (`> 0%`, Pontiff & Woodgate 2008).
6. **Piotroski 9-Signal F-Score (`0–9`, EV-14):** Evaluates ROA > 0, OCF > 0, ΔROA > 0, OCF > NI, ΔLeverage <= 0, ΔCurrent Ratio > 0, non-dilution, ΔGross Margin > 0, and ΔAsset Turnover > 0 (Piotroski 2000).
7. **Altman $Z''$-Score (Non-Manufacturing / Emerging Solvency):** $Z'' = 6.56 X_1 + 3.26 X_2 + 6.72 X_3 + 1.05 X_4$ (`> 2.60` Safe · `1.10–2.60` Grey · `< 1.10` Distress; Altman 1968, 2000).

### 4. Continuous Classification & Factor Validation Loop (`ops-tools/classifier_loop.py`)
We introduce `ops-tools/classifier_loop.py` (sharing the workstation `PSQL_CMD` pattern with `ev0.py`, `ml0.py`, `ml1.py`, and `ml3.py`) to continuously validate all classifications against resolved `prediction_outcomes` (30/91/182d horizons) and write a rendered Markdown calibration report to `stock_reports` (`symbol = '__calibration__'`, viewable in MarketView at `/reports/__calibration__`):
1. **Factor & Sector Audit (`evaluate_factors`):** Computes rolling cross-sectional Spearman Rank IC, IC-IR, hit rate, and Q5−Q1 quintile excess return spread per factor globally and per `(universe, sector)`, flagging decaying factors (`mean_ic < -0.02`) and sector-specific inversions (`ic < -0.10`).
2. **Archetype Cohort Validation (`evaluate_archetypes`):** Computes realized excess return, median excess return, hit rate, and 10th-percentile tail return (`tail_p10_ret`) per archetype, and runs automated thesis soundness checks (`Quality Compounder > Lagging / Weak`, `Deep Value > Value Trap`, `Defensive Quality p10 tail > High-Risk / Stressed p10 tail`).
3. **Multibagger Gate Marginal Value (`evaluate_multibagger_gates`):** Compares candidates passing all 6 gates against **single-gate near-misses** (companies passing 5 of 6 gates and failing only gate $k$). Negative Gate Marginal Value ($\text{GMV}_k < -2\%$) flags over-restrictive gate thresholds.
4. **Bayesian Shrinkage Calibration (`propose_factor_weights`):** Blends baseline weights $W_0 = (0.25, 0.25, 0.20, 0.20, 0.10)$ with positive IC-IR weights using sample-size shrinkage $\lambda = \min(0.50, N / (N + 12))$, strictly bounded by ADR 0018 guardrails (`MIN_CROSS_SECTIONS = 6`, `MAX_TILT = 0.05` per factor, `MIN_WEIGHT = 0.05`, sum = `1.0000`).

### 5. Deterministic-first cross-command routing & universal fact-checked AI layer
In `manifests/telegram/router.py`, `bot.py`, `stocks.py`, and `ask.py`:
- **Deterministic precedence:** Exact command handlers and deterministic parsers always run first.
- **Cross-command fallback with explicit notice:** When a command is misspelled (`router.fuzzy_command`), when a user passes an index to `/stock` or a stock to `/index`, when `/watch <company>` omits `add`, or when a command's deterministic parser fails on arguments that another command can handle (`router.route` deterministic rules first, then `router.ai_route` via `llm-gateway` only if deterministic routing cannot resolve it), the bot notifies the user (`↪️ ... — running ...`) and executes the target command.
- **Fact-checked AI enrichment:** `ask.build_facts` now includes screener, sector, regime, scorecard, valuation, and earnings facts; `stocks.stock_card` and `stocks.news_text` surface verified `per_symbol` (ML-7b) and `event_brief` (ML-7c) narratives; and any command invoked with `ai` / `explain` / `why` / `?` appends a `factcheck.py`-verified takeaway (`ask.summarize_card`).

### 6. 15-Method Dual Practitioner vs Research-Backed Valuation Panel & Schema 25 Persistence (`valuation.py`, `stocks.py`, `stock_report.py`, `marketview`, `schema-configmap.yaml`)
We extend the valuation engine (`valuation.py`) and all presentation surfaces (`stocks.grouped_valuations`, `stock_report.render_report`, and `marketview/app/main.py`) from 10 methods to **15 deterministic intrinsic valuation methods**, explicitly grouped into **8 Practitioner Valuations** and **7 Research-Backed (Academic) Valuations**, while keeping the 10 core `METHOD_COLS` in `stock_valuations` (`out["methods"]` and `out["fair_value"]`) invariant for backward compatibility with `accuracy_check.py`:
- **Practitioner Valuation Lens (`practitioner_fair_value` median, `practitioner_methods` count):**
  1. *Graham Conservative Value*: `graham` ($\sqrt{22.5 \cdot \text{EPS} \cdot \text{BVPS}}$, Graham 1949) and `graham_growth` ($\text{EPS}(8.5 + 2g\%)\frac{4.4}{Y_{\text{AAA}}}$, Graham 1974).
  2. *GARP & Relative Comps*: `peg_lynch` ($\text{EPS} \cdot 100g_{\text{eps}}$, Lynch 1989), `peer_multiple` (sector-median $\text{P/E} \cdot \text{EPS}$ or $\text{P/B} \cdot \text{BVPS}$ for financials), and `ev_ebitda_peer` ($(\text{Median EV/EBITDA} \cdot \text{Implied EBITDA} - \text{Debt})/\text{Shares}$, Koller et al. 2020).
  3. *Cash-Flow & Owner Earnings*: `fcf_yield` ($\text{FCF}/\text{sh}/r$), `dcf` (5-year revenue-CAGR FCF DCF), and `owner_earnings` ($(\overline{\text{FCF}}_{5\text{y}}(1+g_T)/(r-g_T) - \text{Debt})/\text{Shares}$, Buffett 1986).
- **Research-Backed (Academic) Valuation Lens (`academic_fair_value` median, `academic_methods` count):**
  1. *Clean-Surplus & Intangible Accounting*: `residual_income` ($\min((\text{ROE}-g_T)/(r-g_T), 8)\cdot\text{BVPS}$, Ohlson 1995; Frankel & Lee 1998) and `intangible_ri` (Peters & Taylor 2017 / Arnott et al. 2021 Intangible-Adjusted Residual Income capitalizing $1.5\times$ annual overhead $(\text{GP}-\text{EBIT})$ into adjusted book equity $B^*$).
  2. *Abnormal Earnings Growth (AEG)*: `easton_mpeg` ($\frac{\text{EPS}_0 g_{\text{eps}} + r \cdot \text{DPS}_0 g_{\text{eps}}}{r^2}$, Easton 2004 / Ohlson & Juettner-Nauroth 2005 rate-sensitive replacement for PEG=1).
  3. *Corporate Finance & Cycle-Normalized*: `damodaran_fcff` (2-stage NOPAT DCF at WACC with $\text{Reinvestment Rate} = g/\text{ROC}$ minus debt, Damodaran 2012) and `epv` (5-year mean $\text{EBIT}(1-t)/r$ minus debt, Greenwald et al. 2001).
  4. *Shareholder Distribution Present Value*: `ddm` (Gordon 1956 dividend discount model) and `total_payout_ddm` (Boudoukh et al. 2007 Augmented Total Shareholder Payout DDM combining cash dividends and net share buybacks capped at FCF).
- **Database Persistence (`25-valuation-and-metrics-catalog.sql` in `schema-configmap.yaml` + `ensure_schema_and_catalog` in `valuation.py`):**
  1. **`valuation_methods` (34-row catalog table + `v_valuation_methods` view):** Stores `(method_key, method_name, category, lens, sub_school, source_ref, formula, target_table, display_order)` for all 15 valuation methods, 8 scientific diagnostics, 5 factor pillars, 2 composites, 2 archetype taxonomies, and 2 multibagger screens.
  2. **`stock_valuations` (+11 additive columns + `v_valuations_recent` / `v_latest_valuations` append-only views):** Persists `ev_ebitda_peer`, `owner_earnings`, `easton_mpeg`, `intangible_ri`, `total_payout_ddm`, `practitioner_fair_value`, `practitioner_methods`, `academic_fair_value`, `academic_methods`, `all_fair_value`, and `all_methods`.
  3. **`stock_scientific_metrics` (19-column hypertable + `v_latest_scientific_metrics` view):** Persists daily `(symbol, ts, universe, sector, gpa, operating_accruals, asset_growth_cma, net_share_issuance, piotroski_f_score, piotroski_f_tested, altman_z2, altman_zone, practitioner_archetype, academic_archetype, practitioner_composite, academic_composite, mb_practitioner_score, mb_academic_score, mb_lens)` via `scientific_row()`.
  4. **`sector_metrics` (+4 additive columns + `v_sector_latest` append-only view):** Persists `median_practitioner_upside_pct`, `median_academic_upside_pct`, `median_gpa`, and `median_f_score`.
  5. **`calibration_metrics` (plain table + `v_latest_calibration` view):** Persists structured calibration rows `(ts, horizon_days, kind, metric_key, universe, sample_n, value, aux_value, status)` written by `classifier_loop.py` (`factor_ic`, `sector_ic`, `archetype_ret`, `academic_archetype_ret`, `mb_gmv`, `shrunk_weight`).

## Consequences
- Schema `25-valuation-and-metrics-catalog.sql` is strictly additive and auto-bootstrapped by `valuation.py` (`ensure_schema_and_catalog`), obeying ADR 0032's append-only contract view rules and granting `SELECT` to both `grafana_ro` and `telegram_app`.
- Hermetic unit tests in `tests/test_classifier_loop.py`, `tests/test_router.py`, `tests/test_ask.py`, `tests/test_multiuser.py`, `tests/test_stocks.py`, `tests/test_valuation.py`, and `marketview/tests/test_app.py` verify every classification rule, scientific diagnostic, valuation method, catalog row, calibration guardrail, and routing path without network or DB dependencies.

## Peer-Reviewed References & Sources
- **Altman, E. I. (1968).** "Financial Ratios, Discriminant Analysis and the Prediction of Corporate Bankruptcy." *Journal of Finance*, 23(4), 589–609; and **Altman (2000)** $Z''$-Score for non-manufacturers and emerging markets.
- **Ang, A., Hodrick, R. J., Xing, Y., & Zhang, X. (2006).** "The Cross-Section of Volatility and Expected Returns." *Journal of Finance*, 61(1), 259–299.
- **Arnott, R. D., Harvey, C. R., Kalesnik, V., & Linnainmaa, J. T. (2021).** "Reports of Value's Death May Be Greatly Exaggerated." *Financial Analysts Journal*, 77(1), 44–67.
- **Asness, C. S., Moskowitz, T. J., & Pedersen, L. H. (2013).** "Value and Momentum Everywhere." *Journal of Finance*, 68(3), 929–985.
- **Asness, C. S., Frazzini, A., Israel, R., Moskowitz, T. J., & Pedersen, L. H. (2018).** "Size Matters, If You Control Your Junk." *Journal of Financial Economics*, 129(3), 479–509.
- **Asness, C. S., Frazzini, A., & Pedersen, L. H. (2019).** "Quality Minus Junk." *Review of Accounting Studies*, 24(1), 34–112.
- **Ball, R., & Brown, P. (1968).** "An Empirical Evaluation of Accounting Income Numbers." *Journal of Accounting Research*, 6(2), 159–178.
- **Bhojraj, S., Lee, C. M., & Oler, D. K. (2003).** "What's My Line? A Comparison of Industry Classification Schemes for Capital Market Research." *Journal of Accounting Research*, 41(5), 745–774.
- **Boudoukh, J., Michaely, R., Richardson, M., & Roberts, M. R. (2007).** "On the Importance of Measuring Payout Yield: Implications for Empirical Asset Pricing." *Journal of Finance*, 62(2), 877–915.
- **Campbell, J. Y., Hilscher, J., & Szilagyi, J. (2008).** "In Search of Distress Risk." *Journal of Finance*, 63(6), 2899–2939.
- **Chan, L. K., Karceski, J., & Lakonishok, J. (2003).** "The Level and Persistence of Growth Rates." *Journal of Finance*, 58(2), 643–684.
- **Cooper, M. J., Gulen, H., & Schill, M. J. (2008).** "Asset Growth and the Cross-Section of Stock Returns." *Journal of Finance*, 63(4), 1609–1651.
- **Damodaran, A. (2012).** *Investment Valuation: Tools and Techniques for Determining the Value of Any Asset* (3rd ed.). Wiley.
- **DeMiguel, V., Garlappi, L., & Uppal, R. (2009).** "Optimal Versus Naive Diversification: How Inefficient is the 1/N Portfolio Strategy?" *Review of Financial Studies*, 22(5), 1915–1953.
- **Easton, P. D. (2004).** "PE Ratios, PEG Ratios, and Estimating the Implied Expected Rate of Return on Equity Capital." *The Accounting Review*, 79(1), 73–95.
- **Fama, E. F., & French, K. R. (1992).** "The Cross-Section of Expected Stock Returns." *Journal of Finance*, 47(2), 427–465.
- **Fama, E. F., & French, K. R. (2015).** "A Five-Factor Asset Pricing Model." *Journal of Financial Economics*, 116(1), 1–22.
- **Frankel, R., & Lee, C. M. (1998).** "Accounting Valuation, Market Expectation, and Cross-Sectional Stock Returns." *Journal of Accounting and Economics*, 25(3), 283–319.
- **Frazzini, A., & Pedersen, L. H. (2014).** "Betting Against Beta." *Journal of Financial Economics*, 111(1), 1–25.
- **George, T. J., & Hwang, C.-Y. (2004).** "The 52-Week High and Momentum Investing." *Journal of Finance*, 59(5), 2145–2176.
- **Gordon, M. J. (1959).** "Dividends, Earnings, and Stock Prices." *Review of Economics and Statistics*, 41(2), 99–105.
- **Greenwald, B. C., Kahn, J., Sonkin, P. D., & van Biema, M. (2001).** *Value Investing: From Graham to Buffett and Beyond*. Wiley.
- **Jegadeesh, N. (1990).** "Evidence of Predictable Behavior of Security Returns." *Journal of Finance*, 45(3), 881–898.
- **Jegadeesh, N., & Titman, S. (1993).** "Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency." *Journal of Finance*, 48(1), 65–91.
- **Lakonishok, J., Shleifer, A., & Vishny, R. W. (1994).** "Contrarian Investment, Extrapolation, and Risk." *Journal of Finance*, 49(5), 1541–1578.
- **Loughran, T., & Wellman, J. W. (2011).** "New Evidence on the Relation Between the Enterprise Multiple and Average Stock Returns." *Journal of Financial and Quantitative Analysis*, 46(6), 1629–1650.
- **McLean, R. D., & Pontiff, J. (2016).** "Does Academic Research Destroy Stock Return Predictability?" *Journal of Finance*, 71(1), 5–32.
- **Mohanram, P. S. (2005).** "Separating Winners from Losers among Low Book-to-Market Stocks using Financial Statement Analysis." *Review of Accounting Studies*, 10(2–3), 133–170.
- **Moskowitz, T. J., & Grinblatt, M. (1999).** "Do Industries Explain Momentum?" *Journal of Finance*, 54(4), 1249–1290.
- **Novy-Marx, R. (2013).** "The Other Side of Value: The Gross Profitability Premium." *Journal of Financial Economics*, 108(1), 1–28.
- **Ohlson, J. A. (1995).** "Earnings, Book Values, and Dividends in Equity Valuation." *Contemporary Accounting Research*, 11(2), 661–687.
- **Ohlson, J. A., & Juettner-Nauroth, B. E. (2005).** "Expected EPS and EPS Growth as Determinants of Value." *Review of Accounting Studies*, 10(2–3), 349–365.
- **Peters, R. H., & Taylor, L. A. (2017).** "Intangible Capital and the Investment-q Relation." *Journal of Financial Economics*, 123(2), 251–272.
- **Piotroski, J. D. (2000).** "Value Investing: The Use of Historical Financial Statement Information to Separate Winners from Losers." *Journal of Accounting Research*, 38, 1–41.
- **Pontiff, J., & Woodgate, A. (2008).** "Share Issuance and Cross-Sectional Returns." *Journal of Finance*, 63(2), 921–945.
- **Sloan, R. G. (1996).** "Do Stock Prices Fully Reflect Information in Accruals and Cash Flows About Future Earnings?" *The Accounting Review*, 71(3), 289–315.

