#!/bin/bash
# apply-llm.sh — ship narrative.py, ml7b_per_symbol.py, bakeoff.py, factcheck.py, news_embed.py as
# ConfigMap llm-narrative-script and apply the llm-narrative + news-embed + ml7b-per-symbol CronJobs
# (ML-7a, ML-2, ML-7b); shared factcheck.py (from manifests/telegram/) rides in the same ConfigMap.
# Run after any change to either. Tests: tests/test_narrative.py.
set -euo pipefail
cd "$(dirname "$0")/.."
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/pi-cluster.yaml}"
python3 tests/test_narrative.py >/dev/null 2>&1 || { echo "tests/test_narrative.py fails" >&2; exit 1; }
kubectl -n market-data create configmap llm-narrative-script --from-file=narrative.py=manifests/llm/narrative.py \
  --from-file=ml7b_per_symbol.py=manifests/llm/ml7b_per_symbol.py \
  --from-file=bakeoff.py=manifests/llm/bakeoff.py \
  --from-file=factcheck.py=manifests/telegram/factcheck.py \
  --from-file=news_embed.py=manifests/llm/news_embed.py \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f manifests/llm/cronjob.yaml
kubectl apply -f manifests/llm/ml7b-cronjob.yaml
