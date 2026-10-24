"""Sarvam speech: Saaras speech-to-text and Bulbul text-to-speech. Models come from config.yaml."""
import base64
import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from sarvamai import SarvamAI

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
MODELS = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["models"]

TTS_LANG = {"en": "en-IN", "hi": "hi-IN", "hinglish": "hi-IN"}
# Browser MediaRecorder types -> Saaras input codecs
CODECS = {"webm": "webm", "ogg": "ogg", "mp4": "mp4", "x-m4a": "x-m4a", "wav": "wav", "x-wav": "x-wav", "mpeg": "mpeg", "mp3": "mp3"}


@lru_cache(maxsize=1)
def client() -> SarvamAI:
    key = os.getenv("SARVAM_API_KEY")
    if not key:
        raise RuntimeError("SARVAM_API_KEY is not set in .env")
    return SarvamAI(api_subscription_key=key)


def transcribe(audio: bytes, content_type: str) -> str:
    """Original words (Hindi in Devanagari, English as spoken) so red-flag phrases survive."""
    mime = content_type.split(";")[0].strip().lower() or "audio/webm"  # Sarvam rejects "audio/webm;codecs=opus"
    codec = CODECS.get(mime.split("/")[-1], "webm")
    m = MODELS["stt"]
    r = client().speech_to_text.transcribe(
        file=(f"speech.{codec}", audio, mime),
        model=m["model"], mode=m["mode"], language_code=m["language_code"], input_audio_codec=codec,
    )
    return (r.transcript or "").strip()


def synthesize(text: str, language: str) -> bytes:
    """WAV bytes for `text`; language is the UI's en / hi / hinglish."""
    m = MODELS["tts"]
    r = client().text_to_speech.convert(
        text=text, language_code=TTS_LANG.get(language, m["language_code"]), speaker=m["speaker"], model=m["model"],
    )
    return base64.b64decode("".join(r.audios))
