# Consolidated ML-feeder image (AR-5 step 3).
# TX2 only — FinBERT sentiment scoring via CPU torch (JetPack CUDA-10.2 era,
# python:3.11 container with PyTorch CPU aarch64 wheels).
#
# Build natively on the TX2 (no registry, imagePullPolicy: Never):
#   docker build -t local/marketdata-ml:vN .      # N = the tag in manifests/sentiment/cronjob.yaml
#   docker save local/marketdata-ml:vN | gzip > marketdata-ml-vN.tar.gz
#   k3s ctr -n k8s.io images import marketdata-ml-vN.tar.gz  # TX2 only
#
# CronJob using this image passes its script via `command:`:
#   command: ["python", "sentiment-score.py"]
FROM python:3.11-slim
WORKDIR /app
COPY requirements.lock ./
# Everything is pinned (versions read from the live v1 image 2026-11-19): torch from
# the PyTorch CPU index, the rest from requirements.lock, and FinBERT at one commit —
# the SAME revision sentiment-score.py loads (MODEL_REVISION), so a rebuild scores
# headlines with the same weights as before.
ARG TORCH_VERSION=2.15.0
ARG FINBERT_REVISION=0bc2ac10dda6929bcb1f77ccc43c7ec5c424f031
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==${TORCH_VERSION} && \
    pip install --no-cache-dir -r requirements.lock && \
    python -c "from transformers import AutoTokenizer, AutoModelForSequenceClassification; \
    AutoTokenizer.from_pretrained('ProsusAI/finbert', revision='${FINBERT_REVISION}'); \
    AutoModelForSequenceClassification.from_pretrained('ProsusAI/finbert', revision='${FINBERT_REVISION}')"
COPY sentiment-score.py ./
CMD ["python", "sentiment-score.py"]
