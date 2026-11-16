# Market Pipeline: Recommended Open-Source Improvements

> **Review 2026-11-15: this is an external proposal, not an agreed plan.** It was written without
> seeing the repo, so several P0/P1 items duplicate existing tooling or assume infrastructure that
> doesn't exist. The document below is kept unedited. Verdicts after checking it against the repo:
>
> | Item | Verdict | Reason |
> |---|---|---|
> | Postgres Exporter (P0) | **Change approach** | There is **no Prometheus** in the cluster; Grafana has Postgres data sources only. Instead: a monitoring role with `pg_monitor` and Grafana panels on `pg_stat_*` / `pg_database_size`. Same visibility, no new service. Tracked in `pending-tasks.md`. |
> | Soda Core (P0) | **Not now** | Mostly covered by ML-1 (`ops-tools/ml1.py`: bad bars, feed/row-count drops, scraper silence), `pipeline_heartbeat` freshness, `job_runs`, `accuracy_check.py` (DQ-5) and `data-gaps.md`. The real gap is duplicate-key and OHLC sanity rules, which will be added to ML-1 (tracked). Too heavy a dependency set for 1 GB arm64 nodes. |
> | pgBackRest / PITR (P0) | **Partly done; PITR optional** | Nightly `pg-backup` dump, workstation pull, a passing `restore-drill-job.yaml` (2026-10-17: 30 s, 0 errors), and an off-site copy decided (Vostro, `restic`; tracker AR-11). PITR needs WAL archiving on the EOL TX2. Decide after the off-site copy runs, weighing the write-once tables (prediction snapshots, headlines, `bot_users`). |
> | dbt Core (P1) | **Skip** | Schema keys + `v_*` contract views (ADR 0032, `database-contract.md`) already own the schema and lineage. dbt would be a second owner of views. |
> | VectorBT (P1) | **Duplicates EV-0** | `ops-tools/ev0.py` already has point-in-time membership (ADR 0025), corporate actions, walk-forward/leakage tests (`ml0.py`) and the §5 gate. Its proposed first strategies (RSI mean reversion, MA momentum) are ones `evidence-based-signals-roadmap.md` rates as weakly backed. Check the open-source VectorBT's maintenance status first. |
> | MLflow (P1/P2) | **Not yet** | `app.ml_runs` tracks experiments (ML-0). |
> | ML4T / Zipline (P2/P3) | Defer | Only if EV-0 needs execution-aware simulation. |
> | Financial-DataBase provenance (§3.8) | **Adopt as a review checklist** | `financial_facts` already keeps filing dates and EV-19/20 use them correctly. The 2026-11-15 branch review found G-21 `distress_score` joining on `period_end` (look-ahead), exactly this bug class. |
> | Warehouse layers, Dagster, OpenTelemetry, Great Expectations | Don't | Agrees with §9 "What not to do yet". |
>
> **Out of date:** §5's hardware table predates the 2026-11-15 target layout (`node-roles.md` §1b: Orins
> GPU-only, `k3s-cp` on the N100, Vostros off-site). Research belongs on the 3090 workstation, but mount
> the SN850 spare partition first (`/home` is 95% full). §6 Phase 2 "build an isolated restore target"
> already exists.

**Engineering assessment and integration roadmap**\
**Prepared:** 15 November 2026\
**Scope:** Recommendations for the existing
`ahemanthyadav/market-pipeline` project

------------------------------------------------------------------------

## 1. Executive summary

The goal is to improve the existing market-data platform without
destabilizing its deployed architecture.

The current platform already combines financial-data ingestion,
TimescaleDB, Python analytics, Kubernetes CronJobs, Grafana, sentiment
processing, and operational tooling. The recommended approach is
therefore **selective adoption of proven components**, not a wholesale
migration to a new data stack.

The highest-value opportunities are:

1.  **Data quality:** detect incomplete, stale, duplicated, or
    implausible financial data before it reaches downstream analytics.
2.  **Database observability and recovery:** make database health
    visible and prove that backups can be restored.
3.  **Analytical transformations:** make SQL models testable,
    documented, and traceable.
4.  **Quantitative research:** turn historical data and indicators into
    reproducible strategy experiments and realistic backtests.
5.  **Financial-data provenance:** preserve filing dates, reporting
    periods, amendments, and information-availability dates to prevent
    look-ahead bias.

### Recommended adoption order

  -------------------------------------------------------------------------------------------------------------------------------
  Priority          Project                                                                   Primary value     Recommendation
  ----------------- ------------------------------------------------------------------------- ----------------- -----------------
  P0                [Prometheus PostgreSQL                                                    Database          Adopt
                    Exporter](https://github.com/prometheus-community/postgres_exporter)      visibility        

  P0                [Soda Core](https://github.com/sodadata/soda-core)                        Data quality      Pilot and adopt
                                                                                              checks            selectively

  P0                [pgBackRest](https://github.com/pgbackrest/pgbackrest)                    Backup and        Evaluate with
                                                                                              point-in-time     restore testing
                                                                                              recovery          

  P1                [dbt Core](https://github.com/dbt-labs/dbt-core)                          SQL               Adopt selectively
                                                                                              transformations   
                                                                                              and lineage       

  P1                [VectorBT](https://github.com/polakowo/vectorbt)                          Fast quantitative Adopt in a
                                                                                              research          separate research
                                                                                                                environment

  P1                [MLflow](https://github.com/mlflow/mlflow)                                Experiment        Add when
                                                                                              tracking          experiments
                                                                                                                multiply

  P2                [ML4T Backtest](https://github.com/ml4t/backtest)                         Execution-aware   Evaluate after a
                                                                                              backtesting       VectorBT
                                                                                                                prototype

  P2                [Financial-DataBase](https://github.com/JdeJusto/Financial-DataBase)      SEC ingestion     Borrow selected
                                                                                              patterns and      patterns
                                                                                              provenance        

  P2                [Financial Data                                                           Warehouse         Study and adapt
                    Warehouse](https://github.com/pspotq/financial-data-warehouse)            organization      concepts
                                                                                              patterns          

  P3                [Dagster](https://github.com/dagster-io/dagster)                          Asset-centric     Study; do not
                                                                                              orchestration and migrate current
                                                                                              lineage           CronJobs yet

  P3                [OpenTelemetry                                                            Unified logs,     Add when
                    Collector](https://github.com/open-telemetry/opentelemetry-collector)     metrics, and      application
                                                                                              traces            complexity
                                                                                                                warrants it

  Alternative       [Great                                                                    Data validation   Evaluate as an
                    Expectations](https://github.com/great-expectations/great_expectations)                     alternative to
                                                                                                                Soda, not
                                                                                                                alongside it
  -------------------------------------------------------------------------------------------------------------------------------

**Important:** This is an architectural recommendation, not a claim that
every repository has been tested against the exact production versions,
ARM64 environment, or TimescaleDB configuration. Pin versions and
validate compatibility in an isolated environment before production
adoption.

------------------------------------------------------------------------

## 2. Existing architecture and design principles

The current platform's broad architecture is:

``` text
External data providers
  NSE | Yahoo Finance | SEC | FRED | RSS | other sources
                       |
                       v
                Python fetchers
                       |
                       v
                  k3s CronJobs
                       |
                       v
                  TimescaleDB
                       |
          +------------+-------------+
          |            |             |
          v            v             v
      Indicators   Fundamentals   Sentiment
          |            |             |
          +------------+-------------+
                       |
                       v
             Screening and analytics
                       |
                       v
              Grafana / Marketview
                       |
                       v
                Alerts / AI layer
```

### Preserve these architectural choices

-   Keep TimescaleDB as the central source of truth.
-   Keep the existing ingestion jobs and Kubernetes scheduling
    initially.
-   Keep database schema migrations under the existing migration
    process.
-   Keep the production cluster focused on ingestion, storage,
    validation, scheduled analytics, and lightweight monitoring.
-   Run resource-intensive research and model experiments on the RTX
    3090 workstation or another capable machine.
-   Keep new components separate from deployed production workloads
    until they pass validation.
-   Avoid introducing Kafka, Spark, Airflow, or a second orchestration
    platform without a demonstrated requirement.

### Design principles for every integration

1.  **Additive before replacement:** introduce a new capability beside
    the current implementation.
2.  **Reproducible:** record code version, configuration, data window,
    and execution time.
3.  **Idempotent:** rerunning a task should not corrupt or duplicate
    data.
4.  **Observable:** each job should expose outcome, duration, record
    counts, and errors.
5.  **Least privilege:** applications should not use the PostgreSQL
    superuser.
6.  **Point-in-time correct:** research must use only information
    available at the simulated decision time.
7.  **Resource-aware:** avoid placing heavy services on constrained Pi
    or Jetson nodes.
8.  **Test restores, not just backups:** a backup is not proven until a
    restore succeeds.

------------------------------------------------------------------------

# 3. Project evaluations

## 3.1 Soda Core --- data quality

**Repository:**
[sodadata/soda-core](https://github.com/sodadata/soda-core)\
**Priority:** P0\
**Suggested fit:** High\
**Suggested deployment:** Separate validation job on a capable worker or
workstation

### Problem it addresses

A successful CronJob does not prove that its output is complete or
correct. A provider can return a partial response, omit symbols, change
a field's meaning, or deliver stale data while the fetcher still exits
successfully.

Soda provides declarative data-quality checks that can be run against
datasets and databases.

### Potential capabilities

  Capability         Application to this platform
  ------------------ -------------------------------------------------------
  Row-count checks   Detect unexpectedly empty or incomplete loads
  Null checks        Detect missing symbols, dates, prices, or identifiers
  Duplicate checks   Detect duplicate natural keys
  Freshness checks   Detect stale provider feeds
  Schema checks      Detect unexpected schema changes
  SQL checks         Encode financial and business rules
  Check results      Retain validation outcomes for monitoring

### Example validation rules

The following is illustrative pseudocode/YAML. Confirm the exact syntax
and supported check types against the selected Soda version before use.

``` yaml
checks for daily_prices:
  - row_count > 0
  - duplicate_count(symbol, trade_date) = 0
  - missing_count(symbol) = 0
  - missing_count(close) = 0
  - min(close) > 0
```

Financial-specific rules should include:

-   `high >= low`
-   `high >= open`
-   `high >= close`
-   `low <= open`
-   `low <= close`
-   `volume >= 0`
-   one record per expected symbol/date key
-   latest date consistent with the exchange calendar
-   expected versus received symbol coverage
-   no unexplained large price discontinuities
-   source timestamps and reporting periods are present where required

Do not use a universal maximum-price threshold. Thresholds must be
asset-class-, currency-, and source-aware. Corporate actions and genuine
market moves must not be incorrectly rejected.

### Suggested integration

``` text
Fetcher
   |
   v
TimescaleDB raw/staging data
   |
   v
Soda validation job
   |
   +---- PASS ----> downstream analytics
   |
   +---- WARN ----> analytics allowed + alert
   |
   +---- FAIL ----> quarantine/block affected dataset + alert
```

Use three severity levels:

  -----------------------------------------------------------------------
  Severity                Example                 Action
  ----------------------- ----------------------- -----------------------
  Critical                Duplicate natural keys, Block affected
                          impossible OHLC values, downstream processing
                          empty required dataset  

  Warning                 Lower-than-usual symbol Continue only under an
                          coverage, delayed       explicit policy; alert
                          provider                

  Informational           Unusual but explainable Record for review
                          volume or price         
                          movement                
  -----------------------------------------------------------------------

A failed check should not automatically stop unrelated datasets or all
platform workloads.

### Proposed repository structure

``` text
quality/
  soda/
    configuration/
    checks/
      daily_prices.yml
      fundamentals.yml
      technical_indicators.yml
      sentiment.yml
      fx.yml
    reports/
```

### Benefits

-   Detects silent ingestion failures.
-   Reduces the chance of bad data contaminating indicators and
    research.
-   Makes data-quality expectations explicit and reviewable.
-   Provides a common validation layer across providers.
-   Helps distinguish provider failures from application failures.

### Risks and validation

-   Check current Soda licensing and feature availability.
-   Confirm PostgreSQL/TimescaleDB connector compatibility.
-   Confirm Python dependency compatibility.
-   Run validation outside the most constrained node if dependencies are
    too heavy.
-   Ensure quality checks use bounded queries and suitable indexes.
-   Store check results so Grafana can display historical quality
    trends.

### Acceptance criteria

-   A deliberately incomplete test load is detected.
-   Duplicate natural keys are detected.
-   Stale data is detected using the correct exchange calendar.
-   A warning does not block unrelated pipelines.
-   Critical failures prevent only the affected downstream dataset from
    being refreshed.
-   Every validation run records dataset, timestamp, outcome, and check
    details.

------------------------------------------------------------------------

## 3.2 Prometheus PostgreSQL Exporter --- database observability

**Repository:**
[prometheus-community/postgres_exporter](https://github.com/prometheus-community/postgres_exporter)\
**Priority:** P0\
**Suggested fit:** Very high\
**Suggested deployment:** Lightweight exporter close to TimescaleDB,
scraped by Prometheus

### Problem it addresses

The database can be reachable and still be approaching a failure state.
Examples include connection exhaustion, disk growth, dead tuples, slow
queries, autovacuum delays, and increased temporary-file usage.

The exporter exposes PostgreSQL statistics as Prometheus metrics for
dashboards and alerts.

### Metrics to monitor

  -----------------------------------------------------------------------
  Area                    Metric or signal        Why it matters
  ----------------------- ----------------------- -----------------------
  Connections             Active and total        Detect connection
                          connections             exhaustion

  Capacity                Database and table size Plan storage before
                                                  disks fill

  Transactions            Commits, rollbacks,     Detect abnormal
                          deadlocks               transaction behavior

  Cache                   Buffer/cache hit        Understand read
                          statistics              efficiency

  Tables                  Sequential scans and    Identify query and
                          row activity            indexing patterns

  Indexes                 Index scans and index   Understand index usage
                          size                    

  Maintenance             Dead tuples and vacuum  Detect table bloat and
                          activity                maintenance lag

  Queries                 Long-running queries,   Find expensive
                          where separately        workloads
                          instrumented            

  Storage                 Temporary files and     Detect memory/workload
                          bytes                   pressure

  WAL                     WAL generation and      Understand write and
                          archive status          recovery behavior
  -----------------------------------------------------------------------

Some query-level metrics require additional collectors or PostgreSQL
extensions and are not guaranteed by the exporter alone. Use
`pg_stat_statements` if appropriate and verify its compatibility and
overhead.

### Suggested architecture

``` text
TimescaleDB
    |
    v
postgres_exporter
    |
    v
Prometheus
    |
    v
Grafana dashboards and alerts
```

### Recommended dashboards

1.  **Database overview:** availability, connections, transactions,
    database size.
2.  **Capacity:** database growth, largest tables, largest indexes, disk
    free space.
3.  **Maintenance:** dead tuples, vacuum activity, analyze activity.
4.  **Workload:** transaction rates, row reads/writes, temporary files.
5.  **Reliability:** restarts, failed scrapes, archive status where
    available.

### Security

Create a dedicated monitoring role. Do not use the `postgres` superuser
for exporter access. Grant only the monitoring privileges required by
the exporter, commonly including PostgreSQL's `pg_monitor` role where
appropriate.

Keep credentials in Kubernetes Secrets, not manifests committed to Git.

### Benefits

-   Detects database problems before applications fail.
-   Makes storage growth visible.
-   Helps identify maintenance and query-performance issues.
-   Provides evidence for capacity planning.
-   Requires little or no change to ingestion code.

### Acceptance criteria

-   Prometheus successfully scrapes the exporter.
-   Grafana shows database size, connections, transaction activity, and
    table maintenance.
-   Exporter uses a dedicated least-privilege account.
-   Alerts fire in a controlled test.
-   Exporter resource consumption is measured on the selected node.

------------------------------------------------------------------------

## 3.3 pgBackRest --- backup and recovery

**Repository:**
[pgbackrest/pgbackrest](https://github.com/pgbackrest/pgbackrest)\
**Priority:** P0\
**Suggested fit:** High\
**Suggested deployment:** Separate backup target; evaluate on the exact
database version

### Problem it addresses

Scheduled backups are useful, but they do not prove that the database
can be recovered. A single TimescaleDB instance is a central point of
failure. Recovery must be tested independently of the production
database.

pgBackRest supports full, differential, and incremental backups,
compression, retention policies, remote repositories, parallel
operations, and PostgreSQL point-in-time recovery through WAL archiving.

### Capabilities and benefits

  -----------------------------------------------------------------------
  Capability                          Benefit
  ----------------------------------- -----------------------------------
  Full backup                         Complete baseline for recovery

  Differential backup                 Reduce backup work since last full
                                      backup

  Incremental backup                  Reduce backup time and storage

  Compression                         Reduce repository size

  Parallel backup/restore             Improve throughput where CPU and
                                      storage allow

  WAL archiving                       Support point-in-time recovery

  Retention                           Manage backup lifecycle

  Remote repository                   Separate backup copy from database
                                      host
  -----------------------------------------------------------------------

### Suggested target architecture

``` text
Jetson TX2 / TimescaleDB
          |
          v
      pgBackRest
          |
          +--------> Pi 3 SSD repository
          |
          +--------> Workstation copy
          |
          +--------> Encrypted off-site copy
```

### Recovery scenarios to test

1.  Restore the latest full backup to an isolated PostgreSQL/TimescaleDB
    instance.
2.  Restore after simulated loss of the TX2.
3.  Restore to a point before a deliberately introduced test error.
4.  Verify extension versions and hypertable data after restore.
5.  Measure recovery time and recovered data point.

### TimescaleDB-specific caution

Validate the exact TimescaleDB extension version, PostgreSQL major
version, extension library availability, WAL requirements, and restore
procedure. A PostgreSQL backup procedure is not fully validated for this
system until TimescaleDB objects and extension behavior are verified
after recovery.

Do not replace the existing backup system until a parallel test proves
the new recovery path.

### Benefits

-   Reduces potential data loss.
-   Enables point-in-time recovery when configured correctly.
-   Improves recovery repeatability.
-   Supports recovery onto replacement hardware.
-   Makes recovery time and recovery point measurable.

### Acceptance criteria

-   A complete restore succeeds on a separate host or isolated instance.
-   Restored hypertables and representative rows are verified.
-   A point-in-time restore is demonstrated if PITR is part of the
    selected design.
-   Backup copies are not all dependent on the TX2.
-   Recovery time and backup age are documented.
-   Backup repository access is protected and encrypted where
    appropriate.

------------------------------------------------------------------------

## 3.4 dbt Core --- SQL transformation, testing, and lineage

**Repository:**
[dbt-labs/dbt-core](https://github.com/dbt-labs/dbt-core)\
**Priority:** P1\
**Suggested fit:** High\
**Suggested deployment:** Separate analytics environment connected to
TimescaleDB

### Problem it addresses

As SQL transformations grow, it becomes difficult to know which models
depend on which tables, how to test their outputs, and what needs to be
rebuilt after a source change.

dbt organizes SQL transformations as models and tracks dependencies
between them.

### Potential uses

-   Standardize staging models.
-   Calculate daily returns.
-   Build sector-level metrics.
-   Build factor-return datasets.
-   Create research marts.
-   Document analytical columns.
-   Test uniqueness, nullability, and business rules.
-   Build incremental analytical models where appropriate.

### Suggested model layout

``` text
analytics/dbt/
  dbt_project.yml
  models/
    staging/
      stg_daily_prices.sql
      stg_fundamentals.sql
      stg_company_profiles.sql
    intermediate/
      int_daily_returns.sql
      int_sector_metrics.sql
      int_factor_exposures.sql
    marts/
      market_momentum.sql
      stock_screening.sql
      valuation_summary.sql
  tests/
  macros/
  snapshots/
```

### Example: daily returns model

Illustrative SQL:

``` sql
WITH prices AS (
    SELECT
        symbol,
        trade_date,
        close
    FROM {{ ref('stg_daily_prices') }}
),
with_previous AS (
    SELECT
        symbol,
        trade_date,
        close,
        LAG(close) OVER (
            PARTITION BY symbol
            ORDER BY trade_date
        ) AS previous_close
    FROM prices
)
SELECT
    symbol,
    trade_date,
    close,
    previous_close,
    close / NULLIF(previous_close, 0) - 1 AS daily_return
FROM with_previous
```

`ref()` tells dbt that the model depends on another model and helps
build the dependency graph.

### Responsibility boundaries

  Responsibility               Keep with
  ---------------------------- ------------------------------------
  Raw provider ingestion       Existing Python fetchers
  Database schema migrations   Existing numbered migration system
  Analytical SQL models        dbt
  Data-quality checks          Soda or another selected validator
  Time-series storage          TimescaleDB
  Dashboards                   Grafana

Do not allow dbt to silently become a second owner of production schema
migrations.

### TimescaleDB compatibility checks

Before adoption, test:

-   PostgreSQL adapter compatibility with PostgreSQL 16.
-   TimescaleDB hypertable behavior.
-   Continuous aggregates and Timescale-specific functions.
-   Materialized views.
-   Incremental model semantics.
-   Transaction behavior and DDL.
-   Query plans and bounded time-window filters.

Start with read-oriented models and a dedicated schema. Avoid
destructive changes to existing contract views.

### Benefits

-   Clear SQL dependency graph.
-   Reusable transformations.
-   Built-in testing and documentation patterns.
-   Easier impact analysis.
-   More maintainable analytical SQL.
-   Better separation between ingestion and analytics.

### Acceptance criteria

-   A small set of staging models builds successfully.
-   Tests catch an intentionally introduced invalid record.
-   Model documentation can be generated.
-   Existing production contract views remain unchanged.
-   Query performance is measured against equivalent existing SQL.
-   The project can be run independently of production ingestion.

------------------------------------------------------------------------

## 3.5 VectorBT --- quantitative strategy research

**Repository:**
[polakowo/vectorbt](https://github.com/polakowo/vectorbt)\
**Priority:** P1\
**Suggested fit:** Very high for research\
**Suggested deployment:** RTX 3090 workstation or MacBook, not the Pi
control plane

### Problem it addresses

The current platform calculates indicators and stores historical market
data. The next question is whether those indicators produce useful
strategies after realistic costs and constraints.

VectorBT supports fast, vectorized portfolio and strategy research using
Python's numerical ecosystem.

### Potential capabilities

-   Strategy backtesting.
-   Signal evaluation.
-   Parameter sweeps.
-   Portfolio simulation.
-   Performance statistics.
-   Comparison across assets and periods.
-   Interactive analysis.

### Example research flow

``` text
TimescaleDB
    |
    v
Point-in-time historical dataset
    |
    v
Feature and signal generation
    |
    v
VectorBT simulation
    |
    v
Performance and risk metrics
    |
    v
Experiment results
```

### Example: RSI mean-reversion prototype

Illustrative code; confirm API behavior against the pinned VectorBT
version and validate signal alignment before relying on results.

``` python
import vectorbt as vbt

rsi = vbt.RSI.run(
    close,
    window=[10, 14, 21]
)

entries = rsi.rsi_below(30)
exits = rsi.rsi_above(70)

portfolio = vbt.Portfolio.from_signals(
    close,
    entries,
    exits,
    init_cash=100_000,
    fees=0.001,
    slippage=0.001
)

print(portfolio.stats())
```

### Initial strategies to test

1.  RSI mean reversion.
2.  Moving-average momentum.
3.  Sector-relative momentum.

For each strategy, record:

-   Annualized return.
-   Volatility.
-   Sharpe and Sortino ratios.
-   Maximum drawdown.
-   Turnover.
-   Number of trades.
-   Win rate.
-   Average holding period.
-   Exposure and concentration.
-   Benchmark-relative return.

### Essential correctness controls

A backtest can look excellent while being invalid. Explicitly control
for:

-   Survivorship bias.
-   Look-ahead bias.
-   Point-in-time index membership.
-   Corporate actions and adjusted prices.
-   Trading calendars and holidays.
-   Transaction costs.
-   Slippage.
-   Delisted securities.
-   Missing data.
-   Train/validation/test separation.
-   Parameter overfitting.
-   Walk-forward evaluation.

The existing historical index-membership snapshots are especially
valuable. Use the constituents that were actually eligible at each
historical date, not today's universe.

### Benefits

-   Turns indicators into measurable strategy hypotheses.
-   Allows rapid testing of parameter choices.
-   Reuses existing historical data.
-   Enables comparison against benchmarks.
-   Creates a foundation for systematic research.

### Acceptance criteria

-   A reproducible strategy runs against a documented dataset.
-   The test uses point-in-time universe membership.
-   Costs and execution timing are explicit.
-   Results include risk and turnover metrics.
-   A held-out period is evaluated.
-   Experiment configuration and outputs are saved.

------------------------------------------------------------------------

## 3.6 ML4T Backtest --- execution-aware simulation

**Repository:** [ml4t/backtest](https://github.com/ml4t/backtest)\
**Priority:** P2\
**Suggested fit:** High after a VectorBT prototype\
**Suggested deployment:** Research workstation

### Problem it addresses

Fast vectorized research is useful, but execution assumptions can
materially change results. A strategy that assumes a fill at the same
bar's close may overstate achievable performance if the signal was only
known after that close.

An event-driven or execution-oriented simulator can make order timing,
fills, position limits, and portfolio constraints more explicit.

### What to evaluate

-   Order and fill semantics.
-   Next-bar execution assumptions.
-   Slippage and commission models.
-   Position and cash constraints.
-   Portfolio accounting.
-   Trade and fill exports.
-   Equity curve and daily P&L.
-   Reproducible configuration.

### Suggested comparison

``` text
Same point-in-time dataset
          |
          v
    Same strategy signals
          |
       +--+--+
       |     |
       v     v
   VectorBT  ML4T Backtest
       |     |
       +--+--+
          |
          v
  Compare assumptions and results
```

If results differ, investigate why. Do not assume one engine is correct
merely because its return is higher.

### Limitations to check

Verify support for the required asset classes, corporate actions, borrow
costs, taxes, currencies, trading calendars, and data formats. These are
especially important for a platform spanning Indian and international
markets.

### Benefits

-   Exposes unrealistic fill assumptions.
-   Improves confidence in strategy evaluation.
-   Makes transaction-cost assumptions more explicit.
-   Supports more realistic portfolio constraints.

### Acceptance criteria

-   One VectorBT strategy is reproduced in the second engine.
-   Execution timing and fees are documented.
-   Trade and fill records can be inspected.
-   Differences in results are explained.
-   Known limitations are recorded.

------------------------------------------------------------------------

## 3.7 Zipline Reloaded --- event-driven and factor research

**Repository:**
[stefan-jansen/zipline-reloaded](https://github.com/stefan-jansen/zipline-reloaded)\
**Priority:** P2/P3\
**Suggested fit:** Moderate\
**Suggested action:** Study before adopting

### What it provides

Zipline Reloaded is an event-driven backtesting framework with
historical data, trading calendars, portfolio simulation, order
execution, and a Pipeline API for factor calculations.

### Potential use cases

-   Cross-sectional equity ranking.
-   Momentum and value factors.
-   Portfolio rebalancing.
-   Historical strategy simulation.
-   Event-driven execution research.

### Example research concept

``` text
Historical eligible universe
          |
          v
  Momentum / value / quality
          |
          v
  Cross-sectional ranking
          |
          v
  Select top-ranked assets
          |
          v
  Monthly portfolio rebalance
          |
          v
  Benchmark and risk evaluation
```

### Why it is not the first choice

It introduces another data model and ingestion adapter. Its
compatibility with current Python dependencies, Indian market calendars,
corporate actions, and the existing TimescaleDB representation must be
established.

### Benefits

-   Useful reference for event-driven simulation.
-   Useful factor and cross-sectional research patterns.
-   Alternative design to vectorized backtesting.

### Recommendation

Do not install VectorBT, ML4T, and Zipline simultaneously. Start with
VectorBT and evaluate a second engine only after you have a strategy and
a defined validation requirement.

------------------------------------------------------------------------

## 3.8 Financial-DataBase --- SEC ingestion and provenance patterns

**Repository:**
[JdeJusto/Financial-DataBase](https://github.com/JdeJusto/Financial-DataBase)\
**Priority:** P2\
**Suggested fit:** High for US fundamentals\
**Suggested action:** Reuse selected patterns

### Problem it addresses

Fundamental data is not just a value and a fiscal year. Historical
research must know when a value became publicly available, which filing
supplied it, whether it was amended, and which reporting period it
describes.

### Important fields

A fundamental fact should preserve information such as:

-   Company identifier, such as CIK.
-   Ticker mapping with validity dates.
-   Taxonomy and concept.
-   Value and unit.
-   Period start and period end.
-   Filing date.
-   Accession number.
-   Form type.
-   Amendment status.
-   Source and ingestion timestamp.
-   Dimensional context where applicable.

Illustrative schema only:

``` sql
CREATE TABLE financial_facts (
    company_id TEXT NOT NULL,
    taxonomy TEXT NOT NULL,
    concept TEXT NOT NULL,
    value NUMERIC,
    unit TEXT,
    period_start DATE,
    period_end DATE,
    filed_at DATE,
    accession_number TEXT,
    source TEXT,
    ingested_at TIMESTAMPTZ
);
```

A production schema must also represent dimensions, fiscal periods,
amendments, company identifiers, and source-specific semantics.

### Point-in-time correctness example

Suppose a company reports fiscal-year revenue for the year ending
December 31, 2024, but files the annual report on March 15, 2025.

A backtest with a decision date of March 1, 2025 must not use that
filing.

Keep distinct:

``` text
period_end:   2024-12-31
filed_at:     2025-03-15
available_at: 2025-03-15
```

Use `available_at` or a carefully defined public-availability timestamp
to determine when a historical strategy could first have used the fact.

### What to borrow

-   SEC identifier mapping.
-   XBRL ingestion patterns.
-   Resumable bulk imports.
-   Import-run tracking.
-   Filing provenance.
-   SQL analysis patterns.

Do not copy its schema blindly. Map concepts into the existing database
contract and preserve source-specific facts.

### Benefits

-   More reliable fundamental screening.
-   Better filing traceability.
-   Correct historical feature availability.
-   Reduced look-ahead bias.
-   Easier reconciliation when filings are amended.

### Limitation

This primarily addresses SEC data. It does not solve Indian company
filings or normalize all global providers.

------------------------------------------------------------------------

## 3.9 Financial Data Warehouse --- raw, staging, and analytical layers

**Repository:**
[pspotq/financial-data-warehouse](https://github.com/pspotq/financial-data-warehouse)\
**Priority:** P2\
**Suggested action:** Study architecture patterns, not wholesale
adoption

### Problem it addresses

Different providers may represent the same concept differently. If
downstream applications need to understand every provider's format,
provider-specific logic spreads throughout the platform.

A layered data model separates source-specific records from standardized
data and consumer-facing analytics.

### Suggested logical layers

``` text
Provider data
     |
     v
Raw/source-specific
     |
     v
Staging/normalized
     |
     v
Intermediate calculations
     |
     v
Analytics and research marts
     |
     v
Dashboards / API / research
```

These can be implemented as schemas or logical model layers inside the
existing TimescaleDB deployment. A new warehouse database is not
required.

### Benefits

-   Reduces provider-specific logic in consumers.
-   Makes source reconciliation easier.
-   Improves transformation lineage.
-   Makes new providers easier to add.
-   Separates raw evidence from derived values.

### Recommendation

Use this as a design reference. Preserve the current database contract
and introduce layers incrementally.

------------------------------------------------------------------------

## 3.10 OpenTelemetry Collector --- unified telemetry

**Repository:**
[open-telemetry/opentelemetry-collector](https://github.com/open-telemetry/opentelemetry-collector)\
**Priority:** P3\
**Suggested fit:** Moderate now; higher as services grow

### Problem it addresses

A log line saying that a job took 40 seconds does not explain where the
time was spent.

Distributed traces and structured telemetry can break execution into
stages:

``` text
Ingestion run
   |
   +--> provider request
   |
   +--> response parsing
   |
   +--> validation
   |
   +--> database write
   |
   +--> completion
```

### Useful telemetry fields

-   Run ID.
-   Job and provider name.
-   Start and end timestamps.
-   Provider request latency.
-   Downloaded, validated, inserted, skipped, and rejected counts.
-   Database write latency.
-   Retry count.
-   Outcome and error category.
-   Application version and image digest.

### Benefits

-   Better diagnosis of slow jobs.
-   Easier correlation across services.
-   Standardized telemetry export.
-   More useful operational dashboards.

### Recommendation

Start with structured JSON logs and job-level Prometheus metrics. Add
OpenTelemetry when the platform has more interconnected applications,
APIs, and asynchronous workflows.

------------------------------------------------------------------------

## 3.11 Great Expectations --- alternative data-quality framework

**Repository:**
[great-expectations/great_expectations](https://github.com/great-expectations/great_expectations)\
**Priority:** Alternative to Soda

Great Expectations provides expectation-based data validation and
documentation workflows.

It can validate expectations such as:

-   A symbol field must not be null.
-   A price must be positive.
-   A dataset must contain records for the expected date range.
-   A natural key must be unique.
-   A business metric must fall within a justified range.

### Soda versus Great Expectations

  -----------------------------------------------------------------------
  Consideration           Soda                    Great Expectations
  ----------------------- ----------------------- -----------------------
  Declarative checks      Strong                  Supported

  SQL database validation Supported               Supported

  Python customization    Supported               Strong

  Validation              Supported               Strong
  documentation                                   

  Initial setup           Evaluate for simpler    Evaluate for richer
                          check workflows         validation workflows

  Best use here           Initial data-quality    Alternative if
                          pilot                   requirements outgrow
                                                  Soda
  -----------------------------------------------------------------------

**Recommendation:** Select one. Do not deploy both initially. Compare
current versions, licensing, connector compatibility, and operational
complexity in a small proof of concept.

------------------------------------------------------------------------

## 3.12 MLflow --- experiment and model tracking

**Repository:** [mlflow/mlflow](https://github.com/mlflow/mlflow)\
**Priority:** P1/P2 once research experiments begin\
**Suggested deployment:** RTX 3090 workstation or another research host

### Problem it addresses

As experiments multiply, notebooks and local files become difficult to
compare. You need to know which dataset, features, parameters, code
version, and evaluation procedure produced a result.

MLflow tracks experiments, parameters, metrics, artifacts, and model
information.

### Example experiment record

``` text
Experiment: Equity momentum prediction

Run:
  Model: LightGBM
  Feature set: momentum + quality + volatility
  Dataset version: 2026-11-15
  Training window: documented
  Validation window: documented
  Test window: documented

Metrics:
  Benchmark-relative return
  Sharpe ratio
  Maximum drawdown
  Turnover
  Hit rate

Artifacts:
  Model
  Feature importance
  Predictions
  Evaluation report
  Configuration
```

Values should be recorded from actual runs; the example is a structure,
not a performance claim.

### Benefits

-   Reproducible model experiments.
-   Easier comparison of feature sets and algorithms.
-   Retained evaluation artifacts.
-   Model and dataset lineage.
-   Better collaboration and review.

### Recommendation

Do not deploy MLflow just to track a handful of notebooks. Add it when
experiments become numerous enough that manual tracking is unreliable.
Keep it off the constrained k3s control plane.

------------------------------------------------------------------------

# 4. Recommended target architecture

The following architecture adds capabilities while preserving the
existing production foundation.

``` text
                   DATA PROVIDERS
        NSE | Yahoo | SEC | FRED | RSS | FX
                         |
                         v
                 EXISTING FETCHERS
                         |
                         v
                    k3s CronJobs
                         |
                         v
                     TimescaleDB
                         |
              +----------+-----------+
              |                      |
              v                      v
        Soda quality checks     PostgreSQL Exporter
              |                      |
        PASS / WARN / FAIL          Prometheus
              |                      |
              v                      v
       Validated datasets          Grafana
              |
              v
           dbt Core
              |
       +------+----------------+
       |                       |
       v                       v
 Analytical marts       Research datasets
       |                       |
       |                 +-----+------+
       |                 |            |
       |                 v            v
       |              VectorBT     ML4T Backtest
       |                 |            |
       |                 +-----+------+
       |                       |
       |                 MLflow tracking
       |                       |
       +-----------------------+
                               |
                               v
                     Research scorecards
                               |
                               v
                     Marketview / AI layer


TimescaleDB ---- pgBackRest ----> independent backup repositories
```

Dagster and OpenTelemetry are optional later additions, not
prerequisites for this design.

------------------------------------------------------------------------

# 5. Deployment boundaries for the existing hardware

The current infrastructure is resource-constrained and should not become
the default home for every new framework.

  -----------------------------------------------------------------------
  Component               Suggested location      Reason
  ----------------------- ----------------------- -----------------------
  Existing ingestion      Existing k3s workers    Preserve current
  CronJobs                                        deployment

  TimescaleDB             Existing Jetson TX2     Preserve source of
                          placement               truth

  Soda checks             Capable worker or       Avoid unnecessary TX2
                          workstation initially   load

  PostgreSQL Exporter     Near database, if       Low-overhead metrics
                          resource testing        collection
                          permits                 

  Prometheus/Grafana      Existing monitoring     Reuse current
                          placement               observability

  Backup repository       Pi 3 SSD plus           Separate failure
                          independent copy        domains

  dbt                     Workstation or          Keep transformations
                          dedicated analytics job separate initially

  VectorBT                RTX 3090 workstation    Research compute

  ML4T Backtest           RTX 3090 workstation    Research compute

  MLflow                  Workstation             Experiment tracking

  Dagster                 Optional separate host  Avoid extra production
                                                  orchestration

  OpenTelemetry Collector Later, based on service Add only when needed
                          count                   
  -----------------------------------------------------------------------

### Network and security considerations

-   Preserve the existing Tailscale-only access model.
-   Do not expose TimescaleDB publicly.
-   Use separate database roles for ingestion, analytics, monitoring,
    and read-only consumers.
-   Store credentials in Secrets or a suitable secret manager.
-   Keep database migration privileges separate from normal pipeline
    writes.
-   Validate host firewall rules and actual network enforcement; do not
    assume a Kubernetes NetworkPolicy is enforced unless the CNI/kernel
    supports it.
-   Keep backup credentials separate from application credentials.

------------------------------------------------------------------------

# 6. Implementation roadmap

The sequence below is a suggested plan, not a commitment to deploy every
project. Complete each phase only after its acceptance criteria are met.

## Phase 0 --- Baseline and safety

**Estimated effort:** 1--3 days

1.  Record current CronJob success/failure state.
2.  Record database size, table sizes, disk free space, and backup age.
3.  Export current database schema and migration version.
4.  Record current image tags and digests.
5.  Verify existing backup files and retention.
6.  Create a separate branch for new work.
7.  Define a rollback plan for each production-facing change.

**Deliverable:** A known-good baseline and isolated integration
workspace.

## Phase 1 --- Observability and data quality

**Estimated effort:** 1--2 weeks

1.  Deploy PostgreSQL Exporter with a dedicated monitoring role.
2.  Add database panels to Grafana.
3.  Add alerts for database availability, disk pressure, and connection
    pressure.
4.  Pilot Soda on one dataset, preferably daily prices.
5.  Add natural-key duplicate and null checks.
6.  Add source freshness and expected-symbol coverage checks.
7.  Persist validation outcomes.
8.  Test warning and critical failure behavior.

**Deliverable:** Visibility into both database health and data
correctness.

## Phase 2 --- Backup and recovery verification

**Estimated effort:** 1--2 weeks, depending on restore testing

1.  Document the existing backup and restore procedure.
2.  Build an isolated TimescaleDB restore target.
3.  Evaluate pgBackRest against the exact production versions.
4.  Test a full restore.
5.  Test WAL/PITR if selected.
6.  Verify hypertables, extensions, and representative data.
7.  Add an independent backup copy.
8.  Document recovery time and recovery point.

**Deliverable:** A demonstrated recovery process, not just scheduled
backup files.

## Phase 3 --- Analytical SQL organization

**Estimated effort:** 1--2 weeks

1.  Create a separate dbt project.
2.  Connect it to a non-production schema or isolated database.
3.  Build a small staging model for daily prices.
4.  Build daily returns.
5.  Add tests and documentation.
6.  Compare outputs with existing production calculations.
7.  Benchmark query plans and execution time.
8.  Expand to sector and factor models only after equivalence is
    demonstrated.

**Deliverable:** A tested, documented analytical transformation layer.

## Phase 4 --- Quantitative research prototype

**Estimated effort:** 2--4 weeks

1.  Create a separate research environment on the workstation.
2.  Define a reproducible TimescaleDB extraction interface.
3.  Build a point-in-time dataset with historical universe membership.
4.  Implement RSI mean reversion.
5.  Implement moving-average momentum.
6.  Add costs, slippage, and execution timing.
7.  Compare with a benchmark.
8.  Use out-of-sample and walk-forward evaluation.
9.  Record configurations and outputs.
10. Add MLflow when experiment volume justifies it.

**Deliverable:** Reproducible strategy research based on the existing
data platform.

## Phase 5 --- Advanced orchestration and telemetry

**Estimated effort:** As justified by future complexity

1.  Study Dagster asset lineage using a small research workflow.
2.  Compare its operational overhead with the existing CronJobs.
3.  Add OpenTelemetry only where cross-service tracing provides
    meaningful value.
4.  Keep existing production scheduling unless a concrete limitation is
    demonstrated.

**Deliverable:** A decision on whether more orchestration or telemetry
infrastructure is actually necessary.

------------------------------------------------------------------------

# 7. Risks and mitigations

  -----------------------------------------------------------------------
  Risk                    Potential impact        Mitigation
  ----------------------- ----------------------- -----------------------
  Too many frameworks     More services and       Adopt one capability at
                          maintenance             a time

  Dependency conflicts    Broken existing         Separate environments
                          workloads               and pin versions

  ARM64 incompatibility   Deployment failure      Test image/platform
                                                  support before rollout

  Excessive database      TX2 resource pressure   Bounded queries,
  queries                                         indexes, rate limits

  False-positive data     Valid data blocked      Severity levels and
  checks                                          source-aware thresholds

  False confidence in     Bad investment          Point-in-time data,
  backtests               conclusions             costs, out-of-sample
                                                  testing

  Schema ownership        Migration failures      Keep existing migration
  conflicts                                       system authoritative

  Backup tool             Failed recovery         Restore-test exact
  incompatibility                                 TimescaleDB version

  Credential              Larger security impact  Dedicated
  over-privilege                                  least-privilege roles

  New orchestration       Fragile cluster         Keep Dagster optional
  overhead                                        and separate
  -----------------------------------------------------------------------

------------------------------------------------------------------------

# 8. Suggested project-level acceptance checklist

Use this checklist before calling an integration complete.

## Data quality

-   [ ] Empty ingestion is detected.
-   [ ] Duplicate natural keys are detected.
-   [ ] Missing expected symbols are detected.
-   [ ] Stale data is detected using an exchange-aware calendar.
-   [ ] Financial rules are source- and asset-aware.
-   [ ] Validation results are persisted.
-   [ ] Critical failures block only affected downstream processing.

## Database observability

-   [ ] Exporter uses a dedicated role.
-   [ ] Prometheus scrapes successfully.
-   [ ] Grafana displays connections and database size.
-   [ ] Disk capacity alerts are configured.
-   [ ] Vacuum/dead-tuple visibility is available.
-   [ ] Exporter overhead is measured.

## Backup and recovery

-   [ ] Restore to an isolated instance succeeds.
-   [ ] TimescaleDB extension state is verified.
-   [ ] Backup copy is independent of TX2.
-   [ ] WAL archive completeness is checked if PITR is used.
-   [ ] Recovery time is measured.
-   [ ] Recovery procedure is documented.

## dbt

-   [ ] Project connects to a non-production schema.
-   [ ] Staging models build.
-   [ ] Tests detect intentionally invalid records.
-   [ ] Model documentation is generated.
-   [ ] Existing database contract remains intact.
-   [ ] Query performance is measured.

## Quantitative research

-   [ ] Historical universe membership is point-in-time correct.
-   [ ] Corporate actions are handled consistently.
-   [ ] Signal timing is explicit.
-   [ ] Transaction costs and slippage are included.
-   [ ] Benchmark comparison is included.
-   [ ] Out-of-sample evaluation is performed.
-   [ ] Experiment configuration and results are retained.
-   [ ] No strategy is promoted based only on in-sample performance.

------------------------------------------------------------------------

# 9. Final recommendations

If only five improvements are funded, prioritize these:

  ------------------------------------------------------------------------
                          Rank Improvement           Why
  ---------------------------- --------------------- ---------------------
                             1 PostgreSQL Exporter   Immediate visibility
                                                     into the central
                                                     database

                             2 Soda Core             Detect incorrect or
                                                     incomplete financial
                                                     data

                             3 Restore-tested backup Protect the
                               strategy              platform's most
                                                     valuable asset

                             4 dbt Core              Make analytical
                                                     transformations
                                                     maintainable

                             5 VectorBT              Turn historical data
                                                     into systematic
                                                     research
  ------------------------------------------------------------------------

Then add MLflow and an execution-aware backtesting engine as the
research workflow matures.

### What not to do yet

-   Do not migrate all 36 CronJobs to Dagster.
-   Do not introduce a second database warehouse.
-   Do not deploy both Soda and Great Expectations.
-   Do not deploy multiple backtesting frameworks before defining a
    common dataset and strategy.
-   Do not run research optimization workloads on the Pi control plane.
-   Do not replace the existing migration or database-contract process.
-   Do not interpret a successful backtest as evidence of future
    profitability.

### The desired end state

The project should evolve from a market-data collection and analytics
platform into a **reliable, point-in-time-correct quantitative research
platform**.

The progression is:

``` text
Reliable ingestion
       |
       v
Validated financial data
       |
       v
Traceable analytical transformations
       |
       v
Point-in-time research datasets
       |
       v
Reproducible backtesting
       |
       v
Experiment tracking and evaluation
       |
       v
AI-assisted financial research
```

The most important investment is not adding more frameworks. It is
making the existing data trustworthy, historically correct, recoverable,
and useful for reproducible research.

------------------------------------------------------------------------

## 10. Repository references

All links below point to the upstream projects or documentation. Check
current release status, licensing, supported Python/PostgreSQL versions,
and ARM64 availability before adopting.

### Data quality and transformations

-   [Soda Core](https://github.com/sodadata/soda-core)
-   [Great
    Expectations](https://github.com/great-expectations/great_expectations)
-   [dbt Core](https://github.com/dbt-labs/dbt-core)

### Orchestration and observability

-   [Dagster](https://github.com/dagster-io/dagster)
-   [Prometheus PostgreSQL
    Exporter](https://github.com/prometheus-community/postgres_exporter)
-   [OpenTelemetry
    Collector](https://github.com/open-telemetry/opentelemetry-collector)

### Database recovery

-   [pgBackRest](https://github.com/pgbackrest/pgbackrest)
-   [pgBackRest documentation](https://pgbackrest.org/)

### Quantitative research

-   [VectorBT](https://github.com/polakowo/vectorbt)
-   [ML4T Backtest](https://github.com/ml4t/backtest)
-   [Zipline
    Reloaded](https://github.com/stefan-jansen/zipline-reloaded)
-   [MLflow](https://github.com/mlflow/mlflow)

### Financial data engineering

-   [Financial-DataBase](https://github.com/JdeJusto/Financial-DataBase)
-   [Financial Data
    Warehouse](https://github.com/pspotq/financial-data-warehouse)

### Supporting documentation

-   [VectorBT documentation](https://vectorbt.dev/)
-   [OpenTelemetry Collector
    documentation](https://opentelemetry.io/docs/collector/)
-   [Prometheus PostgreSQL Exporter
    documentation](https://github.com/prometheus-community/postgres_exporter/blob/master/docs/README.md)

------------------------------------------------------------------------

**Document status:** Architecture recommendations and proposed roadmap.
No production changes are implied or authorized by this document.
