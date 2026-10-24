"""CareCompass REST API: /triage (the organiser contract), speech, sign-in, and per-user history."""
import json
import logging
import secrets
import time
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Path, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app import auth, db, pipeline, services, speech
from app.auth import current_user
from app.schemas import TriageRequest, TriageResponse

log = logging.getLogger("carecompass")
MAX_AUDIO_BYTES = 10 * 1024 * 1024  # ~10 min of webm/opus; a spoken turn is far smaller

db.init()
app = FastAPI(title="CareCompass API")
# ponytail: local demo origins only; add the deployed UI's origin when there is one.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,  # session cookie
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(services.router)


@app.post("/triage", response_model=TriageResponse)
def triage(body: TriageRequest):
    """Returns either a follow_up_question (decision fields null) or a decision with matched caregivers."""
    return pipeline.run(body)


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


# ---------------------------------------------------------------- per-user history (signed in only)

UUID_RE = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
ChatId = Path(pattern=UUID_RE)
MAX_JSON = 100_000


def _dump(obj) -> str:
    s = json.dumps(obj, ensure_ascii=False)
    if len(s) > MAX_JSON:
        raise HTTPException(413, "Too large")
    return s


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=2000)


class ChatIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    messages: list[Message] = Field(max_length=100)
    outcome: Optional[dict] = None  # the final /triage response


@app.get("/me/chats")
def list_chats(user=Depends(current_user)):
    with db.connect() as con:
        rows = con.execute(
            "SELECT id, title, outcome, created_at, updated_at FROM chats WHERE user_id = ? ORDER BY updated_at DESC LIMIT 50",
            (user["id"],),
        ).fetchall()
    out = []
    for r in rows:
        o = json.loads(r["outcome"]) if r["outcome"] else {}
        out.append({"id": r["id"], "title": r["title"], "created_at": r["created_at"], "updated_at": r["updated_at"],
                    "care_type": o.get("care_type"), "urgency": o.get("urgency")})
    return out


@app.get("/me/chats/{chat_id}")
def get_chat(chat_id: str = ChatId, user=Depends(current_user)):
    with db.connect() as con:
        r = con.execute("SELECT * FROM chats WHERE id = ? AND user_id = ?", (chat_id, user["id"])).fetchone()
    if not r:
        raise HTTPException(404, "Chat not found")
    return {"id": r["id"], "title": r["title"], "messages": json.loads(r["messages"]),
            "outcome": json.loads(r["outcome"]) if r["outcome"] else None,
            "created_at": r["created_at"], "updated_at": r["updated_at"]}


@app.put("/me/chats/{chat_id}")
def save_chat(body: ChatIn, chat_id: str = ChatId, user=Depends(current_user)):
    now = int(time.time())
    with db.connect() as con:
        cur = con.execute(
            """INSERT INTO chats (id, user_id, title, messages, outcome, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET title = excluded.title, messages = excluded.messages,
                 outcome = excluded.outcome, updated_at = excluded.updated_at
               WHERE chats.user_id = excluded.user_id""",
            (chat_id, user["id"], body.title, _dump([m.model_dump() for m in body.messages]),
             body.outcome and _dump(body.outcome), now, now),
        )
    if cur.rowcount == 0:  # the id belongs to someone else
        raise HTTPException(404, "Chat not found")
    return {"id": chat_id, "updated_at": now}


class CaregiverIn(BaseModel):
    caregiver_id: str = Field(max_length=64)
    name: Optional[str] = Field(default=None, max_length=120)
    role: Optional[str] = Field(default=None, max_length=120)
    reason: Optional[str] = Field(default=None, max_length=300)
    distance_km: Optional[float] = None
    rating: Optional[float] = None


class LocationIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    name: Optional[str] = Field(default=None, max_length=120)


class BookingIn(BaseModel):
    chat_id: Optional[str] = Field(default=None, pattern=UUID_RE)
    caregiver: CaregiverIn
    care_type: str = Field(max_length=64)
    urgency: Optional[str] = Field(default=None, max_length=32)
    slot: Optional[str] = Field(default=None, max_length=64)  # ISO 8601
    location: Optional[LocationIn] = None


def _booking(r) -> dict:
    return {"id": r["id"], "chat_id": r["chat_id"], "caregiver": json.loads(r["caregiver"]), "care_type": r["care_type"],
            "urgency": r["urgency"], "slot": r["slot"], "location": r["location"] and json.loads(r["location"]),
            "created_at": r["created_at"]}


@app.get("/me/bookings")
def list_bookings(user=Depends(current_user)):
    with db.connect() as con:
        rows = con.execute("SELECT * FROM bookings WHERE user_id = ? ORDER BY created_at DESC LIMIT 50", (user["id"],)).fetchall()
    return [_booking(r) for r in rows]


@app.post("/me/bookings")
def create_booking(body: BookingIn, user=Depends(current_user)):
    """ponytail: records the booking only; no caregiver-side scheduling exists yet."""
    ref = "CC-" + secrets.token_hex(3).upper()
    with db.connect() as con:
        con.execute(
            "INSERT INTO bookings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ref, user["id"], body.chat_id, _dump(body.caregiver.model_dump()), body.care_type, body.urgency, body.slot,
             body.location and _dump(body.location.model_dump()), int(time.time())),
        )
        r = con.execute("SELECT * FROM bookings WHERE id = ?", (ref,)).fetchone()
    return _booking(r)
