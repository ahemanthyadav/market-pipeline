# Skill: data-backfill-strategy

Force backfill of all fetcher CronJobs with rate-limit-aware ordering to avoid hitting yfinance/NSE
per-IP limits. Use after image recovery, cluster outage, or extended data staleness.

## Prerequisites

- `KUBECONFIG=~/.kube/pi-cluster.yaml` exported
- Images present on pi1/pi3 (or orin2 uncordoned with images) — see `image-recovery` skill
- pi2 cordoned (fragile control plane — do not run imports/jobs there)

## Backfill order (rate-limit risk → gap)

Run each as `kubectl create job --from=cronjob/<name>` → poll → check logs → delete. One at a time.

| # | Job | Risk | Gap after |
|---|-----|------|-----------|
| 1 | fx-fetcher | low | none |
| 2 | yields-fetcher | low | none |
| 3 | rates-fetcher | low | none |
| 4 | rbi-fetcher | low | none |
| 5 | nse-fetcher | medium | 2 min |
| 6 | stock-prices | high | 5 min |
| 7 | yfinance-fetcher | high | 5 min |
| 8 | news-fetcher | low | none |
| 9 | sentiment-scorer | medium (HF cold start) | none |
| 10 | ta-compute | none | none |
| 11 | stock-metrics | none | none |
| 12 | stock-fundamentals | **highest** (2251 symbols) | 10 min + monitor |
| 13 | stock-screener | none | none |
| 14 | stock-report | none | none |
| 15 | stock-valuation | none | none |

## Example run (single job)

```bash
kubectl create job -n market-data backfill-stock-prices --from=cronjob/stock-prices
kubectl wait --for=condition=complete job/backfill-stock-prices -n market-data --timeout=600s
kubectl logs -n market-data -l job-name=backfill-stock-prices --tail=10
kubectl delete job backfill-stock-prices -n market-data
```

## Rate-limit handling

**NSE "unavailable" on every filing** (as happened 2026-11-08):
- Cancel: `kubectl delete job backfill-nse-fund -n market-data`
- Wait 15–30 min, retry single-symbol manual run to confirm limit lifted
- Fetcher keeps a cursor; re-runs resume (no duplicates)

**stock-fundamentals times out** (>10 min, no progress):
- 2251 symbols sequential on pi3 (1 GB RAM) — let it run, it's resumable
- Do NOT launch another fundamentals Job in parallel (doubles yfinance calls)

## Skip list (safe to defer)

- `nse-fundamentals` (suspended by design until DG-03 backfill completes)
- `earnings-calendar` (weekly Sun 12:00)
- `ml1-anomalies`, `ml3-volatility` (daily; skip if resource-constrained, retry next day)

## Verify after backfill

```bash
PW=$(kubectl -n market-data get secret timescaledb-auth -o jsonpath='{.data.password}' | base64 -d)
kubectl -n market-data exec timescaledb-0 -- env PGPASSWORD="$PW" \
  psql -U postgres -d postgres -At -c "SELECT feed, last_check, fresh FROM pipeline_heartbeat ORDER BY feed;"
```
All feeds should show `fresh=true` with `last_check` within 24 h.

## Related

- ops manual §6b (backfill rate-limit strategy table)
- pending-tasks (Universe expansion item — backfill addition)
- `image-recovery` skill (prerequisite: images must be present first)