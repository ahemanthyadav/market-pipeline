# Consolidated ML-feeder image (AR-5 step 3).
# TX2 only — FinBERT sentiment scoring via CPU torch (JetPack CUDA-10.2 era,
# python:3.11 container with PyTorch CPU aarch64 wheels).
#
# Build natively on the TX2 (no registry, imagePullPolicy: Never):
#   docker build -t local/marketdata-ml:v1 .
#   docker save local/marketdata-ml:v1 | gzip > marketdata-ml-v1.tar.gz
#   k3s ctr -n k8s.io images import marketdata-ml-v1.tar.gz  # TX2 only
#
# CronJob using this image passes its script via `command:`:
#   command: ["python", "sentiment-score.py"]
FROM python:3.11-slim
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock && \
    pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch && \
    pip install --no-cache-dir transformers tokenizers safetensors huggingface-hub && \
    python -c "from transformers import AutoTokenizer, AutoModelForSequenceClassification; \
    AutoTokenizer.from_pretrained('ProsusAI/finbert'); \
    AutoModelForSequenceClassification.from_pretrained('ProsusAI/finbert')"
COPY sentiment-score.py ./
CMD ["python", "sentiment-score.py"]
