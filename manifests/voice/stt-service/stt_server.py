# STT service — faster-whisper HTTP server (runs on Orin Nano)

# Run with:  python stt_server.py

# Endpoints:
#   GET  /healthz   -> {"status": "ok"}                      (no auth: kubelet probes)
#   POST /transcribe -> multipart form field "audio" (ogg/wav/mp3)
#                       -> {"text": "transcribed text"}
#
# Auth: when VOICE_API_TOKEN is set, /transcribe requires `Authorization: Bearer <token>`.
# Unset = open inside the cluster (ClusterIP only). Limits: MAX_AUDIO_BYTES per upload
# (default 20 MB = the largest file a Telegram bot can download).

import hmac
import os
import tempfile

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from faster_whisper import WhisperModel

app = FastAPI(title="stt-service", version="2.0.0")

MODEL_SIZE = os.environ.get("WHISPER_MODEL", "tiny")
DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")   # cuda on Orin Nano
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "float16")
API_TOKEN = os.environ.get("VOICE_API_TOKEN", "")
MAX_AUDIO_BYTES = int(os.environ.get("MAX_AUDIO_BYTES", str(20 * 1024 * 1024)))
CHUNK = 1024 * 1024

model = None


def authorized(header, token=None):
    """Pure. True when no token is configured, or the bearer token matches."""
    token = API_TOKEN if token is None else token
    if not token:
        return True
    scheme, _, given = (header or "").partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(given.strip(), token)


def safe_suffix(filename):
    """Pure. A short alphanumeric extension for the temp file (never user path text)."""
    ext = os.path.splitext(filename or "")[1].lower()
    return ext if ext[1:].isalnum() and 1 < len(ext) <= 6 else ".ogg"


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
async def transcribe(request: Request, audio: UploadFile = File(...)):
    if not authorized(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="unauthorized")
    if model is None:
        raise HTTPException(status_code=503, detail="model not loaded")
    # Save upload to temp file (ffmpeg-compatible), refusing anything over the cap
    size = 0
    with tempfile.NamedTemporaryFile(suffix=safe_suffix(audio.filename), delete=False) as tmp:
        tmp_path = tmp.name
        while True:
            chunk = await audio.read(CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_AUDIO_BYTES:
                break
            tmp.write(chunk)
    try:
        if size > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413,
                                detail=f"audio larger than {MAX_AUDIO_BYTES} bytes")
        if size == 0:
            raise HTTPException(status_code=400, detail="empty audio")
        segments, info = model.transcribe(tmp_path, beam_size=5)
        text = " ".join(seg.text for seg in segments).strip()
        return {"text": text, "language": info.language, "duration": info.duration}
    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
