# Database contract — the data platform's published interface (ADR 0032)

> One TimescaleDB instance (`timescaledb-0`, TX2) serves every application:
> the ~33 pipeline CronJobs, Grafana, the Telegram bot, marketview, and the
> llm-gateway. **marketdata is the platform repo**: it owns the schema, the
> roles and this contract; sibling repos consume under these rules
> (ADR 0027 precedent, ADR 0032 decision — deliberately NOT a separate repo:
> schema keys ship in the same commit as the writer that fills them).
> Last verified: 2026-11-15 (role matrix checked live; schema re-apply tested twice on a scratch DB).

## 1. The interface: `v_*` views, not base tables

Applications read the **contract views** (schema key 22). Base tables are
private to the pipeline and may change shape; views only ever gain columns.

| View | Serves | Guarantees |
|---|---|---|
| `v_universe` | active constituents, all five indexes | one row per (universe, symbol) |
| `v_latest_prices` | latest close/adj_close per symbol | 7-day freshness bound inside |
| `v_latest_scores` | latest screener row per symbol | 14-day bound; rank is within-universe |
| `v_valuations_recent` | last 4 days of valuation rows | **`methods_above_close` computed here** — all 15 valuation method columns + Practitioner/Academic medians |
| `v_latest_valuations` | latest valuation per symbol | all 15 method columns (`10 core + 5 extended`), Practitioner/Academic lens medians, panel assumptions columns |
| `v_valuation_methods` | catalog of all valuation & scientific metrics | `method_key`, `method_name`, `category`, `lens` (`Practitioner` / `Research-Backed`), `sub_school`, `source_ref`, `formula`, `target_table` (key 25) |
| `v_latest_scientific_metrics` | latest per-symbol scientific accounting diagnostics | 14-day bound; `gpa`, `operating_accruals`, `asset_growth_cma`, `net_share_issuance`, `piotroski_f_score`, `altman_z2`, dual archetypes (key 25) |
| `v_sector_latest` | latest sector aggregates + style lens | 7-day bound; medians need ≥ 5 inputs (else NULL); includes Practitioner & Academic sector median upsides |
| `v_upcoming_earnings` | next 30 days of earnings dates | one row per symbol (earliest date) |

Rules for views: columns are APPEND-ONLY; the rule-9 time bound lives inside
the view; a view rename or column removal is a breaking change needing an ADR
and a coordinated consumer release. Consumers still migrating (tracked, moved
at each one's next natural image cycle): stock_report.py, Grafana panel SQL,
marketview's screener/sectors/markets/multibagger/strategy queries.

## 2. Schema change process

- Additive, idempotent, numbered keys in `manifests/timescaledb/schema-configmap.yaml`,
  applied by a RENAMED one-shot job (`schema-<topic>-<n>`); never psql ad hoc.
- The job records every applied key in **`schema_migrations`** (filename, md5,
  first/last applied) and WARNs when an old key's md5 changed after first
  application — editing history is a smell; add a new key instead.
- **Every key is re-applied on every run**, so an older key must never undo
  a later one. If a later key widens a view, the earlier definition is guarded
  to bootstrap-only (`IF to_regclass(...) IS NULL`) — a new key cannot help,
  because the earlier key fails before it runs. A role must be created in a
  key at or before the first key that GRANTs to it. Both are enforced by
  `tests/test_manifest_safeguards.py` (`SchemaReapplySafety`). The one
  sanctioned history edit so far: 2026-11-15, keys 10 (+`pipeline_rw`) and 22
  (view guards) — expect the md5 WARN for those two once.
- A failed key fails the Job (`SCHEMA-FAIL <file>`); before 2026-11-15 the
  loop carried on and printed `SCHEMA-OK` over a half-applied key.
- **New app-state tables go in the `app` schema** (created in key 22, USAGE
  granted to the reader/app roles, default SELECT in place). Existing tables
  are grandfathered in `public` — do not migrate them for cosmetics.

## 3. Role matrix

| Role | May | Used by |
|---|---|---|
| `postgres` | everything (superuser) | schema-job, pg-backup, restore-drill, the StatefulSet |
| `grafana_ro` | SELECT everywhere (default privileges) | Grafana, marketview |
| `telegram_app` | SELECT + writes ONLY on `watchlist`, `bot_users` | Telegram bot |
| `litellm` | its own gateway state | llm-gateway |
| `pipeline_rw` | SELECT/INSERT/UPDATE/DELETE on data tables + sequences, **no DDL/roles** | all pipeline CronJobs (AR-8, live since 2026-10-30) |

All roles are created NOLOGIN in schema key 10; LOGIN + password are set
out-of-band from their Secrets (`pipeline-db-rw` for `pipeline_rw`).

**Drift fixed 2026-11-15:** `pipeline_rw` had come to own `stock_valuations`,
`stock_scientific_metrics` and `sector_metrics` and to hold CREATE on `public`,
leftovers of `valuation.py` running DDL at runtime. That DDL was removed in
`marketdata-data:v9`. Ownership was moved back to `postgres` and CREATE was
revoked; the DML grants were kept. Valuation was re-verified working as DML-only.

## 4. Invariants every consumer may rely on

- Writers DELETE+INSERT only their own `(key, ts)` slice (rule 4) — reads never
  see partial foreign rewrites inside a transaction.
- `stock_prices.ts` = 00:00 UTC of the trade date (ADR 0013); `adj_close` is
  our own total-return series, never Yahoo's.
- Valuations are descriptive (ADR 0026) — never inside scores/composite.
- Hypertables require `ts >=` bounds (rule 9) — satisfied inside the views.
- One writer per table; the owner map is the workload tables in the
  architecture doc (enforced by `workloads.py --check`).
