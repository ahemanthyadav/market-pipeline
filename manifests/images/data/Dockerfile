# Consolidated data-feeder image (AR-5 step 3).
# One image, many entrypoints — the job is chosen by `command:` in the CronJob.
# Profile: pandas + yfinance + psycopg + fredapi (price/fundamental/technical fetchers).
# The screener already proved this pattern (one image, 12+ CronJobs via `command:`).
#
# Build natively on the TX2 (no registry, imagePullPolicy: Never):
#   docker build -t local/marketdata-data:vN .     # N = the tag in the CronJob manifests
#   docker save local/marketdata-data:vN | gzip > marketdata-data-vN.tar.gz
#   k3s ctr -n k8s.io images import marketdata-data-vN.tar.gz  # on every node that runs floating jobs:
#                                                              # pi1, pi3, orin2 (pi2 is cordoned)
#
# CronJobs using this image pass their script via `command:`:
#   command: ["python", "fx-fetcher.py"]
FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY fx-fetcher.py intraday-fetcher.py nse-indices.py rates-fetcher.py \
     ta-compute.py yfinance-fetcher.py yields-fetcher.py \
     universe_refresh.py stock_prices.py stock_fundamentals.py stock_metrics.py \
     stock_screener.py stock_report.py valuation.py predictions.py \
     earnings_calendar.py accuracy_check.py membership_history.py nse_fundamentals.py \
     corporate_actions_backfill.py history_backfill.py fx-backfill.py mdjob.py \
     ml1.py ml3.py ml3_volatility.py pgcompat.py sessions.py ./
CMD ["python", "stock_prices.py"]
