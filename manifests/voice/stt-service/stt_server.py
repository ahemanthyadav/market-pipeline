# STT service — faster-whisper HTTP server (runs on Orin Nano)

# Run with:  python stt_server.py

# Endpoints:
#   GET  /healthz   -> {"status": "ok"}
#   POST /transcribe -> multipart form field "audio" (ogg/wav/mp3)
#                       -> {"text": "transcribed text"}

import os
import sys
import tempfile
from fastapi import FastAPI, File, UploadFile, HTTPException
from faster_whisper import WhisperModel
import uvicorn

app = FastAPI(title="stt-service", version="1.0.0")

MODEL_SIZE = os.environ.get("WHISPER_MODEL", "tiny")
DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")   # cuda on Orin Nano
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "float16")

model = None

@app.on_event("startup")
def load_model():
    global model
    model = WhisperModel(
        MODEL_SIZE,
        device=DEVICE,
        compute_type=COMPUTE_TYPE,
        download_root="/root/.cache/faster-whisper",
    )

@app.get("/healthz")
def healthz():
    return {"status": "ok", "model": MODEL_SIZE, "device": DEVICE}

@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    if model is None:
        raise HTTPException(status_code=503, detail="model not loaded")
    # Save upload to temp file (ffmpeg-compatible)
    suffix = os.path.splitext(audio.filename or ".ogg")[1] or ".ogg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await audio.read())
        tmp_path = tmp.name
    try:
        segments, info = model.transcribe(tmp_path, beam_size=5)
        text = " ".join(seg.text for seg in segments).strip()
        return {"text": text, "language": info.language, "duration": info.duration}
    finally:
        os.unlink(tmp_path)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
