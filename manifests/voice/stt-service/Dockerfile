# STT service — faster-whisper HTTP server for Orin Nano (CPU int8 by default)
# Build ON an Orin (arm64; the TX2 cannot build this), no registry (ADR 0004):
#   sudo docker build -t local/stt-service:vN .          # N = the tag in stt-service-deployment.yaml
#   sudo docker save local/stt-service:vN | sudo k3s ctr -n k8s.io images import -
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pinned: the 2026-11-17 build installed whatever was newest and ignored this file.
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY stt_server.py .

EXPOSE 8000

# Health is checked by the kubelet probes in the Deployment (no curl in this image).
CMD ["python3", "stt_server.py"]
