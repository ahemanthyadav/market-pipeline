#!/usr/bin/env python3
"""Voice services (manifests/voice): auth, request limits and the Piper call shape.
fastapi / piper / faster_whisper / uvicorn are stubbed — no model, no network.

    python3 tests/test_voice.py
"""
import importlib.util
import io
import sys
import types
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "manifests" / "voice"


class _App:
    def __init__(self, **kw):
        pass

    def _deco(self, *a, **k):
        return lambda fn: fn
    on_event = get = post = _deco


class HTTPException(Exception):
    def __init__(self, status_code, detail=""):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


class Response:
    def __init__(self, content=b"", media_type=None):
        self.content, self.media_type = content, media_type


fastapi = types.ModuleType("fastapi")
fastapi.FastAPI, fastapi.HTTPException = _App, HTTPException
fastapi.Request = fastapi.UploadFile = object
fastapi.File = lambda *a, **k: None
responses = types.ModuleType("fastapi.responses")
responses.Response = Response
piper = types.ModuleType("piper")


class PiperVoice:
    loaded = []

    @classmethod
    def load(cls, path):
        cls.loaded.append(path)
        return cls()

    def synthesize_wav(self, text, wav_file):
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes(b"\x00\x00" * 10)


piper.PiperVoice = PiperVoice
fw = types.ModuleType("faster_whisper")
fw.WhisperModel = object
for name, mod in (("fastapi", fastapi), ("fastapi.responses", responses), ("piper", piper),
                  ("faster_whisper", fw), ("uvicorn", types.ModuleType("uvicorn"))):
    sys.modules[name] = mod


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tts = load("tts_server", "tts-service/tts_server.py")
stt = load("stt_server", "stt-service/stt_server.py")


class Auth(unittest.TestCase):
    def test_open_when_no_token_is_configured(self):
        for mod in (tts, stt):
            self.assertTrue(mod.authorized(None, token=""))
            self.assertTrue(mod.authorized("Bearer whatever", token=""))

    def test_token_required_once_configured(self):
        for mod in (tts, stt):
            self.assertTrue(mod.authorized("Bearer s3cret", token="s3cret"))
            self.assertTrue(mod.authorized("bearer s3cret", token="s3cret"))
            for bad in (None, "", "s3cret", "Bearer nope", "Basic s3cret", "Bearer "):
                self.assertFalse(mod.authorized(bad, token="s3cret"), bad)


class Limits(unittest.TestCase):
    def test_text_is_required_and_capped(self):
        self.assertEqual(tts.clean_text({"text": "  hi  "}), ("hi", None))
        for bad in ({}, {"text": ""}, {"text": "   "}, {"text": 5}, ["x"], None):
            self.assertEqual(tts.clean_text(bad)[1][0], 400, bad)
        self.assertEqual(tts.clean_text({"text": "x" * 11}, limit=10)[1][0], 413)
        self.assertEqual(tts.clean_text({"text": "x" * 10}, limit=10)[0], "x" * 10)

    def test_upload_suffix_never_carries_user_path_text(self):
        self.assertEqual(stt.safe_suffix("voice.OGG"), ".ogg")
        self.assertEqual(stt.safe_suffix("a.mp3"), ".mp3")
        for bad in (None, "", "noext", "x.", "x.../../etc", "x.verylongext", "x.o g"):
            self.assertEqual(stt.safe_suffix(bad), ".ogg", bad)
        self.assertGreater(stt.MAX_AUDIO_BYTES, 0)


class PiperCallShape(unittest.TestCase):
    """The 2026-11-17 build called PiperVoice(model) and text_to_wav(): neither exists."""

    def test_load_needs_model_and_config_then_uses_load(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            tts.VOICE_DIR, tts.VOICE_MODEL = d, "v.onnx"
            (Path(d) / "v.onnx").write_bytes(b"x")
            with self.assertRaises(RuntimeError) as err:
                tts.load_model()                    # config .onnx.json missing
            self.assertIn("v.onnx.json", str(err.exception))
            (Path(d) / "v.onnx.json").write_text("{}")
            tts.load_model()
            self.assertEqual(PiperVoice.loaded[-1], str(Path(d) / "v.onnx"))
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                tts.tts.synthesize_wav("hello", w)
            self.assertTrue(buf.getvalue().startswith(b"RIFF"))

    def test_ffmpeg_failure_degrades_to_none(self):
        real = tts.subprocess.run

        def boom(*a, **k):
            raise FileNotFoundError("ffmpeg")
        tts.subprocess.run = boom
        try:
            self.assertIsNone(tts.wav_to_ogg(b"RIFF"))
        finally:
            tts.subprocess.run = real


if __name__ == "__main__":
    unittest.main(verbosity=1)
