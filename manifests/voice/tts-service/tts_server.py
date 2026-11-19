# TTS service — Piper HTTP server (runs on Orin Nano)

# Run with:  python tts_server.py

# Endpoints:
#   GET  /healthz   -> {"status": "ok"}                      (no auth: kubelet probes)
#   POST /speak     -> JSON {"text": "..."} -> OGG/Opus audio (audio/ogg), the format
#                      Telegram voice messages need; WAV (audio/wav) if ffmpeg fails.
#
# Auth: when VOICE_API_TOKEN is set, /speak requires `Authorization: Bearer <token>`.
# Unset = open inside the cluster (ClusterIP only). Limits: MAX_TEXT_CHARS per request.
#
# Piper API (piper-tts 1.8): PiperVoice.load(model) reads <model>.json beside the
# .onnx file, and synthesize_wav(text, wave_writer) writes the audio. The 2026-11-17
# build called PiperVoice(model) / text_to_wav(), neither of which exists — the pod
# crash-looped at startup.

import hmac
import io
import os
import subprocess
import wave

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from piper import PiperVoice

app = FastAPI(title="tts-service", version="2.0.0")

VOICE_MODEL = os.environ.get("PIPER_MODEL", "en_US-lessac-medium.onnx")
VOICE_DIR = os.environ.get("PIPER_VOICE_DIR", "/root/.local/share/piper/voices")
API_TOKEN = os.environ.get("VOICE_API_TOKEN", "")
MAX_TEXT_CHARS = int(os.environ.get("MAX_TEXT_CHARS", "2000"))

tts = None


def authorized(header, token=None):
    """Pure. True when no token is configured, or the bearer token matches."""
    token = API_TOKEN if token is None else token
    if not token:
        return True
    scheme, _, given = (header or "").partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(given.strip(), token)


def clean_text(payload, limit=None):
    """Pure. -> (text, None) or (None, (status, detail))."""
    limit = MAX_TEXT_CHARS if limit is None else limit
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        return None, (400, "text required")
    text = text.strip()
    if len(text) > limit:
        return None, (413, f"text longer than {limit} characters")
    return text, None


def wav_to_ogg(wav_data):
    """WAV bytes -> OGG/Opus bytes via ffmpeg, or None when the conversion fails."""
    try:
        done = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-c:a", "libopus",
             "-b:a", "32k", "-f", "ogg", "pipe:1"],
            input=wav_data, capture_output=True, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout or None


@app.on_event("startup")
def load_model():
    global tts
    model_path = os.path.join(VOICE_DIR, VOICE_MODEL)
    for path in (model_path, model_path + ".json"):
        if not os.path.exists(path):
            raise RuntimeError(f"Piper voice file not found: {path} "
                               f"(a voice is the .onnx model plus its .onnx.json config)")
    tts = PiperVoice.load(model_path)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "model": VOICE_MODEL}


@app.post("/speak")
async def speak(request: Request):
    if not authorized(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="unauthorized")
    if tts is None:
        raise HTTPException(status_code=503, detail="tts not loaded")
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="JSON body required") from None
    text, problem = clean_text(payload)
    if problem:
        raise HTTPException(status_code=problem[0], detail=problem[1])
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        tts.synthesize_wav(text, wav_file)
    wav_data = wav_buffer.getvalue()
    ogg = wav_to_ogg(wav_data)
    if ogg:
        return Response(content=ogg, media_type="audio/ogg")
    return Response(content=wav_data, media_type="audio/wav")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
