# Consolidated light-feeder image (AR-5 step 3).
# One image, many entrypoints — the job is chosen by `command:` in the CronJob.
# Profile: psycopg + requests + feedparser + lxml (light DB/HTTP fetchers, no pandas).
#
# Build natively on the TX2 (no registry, imagePullPolicy: Never):
#   docker build -t local/marketdata-light:vN .   # N = the tag in the CronJob manifests
#   docker save local/marketdata-light:vN | gzip > marketdata-light-vN.tar.gz
#   k3s ctr -n k8s.io images import marketdata-light-vN.tar.gz  # pi1, pi2, pi3 + orin2 (see images/data/Dockerfile)
#
# CronJobs using this image pass their script via `command:`:
#   command: ["python", "pipeline-health.py"]
FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY headline_ner.py pipeline-health.py fdi-watch.py fiidii-fetcher.py \
     news-fetcher.py rbi.py factors-fetcher.py research-fetcher.py mdjob.py ./
CMD ["python", "pipeline-health.py"]
