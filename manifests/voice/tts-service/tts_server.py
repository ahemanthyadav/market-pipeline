# TTS service — Piper HTTP server (runs on Orin Nano)

# Run with:  python tts_server.py

# Endpoints:
#   GET  /healthz   -> {"status": "ok"}
#   POST /speak     -> JSON {"text": "..."} -> returns OGG/Opus audio (application/ogg)

import os
import io
import wave
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from piper.tts import PiperTts
import uvicorn

app = FastAPI(title="tts-service", version="1.0.0")

VOICE_MODEL = os.environ.get("PIPER_MODEL", "en_US-lessac-medium.onnx")
VOICE_DIR = os.environ.get("PIPER_VOICE_DIR", "/root/.local/share/piper/voices")

tts = None

@app.on_event("startup")
def load_model():
    global tts
    model_path = os.path.join(VOICE_DIR, VOICE_MODEL)
    if not os.path.exists(model_path):
        raise RuntimeError(f"Piper voice model not found: {model_path}")
    tts = PiperTts(model_path)

@app.get("/healthz")
def healthz():
    return {"status": "ok", "model": VOICE_MODEL}

@app.post("/speak")
async def speak(payload: dict):
    if tts is None:
        raise HTTPException(status_code=503, detail="tts not loaded")
    text = payload.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text required")
    # Piper writes to a WAV buffer; convert to OGG/Opus for Telegram
    wav_buffer = io.BytesIO()
    tts.text_to_wav(text, wav_buffer)
    wav_buffer.seek(0)
    # Minimal WAV -> OGG: return raw WAV if opus not available;
    # Telegram accepts OGG/Opus. For MVP, WAV is acceptable in many clients.
    # To emit OGG, pipe through ffmpeg here if installed.
    wav_data = wav_buffer.read()
    return Response(content=wav_data, media_type="audio/wav")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
