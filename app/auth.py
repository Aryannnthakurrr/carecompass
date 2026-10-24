"""Sign-in with Google (ID token) or phone OTP. Both end in an HttpOnly session cookie.

Session tokens and OTP codes are stored only as hashes.
"""
import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv
from fastapi import APIRouter, Cookie, HTTPException, Response
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pydantic import BaseModel, Field

from app import db

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
log = logging.getLogger("carecompass.auth")
router = APIRouter(prefix="/auth")

COOKIE = "cc_session"
SESSION_TTL = 30 * 86400
OTP_TTL = 5 * 60
OTP_RESEND_GAP = 30
OTP_MAX_SENDS_PER_HOUR = 5
OTP_MAX_ATTEMPTS = 5
_OTP_KEY = secrets.token_bytes(32)  # per process: restarting the API voids codes still in flight


def google_client_id() -> Optional[str]:
    return os.getenv("GOOGLE_CLIENT_ID") or None


def otp_dev_mode() -> bool:
    return os.getenv("OTP_DEV_MODE") == "1"


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _code_hash(phone: str, code: str) -> str:
    return hmac.new(_OTP_KEY, f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


def public_user(row) -> dict:
    phone = row["phone"]
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "phone": phone and f"{phone[:3]} {phone[3:5]}•••••{phone[-3:]}",
        "picture": row["picture"],
    }


def _start_session(response: Response, user_id: int) -> None:
    token = secrets.token_urlsafe(32)
    with db.connect() as con:
        con.execute("INSERT INTO sessions VALUES (?, ?, ?)", (_sha(token), user_id, int(time.time()) + SESSION_TTL))
    response.set_cookie(
        COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="lax",
        secure=os.getenv("COOKIE_SECURE") == "1", path="/",
    )


def _user_row(user_id: int):
    with db.connect() as con:
        return con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def current_user(cc_session: Optional[str] = Cookie(default=None)) -> dict:
    """FastAPI dependency: the signed-in user, or 401."""
    if cc_session:
        with db.connect() as con:
            row = con.execute(
                "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at > ?",
                (_sha(cc_session), int(time.time())),
            ).fetchone()
        if row:
            return dict(row)
    raise HTTPException(401, "Not signed in")


# ---------------------------------------------------------------- endpoints

@router.get("/config")
def config():
    """What the sign-in sheet should offer."""
    phone = "sms" if sms_configured() else "dev" if otp_dev_mode() else None
    return {"google_client_id": google_client_id(), "phone": phone}


@router.get("/me")
def me(cc_session: Optional[str] = Cookie(default=None)):
    return {"user": public_user(current_user(cc_session))}


@router.post("/logout")
def logout(response: Response, cc_session: Optional[str] = Cookie(default=None)):
    if cc_session:
        with db.connect() as con:
            con.execute("DELETE FROM sessions WHERE token_hash = ?", (_sha(cc_session),))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


class GoogleIn(BaseModel):
    credential: str = Field(min_length=20, max_length=4096)


@router.post("/google")
def google_sign_in(body: GoogleIn, response: Response):
    cid = google_client_id()
    if not cid:
        raise HTTPException(503, "Google sign-in is not configured (set GOOGLE_CLIENT_ID in .env)")
    try:  # checks signature, audience, issuer and expiry
        info = id_token.verify_oauth2_token(body.credential, google_requests.Request(), cid)
    except ValueError as e:
        raise HTTPException(401, "Invalid Google credential") from e
    with db.connect() as con:
        con.execute(
            """INSERT INTO users (google_sub, email, name, picture, created_at) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(google_sub) DO UPDATE SET email = excluded.email, name = excluded.name, picture = excluded.picture""",
            (info["sub"], info.get("email"), info.get("name"), info.get("picture"), int(time.time())),
        )
        uid = con.execute("SELECT id FROM users WHERE google_sub = ?", (info["sub"],)).fetchone()["id"]
    _start_session(response, uid)
    return {"user": public_user(_user_row(uid))}


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"[^\d+]", "", raw)
    m = re.fullmatch(r"(?:\+?91|0)?([6-9]\d{9})", digits)
    if not m:
        raise HTTPException(422, "Enter a valid 10-digit Indian mobile number")
    return "+91" + m.group(1)


HTTPSMS_URL = "https://api.httpsms.com/v1/messages/send"


def twilio_configured() -> bool:
    return all(os.getenv(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_VERIFY_SID"))


def httpsms_configured() -> bool:
    return bool(os.getenv("HTTPSMS_API_KEY") and os.getenv("HTTPSMS_FROM"))


def sms_configured() -> bool:
    return twilio_configured() or httpsms_configured()


def _twilio(path: str, **data) -> requests.Response:
    """Twilio Verify generates, sends and checks the code (and handles India's DLT rules for us)."""
    return requests.post(
        f"https://verify.twilio.com/v2/Services/{os.environ['TWILIO_VERIFY_SID']}/{path}", data=data,
        auth=(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]), timeout=10,
    )


def twilio_check(phone: str, code: str) -> bool:
    r = _twilio("VerificationCheck", To=phone, Code=code)
    if not r.ok and r.status_code not in (404, 429):  # 404/429: expired or too many tries, i.e. not approved
        log.error("Twilio check failed: %s %s", r.status_code, r.text[:300])
        raise HTTPException(502, "Couldn't check the code. Please try again.")
    return r.ok and r.json().get("status") == "approved"


def send_sms(phone: str, code: str) -> None:
    """Deliver the code: Twilio Verify (sends its own code), else httpSMS (an Android phone as gateway); dev mode only logs it."""
    if twilio_configured():
        r = _twilio("Verifications", To=phone, Channel="sms")
        if not r.ok:
            log.error("Twilio send failed: %s %s", r.status_code, r.text[:300])
            if r.json().get("code") == 21608:  # trial accounts can only text numbers verified in the console
                raise HTTPException(502, "This number isn't verified on the Twilio trial account yet.")
            raise HTTPException(502, "Couldn't send the SMS. Please try again.")
        return
    if not httpsms_configured():
        log.warning("OTP for %s: %s (dev mode, not sent by SMS)", phone, code)
        return
    r = requests.post(
        HTTPSMS_URL,
        headers={"x-api-key": os.environ["HTTPSMS_API_KEY"]},
        json={"from": os.environ["HTTPSMS_FROM"], "to": phone,
              "content": f"{code} is your CareCompass sign-in code. It expires in 5 minutes. Do not share it."},
        timeout=10,
    )
    if not r.ok:
        log.error("httpSMS send failed: %s %s", r.status_code, r.text[:300])
        raise HTTPException(502, "Couldn't send the SMS. Please try again.")


class PhoneIn(BaseModel):
    phone: str = Field(max_length=20)


class OtpIn(PhoneIn):
    code: str = Field(pattern=r"^\d{6}$")


@router.post("/otp/send")
def otp_send(body: PhoneIn):
    if not (sms_configured() or otp_dev_mode()):
        raise HTTPException(503, "Phone sign-in needs Twilio (TWILIO_*) or httpSMS (HTTPSMS_*) keys, or OTP_DEV_MODE=1 for the demo")
    phone = normalize_phone(body.phone)
    now = int(time.time())
    with db.connect() as con:
        row = con.execute("SELECT * FROM otps WHERE phone = ?", (phone,)).fetchone()
        if row and now - row["last_sent_at"] < OTP_RESEND_GAP:
            raise HTTPException(429, f"Please wait {OTP_RESEND_GAP - (now - row['last_sent_at'])} seconds before asking again")
        fresh_window = not row or now - row["window_start"] >= 3600
        window_start, sends = (now, 0) if fresh_window else (row["window_start"], row["sends_in_window"])
        if sends >= OTP_MAX_SENDS_PER_HOUR:
            raise HTTPException(429, "Too many codes requested. Try again in an hour.")
        code = f"{secrets.randbelow(10**6):06d}"
        con.execute(
            "INSERT OR REPLACE INTO otps VALUES (?, ?, ?, 0, ?, ?, ?)",
            (phone, _code_hash(phone, code), now + OTP_TTL, now, window_start, sends + 1),
        )
    try:
        send_sms(phone, code)
    except HTTPException:
        with db.connect() as con:  # a failed send shouldn't make the user wait out the resend gap
            con.execute("UPDATE otps SET last_sent_at = 0 WHERE phone = ?", (phone,))
        raise
    out = {"sent": True, "phone": phone, "expires_in": OTP_TTL, "resend_in": OTP_RESEND_GAP}
    if not sms_configured():
        out["dev_code"] = code  # demo only: there is no SMS to read it from
    return out


@router.post("/otp/verify")
def otp_verify(body: OtpIn, response: Response):
    phone = normalize_phone(body.phone)
    now = int(time.time())
    # Twilio owns the code when it sent it; ask it first, outside the DB transaction (it's a network call).
    approved = twilio_check(phone, body.code) if twilio_configured() else None
    # Decide inside the transaction, raise after it, so a wrong-attempt count is committed.
    with db.connect() as con:
        row = con.execute("SELECT * FROM otps WHERE phone = ?", (phone,)).fetchone()
        if not row or row["expires_at"] < now:
            error = (400, "Code expired. Ask for a new one.")
        elif row["attempts"] >= OTP_MAX_ATTEMPTS:
            error = (429, "Too many wrong tries. Ask for a new code.")
        elif not (approved if approved is not None else hmac.compare_digest(row["code_hash"], _code_hash(phone, body.code))):
            con.execute("UPDATE otps SET attempts = attempts + 1 WHERE phone = ?", (phone,))
            error = (400, "Wrong code")
        else:
            error = None
            con.execute("DELETE FROM otps WHERE phone = ?", (phone,))
            con.execute("INSERT INTO users (phone, created_at) VALUES (?, ?) ON CONFLICT(phone) DO NOTHING", (phone, now))
            uid = con.execute("SELECT id FROM users WHERE phone = ?", (phone,)).fetchone()["id"]
    if error:
        raise HTTPException(*error)
    _start_session(response, uid)
    return {"user": public_user(_user_row(uid))}
