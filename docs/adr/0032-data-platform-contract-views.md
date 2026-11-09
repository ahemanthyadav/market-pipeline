# 0032. Data-platform contract: views as the interface; no separate DB repo

- Date: 2026-10-27
- Status: accepted (schema key 22 + schema_migrations live; telegram v31 and marketview v12 are the first view consumers)

## Context
The owner asked whether the database is modular enough for the growing set of
applications and whether it needs its own repo. Evidence of the real problem:
adding two valuation methods (ADR 0030) required coordinated edits in five
consumers because they read base-table column layouts directly.

## Decision
1. **No separate database repo.** Schema keys ship in the same commit as the
   writer that fills them; the enforcement tooling (workloads check, schema
   jobs, safeguards tests, pre-commit guards) is marketdata-native; ADR 0027
   already established marketdata as the platform repo that sibling apps
   consume. A split would add lock-step cross-repo releases for zero
   mechanical benefit at single-operator scale.
2. **`v_*` contract views are the published interface** (schema key 22):
   columns append-only, rule-9 bounds inside the view, shared computed columns
   (e.g. `methods_above_close`) defined once. Base tables are private to the
   pipeline. Breaking a view needs an ADR + coordinated release.
3. **`app` schema for new app-state tables** (watchlist-like); existing
   tables grandfathered in `public` — no cosmetic migrations.
4. **`schema_migrations` bookkeeping** in the schema job: every key recorded
   with md5 and timestamps; an old key changing md5 after first application
   prints a WARN (add a new key instead of editing history). Keys remain
   idempotent and reapplied wholesale — the bookkeeping makes drift visible,
   it does not gate.
5. **AR-8 stays the top modularity gap** (writers run as superuser) and stays
   owner-run; everything needed is staged in pending-tasks §1.

## Consequences
- The ADR 0030 five-place edit becomes a one-place edit next time (the view).
- marketview's SQL lint now recognises `CONTRACT_VIEWS` as pre-bounded; a view
  must never appear in PLAIN_TABLES (tested).
- Remaining base-table consumers (stock_report, Grafana panels, marketview's
  older pages) migrate opportunistically at their next image cycles — tracked
  in `docs/database-contract.md` §1.
