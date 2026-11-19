"""Voice input for the bot: a Telegram voice note -> text via the in-cluster STT service.

The bot treats the transcript exactly like typed text, with one difference: a
voice turn is READ-ONLY (bot.run_command refuses the owner-only commands and any
watchlist change) — a misheard word must never write anything.

Off unless STT_URL is set (bot Deployment env). Config:
  STT_URL             e.g. http://stt-service.market-data.svc.cluster.local:8000
  VOICE_API_TOKEN     optional bearer token (Secret voice-api-token; unset = no header)
  VOICE_MAX_SECONDS   longest note accepted (default 30 — transcription runs on the
                      bot's single poll loop, so a long note delays every other chat)
  VOICE_MAX_BYTES     largest file accepted (default 1 MiB; Opus at 30 s is ~120 kB)

Nothing here raises to the caller: transcribe() returns (text, problem).
"""
import os

MAX_SECONDS = int(os.environ.get("VOICE_MAX_SECONDS", "30"))
MAX_BYTES = int(os.environ.get("VOICE_MAX_BYTES", str(1024 * 1024)))
STT_TIMEOUT = 60

NOT_ENABLED = "Voice input is not enabled — send that as text."
NOT_HEARD = "I could not make out any words in that voice message — try again or send text."
FAILED = "Voice transcription is unavailable right now — send that as text."
READ_ONLY = ("Voice messages are read-only — they cannot change a watchlist or manage "
             "users. Send that one as text.")


def enabled():
    return bool(os.environ.get("STT_URL"))


def voice_ref(msg):
    """Pure. (file_id, seconds, bytes) for a voice note / audio file in a message, else None."""
    v = (msg or {}).get("voice") or (msg or {}).get("audio")
    if not isinstance(v, dict) or not v.get("file_id"):
        return None
    return v["file_id"], int(v.get("duration") or 0), int(v.get("file_size") or 0)


def refusal(seconds, size, max_seconds=None, max_bytes=None):
    """Pure. A message for the user when the note is too long/large, else None."""
    max_seconds = MAX_SECONDS if max_seconds is None else max_seconds
    max_bytes = MAX_BYTES if max_bytes is None else max_bytes
    if seconds > max_seconds:
        return (f"That voice message is {seconds}s — the limit is {max_seconds}s. "
                f"Say just the request, e.g. “TCS price”.")
    if size > max_bytes:
        return f"That audio file is too large ({size // 1024} kB; limit {max_bytes // 1024} kB)."
    return None


def clean_transcript(text):
    """Pure. Whisper writes sentences ("Tcs price." / "What's Nifty at?"); the router
    wants the bare request. One line, outer quotes and trailing punctuation removed —
    a trailing "?" is kept out too: on typed text it is the 'explain with AI' flag,
    and speech should not switch that on by intonation."""
    t = " ".join((text or "").split()).strip("\"'“”‘’ ")
    return t.rstrip(".!?。 ").strip()


def transcribe(api, file_api, file_id, http):
    """-> (text, None) or (None, message). `api` = https://api.telegram.org/bot<token>,
    `file_api` = https://api.telegram.org/file/bot<token>, `http` = the requests module.
    Size is re-checked on the downloaded bytes (file_size in the update is optional)."""
    if not enabled():
        return None, NOT_ENABLED
    try:
        meta = http.get(f"{api}/getFile", params={"file_id": file_id}, timeout=15).json()
        path = (meta.get("result") or {}).get("file_path")
        if not path:
            return None, FAILED
        audio = http.get(f"{file_api}/{path}", timeout=30)
        if audio.status_code != 200:
            return None, FAILED
        data = audio.content
        if len(data) > MAX_BYTES:
            return None, refusal(0, len(data))
        headers = {}
        token = os.environ.get("VOICE_API_TOKEN")
        if token:
            headers["Authorization"] = "Bearer " + token
        r = http.post(os.environ["STT_URL"].rstrip("/") + "/transcribe",
                      files={"audio": ("voice.ogg", data, "audio/ogg")},
                      headers=headers, timeout=STT_TIMEOUT)
        if r.status_code != 200:
            print(f"WARN stt-service answered {r.status_code}", flush=True)
            return None, FAILED
        text = clean_transcript(r.json().get("text"))
    except Exception as exc:  # noqa: BLE001 — voice is optional; the bot must keep answering
        print(f"WARN voice transcription failed ({exc.__class__.__name__})", flush=True)
        return None, FAILED
    return (text, None) if text else (None, NOT_HEARD)
