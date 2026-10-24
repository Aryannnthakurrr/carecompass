"""Structurer: the whole conversation -> CaseFacts (English facts only; no decisions, no questions)."""
import re

from app import llm
from app.schemas import CaseFacts, Turn

SYSTEM = """You turn a patient's conversation with a home-healthcare app (English, Devanagari Hindi, or romanised Hinglish)
into facts for a triage system. Record only what the user said across ALL their turns; never guess, diagnose or decide urgency.
Translate everything to short English phrases. Symptoms the user denies ("bukhar nahi", "no vomiting") go ONLY in symptoms_denied.
Care tasks (injection, dressing, catheter, blood test, BP/sugar check, report review) go in services_requested, not symptoms.
setting_requested: home_visit if they want someone at home, video_consult if they want to talk to a doctor, lab_test if they want a
test or sample collection, else unspecified. preferences: only what the user explicitly asks for (cheap/sasti, nearby/paas,
best/experienced, today/abhi, lady doctor, at home, video/phone, just a consultation, hospital); empty if none.
detected_language is the language of the user's LATEST message."""


def transcript(history: list[Turn], user_input: str) -> str:
    lines = [f"{'User' if t.role == 'user' else 'App'}: {t.content}" for t in history]
    lines.append(f"User: {user_input}")
    return "\n".join(lines)


_HINGLISH = re.compile(r"\b(hai|hain|ho|ko|ka|ki|ke|se|mein|me|mujhe|meri|mera|nahi|bhi|aur|kya|chahiye|karna|karwana|dard|bukhar|khansi|raha|rahi|papa|mummy|bahut|ghar|pe|par|liye|koi|wala)\b", re.I)


def guess_language(text: str) -> str:
    if re.search(r"[ऀ-ॿ]", text):
        return "hi"
    return "hinglish" if len(_HINGLISH.findall(text)) >= 2 else "en"


def structure(history: list[Turn], user_input: str) -> CaseFacts:
    return llm.chat(SYSTEM, f"Conversation:\n{transcript(history, user_input)}", CaseFacts)
