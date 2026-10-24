"""CareCompass REST API. Speech endpoints only so far; /triage is not wired yet (the UI uses its mock)."""
import logging
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app import speech

log = logging.getLogger("carecompass")
MAX_AUDIO_BYTES = 10 * 1024 * 1024  # ~10 min of webm/opus; a spoken turn is far smaller

app = FastAPI(title="CareCompass API")
# ponytail: local demo origins only; add the deployed UI's origin when there is one.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/stt")
async def stt(request: Request):
    """Raw audio body (Content-Type e.g. audio/webm) -> {"transcript"} via Sarvam Saaras."""
    audio = await request.body()
    if not audio:
        raise HTTPException(400, "Empty audio")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audio too long")
    try:
        transcript = await run_in_threadpool(speech.transcribe, audio, request.headers.get("content-type", "audio/webm"))
    except Exception as e:
        log.exception("Sarvam STT failed")
        raise HTTPException(502, "Speech-to-text failed") from e
    return {"transcript": transcript}


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1500)
    language: Literal["en", "hi", "hinglish"] = "hi"


@app.post("/tts")
def tts(body: TTSRequest):
    """{"text", "language"} -> WAV audio via Sarvam Bulbul."""
    try:
        wav = speech.synthesize(body.text, body.language)
    except Exception as e:
        log.exception("Sarvam TTS failed")
        raise HTTPException(502, "Text-to-speech failed") from e
    return Response(wav, media_type="audio/wav")
