# TTS service — Piper HTTP server for Orin Nano (JetPack 7 / CUDA 13)
# Build on Orin Nano (ARM64). CUDA libraries mounted at runtime from host.
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Copy application
COPY tts_server.py .

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:8001/healthz || exit 1

CMD ["python3", "tts_server.py"]