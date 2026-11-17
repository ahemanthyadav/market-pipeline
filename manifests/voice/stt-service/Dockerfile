# STT service — faster-whisper HTTP server for Orin Nano (JetPack 7 / CUDA 13)
# Build on Orin Nano (ARM64). CUDA libraries mounted at runtime from host.
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies including python3-av (avoids PyAV compilation)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    ca-certificates \
    python3-av \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (excluding av which is from apt)
RUN pip3 install --no-cache-dir --break-system-packages \
    faster-whisper \
    fastapi \
    uvicorn \
    python-multipart

# Copy application
COPY stt_server.py .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:8000/healthz || exit 1

CMD ["python3", "stt_server.py"]