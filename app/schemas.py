"""API contract (organiser schema + optional extras) and the internal JSON shapes the models exchange."""
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.llm import CFG

LABELS = CFG["labels"]
CareType = Literal[tuple(LABELS["care_types"])]
Urgency = Literal[tuple(LABELS["urgency"])]
RedFlag = Literal[tuple(LABELS["red_flags"])]
Lang = Literal["en", "hi", "hinglish"]


# ---------------------------------------------------------------- API

class Location(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=2000)


class TriageRequest(BaseModel):
    user_input: str = Field(min_length=1, max_length=2000)
    language: Literal["auto", "en", "hi", "hinglish"] = "auto"
    location: Optional[Location] = None
    conversation_history: list[Turn] = Field(default_factory=list, max_length=40)


class MatchedCaregiver(BaseModel):
    caregiver_id: str
    score: float
    reason: str
    # extras the UI shows
    name: Optional[str] = None
    role: Optional[str] = None
    distance_km: Optional[float] = None
    languages: list[str] = Field(default_factory=list)
    rating: Optional[float] = None
    next_slot: Optional[str] = None


class Emergency(BaseModel):
    title: str
    call: list[str]
    steps: list[str]


class TriageResponse(BaseModel):
    # organiser contract
    follow_up_question: Optional[str] = None
    care_type: Optional[CareType] = None
    urgency: Optional[Urgency] = None
    red_flags: list[RedFlag] = Field(default_factory=list)
    reasoning: Optional[str] = None
    matched_caregivers: list[MatchedCaregiver] = Field(default_factory=list)
    # optional extras
    language: Lang = "en"
    explanation: Optional[str] = None
    emergency: Optional[Emergency] = None


# ---------------------------------------------------------------- model I/O

class CaseFacts(BaseModel):
    """Structurer output: what the user said, in English. No decisions."""
    symptoms_present: list[str] = Field(description="Symptoms the patient HAS, short English clinical phrases")
    symptoms_denied: list[str] = Field(description="Symptoms the user explicitly says are absent")
    onset: Optional[Literal["sudden", "gradual"]] = None
    duration: Optional[str] = None
    patient: Optional[str] = Field(None, description="Who the patient is, e.g. 'father, 70 years', 'baby, 2 months'")
    special_population: list[Literal["infant", "child", "elderly", "pregnant"]] = Field(default_factory=list)
    existing_conditions: list[str] = Field(default_factory=list, description="e.g. diabetes, recent surgery, bedridden")
    vitals_reported: list[str] = Field(default_factory=list, description="e.g. 'temperature 103F', 'sugar 350'")
    services_requested: list[str] = Field(default_factory=list, description="Specific tasks asked for, e.g. injection, wound dressing, catheter care, blood test, full body checkup, BP and sugar check, second opinion on reports")
    setting_requested: Optional[Literal["home_visit", "video_consult", "lab_test", "unspecified"]] = None
    severity: Optional[Literal["mild", "moderate", "severe"]] = None
    detected_language: Lang = Field(description="hi = Devanagari Hindi, hinglish = romanised Hindi/English mix, en = English")


class Decision(BaseModel):
    """Classifier output: decided from CaseFacts alone."""
    status: Literal["final", "need_more_info"]
    care_type: CareType
    red_flags: list[str] = Field(max_length=4, description="Red-flag findings present, empty if none")
    missing_info: list[str] = Field(max_length=2, description="At most 2 plain-English questions whose answers could change the decision; empty if final")
    reasoning: str = Field(description="One short English sentence, no diagnosis, no medication")
