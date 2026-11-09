# ARM64 telegram image — one image, three entrypoints (ADR 0009 family):
#   CronJob telegram-digest → CMD below (digest.py)
#   CronJob telegram-alerts → args: ["python", "alerts.py"] (daily change alerts)
#   Deployment telegram-bot  → args: ["python", "bot.py"] (two-way listener)
# stocks.py is the shared stock/watchlist/screener view layer (no entrypoint); users.py is the
# multi-user layer (owner + bot_users members, per-user fan-out — ADR 0012).
# Same build/distribute pattern (native TX2 docker build, tarball import —
# pinned node pi1 only, see cronjob.yaml/deployment.yaml):
#   docker build -t local/telegram-digest:v37 .
#   docker save local/telegram-digest:v37 | gzip > telegram-digest-v37.tar.gz
FROM python:3.12-slim
WORKDIR /app
# requirements.lock (AR-5): installed from the lock since v9 (2026-10-18).
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY digest.py bot.py alerts.py stocks.py users.py resolver.py factcheck.py ask.py router.py nicknames.py ./
CMD ["python", "digest.py"]
