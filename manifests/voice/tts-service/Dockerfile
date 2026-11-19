# TTS service — Piper HTTP server for Orin Nano (CPU inference via onnxruntime)
# Build ON an Orin (arm64; the TX2 cannot build this), no registry (ADR 0004):
#   sudo docker build -t local/tts-service:vN .          # N = the tag in tts-service-deployment.yaml
#   sudo docker save local/tts-service:vN | sudo k3s ctr -n k8s.io images import -
# The voice (.onnx + .onnx.json) is NOT in the image: it is a hostPath on the node,
# /root/.local/share/piper/voices (see the deployment manifest).
FROM python:3.12-slim

WORKDIR /app

# ffmpeg converts Piper's WAV to the OGG/Opus Telegram voice messages need
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY tts_server.py .

EXPOSE 8001

# Health is checked by the kubelet probes in the Deployment (no curl in this image).
CMD ["python3", "tts_server.py"]
