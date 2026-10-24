"""API contract (organiser schema + optional extras) and the internal JSON shapes the models exchange."""
import json
from typing import Literal, Optional, get_args, get_origin

from pydantic import BaseModel, Field, model_validator
from pydantic_core import PydanticUndefined

from app.llm import CFG


def _literal_values(ann) -> set:
    """The allowed values of a Literal, Optional[Literal] or list[Literal] annotation; empty if it is not one."""
    if get_origin(ann) is Literal:
        return set(get_args(ann))
    return set().union(*(_literal_values(a) for a in get_args(ann))) if get_args(ann) else set()

LABELS = CFG["labels"]
SMART_CARE = ("hospital_visit", "clinic_visit", "specialist_visit", "diagnostic_test")  # smart mode only
OrganiserCare = Literal[tuple(LABELS["care_types"])]
CareType = Literal[tuple(LABELS["care_types"]) + SMART_CARE]
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
    kind: Optional[str] = None  # assistant turns: echo the reply's follow_up_kind back (budgets count medical questions only)


class TriageRequest(BaseModel):
    user_input: str = Field(min_length=1, max_length=2000)
    language: Literal["auto", "en", "hi", "hinglish"] = "auto"
    location: Optional[Location] = None
    conversation_history: list[Turn] = Field(default_factory=list, max_length=40)
    mode: Literal["dataset", "smart"] = "dataset"  # dataset: organiser roster + reference matching; smart: providers.csv + dynamic matching
    input_mode: Literal["text", "voice"] = "text"  # voice: user_input is a speech transcript, so words may be mis-heard


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
    # smart mode
    factors: list[str] = Field(default_factory=list)
    fee_inr: Optional[int] = None
    provider_type: Optional[str] = None


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
    mode: Literal["dataset", "smart"] = "dataset"
    # smart mode
    seriousness: Optional[Literal["routine", "soon", "urgent", "emergency"]] = None
    specialty: Optional[str] = None
    recommended_tests: list[str] = Field(default_factory=list)
    services_needed: list[str] = Field(default_factory=list)
    match_summary: Optional[str] = None
    possible_causes: list[str] = Field(default_factory=list)
    # conversation control
    follow_up_kind: Optional[Literal["medical", "safety_check", "clarify", "redirect", "misheard"]] = None
    safety_note: Optional[str] = None  # shown with a safety_check question: "if this is happening right now, call 112"
    conversation_ended: bool = False   # repeated non-health messages: closed politely (explanation holds the closing line)


# ---------------------------------------------------------------- model I/O

class CaseFacts(BaseModel):
    """Structurer output: what the user said, in English. No decisions."""
    symptoms_present: list[str] = Field(default_factory=list, description="Symptoms the patient HAS, short English clinical phrases")
    symptoms_denied: list[str] = Field(default_factory=list, description="Symptoms the user explicitly says are absent")
    onset: Optional[Literal["sudden", "gradual"]] = None
    duration: Optional[str] = None
    patient: Optional[str] = Field(None, description="Who the patient is, e.g. 'father, 70 years', 'baby, 2 months'")
    special_population: list[Literal["infant", "child", "elderly", "pregnant"]] = Field(default_factory=list)
    existing_conditions: list[str] = Field(default_factory=list, description="e.g. diabetes, recent surgery, bedridden")
    vitals_reported: list[str] = Field(default_factory=list, description="e.g. 'temperature 103F', 'sugar 350'")
    services_requested: list[str] = Field(default_factory=list, description="Specific tasks asked for, e.g. injection, wound dressing, catheter care, blood test, full body checkup, BP and sugar check, second opinion on reports")
    setting_requested: Optional[Literal["home_visit", "video_consult", "lab_test", "unspecified"]] = None
    severity: Optional[Literal["mild", "moderate", "severe"]] = None
    preferences: list[Literal["quality", "nearby", "budget", "soon", "female", "home", "online", "consult_only", "hospital"]] = Field(
        default_factory=list,
        description="Only what the user explicitly ASKS FOR about the care, in any language: quality (best, experienced, bad past doctor), "
                    "nearby, budget (cheap, sasta/sasti, kam paise), soon (wants help today/now; NOT a symptom that started today), "
                    "female (lady doctor), home, online (video/phone call), consult_only (just a consultation, no tests), "
                    "hospital (wants a hospital; NOT 'was in hospital')")
    detected_language: Lang = Field("en", description="hi = Devanagari Hindi, hinglish = romanised Hindi/English mix, en = English")
    # reading the conversation, not just extracting it
    age_group: Optional[Literal["infant", "child", "adult", "elderly"]] = Field(None, description="Patient's age group if stated or clear; null if unknown")
    message_check: Literal["ok", "unclear", "off_topic", "nonsense", "abusive", "mixed_up", "contradictory"] = Field(
        "ok", description="Is the conversation usable as a health request? See the rules.")
    check_notes: list[str] = Field(default_factory=list, max_length=3, description="What is wrong with the conversation, in plain English; empty if ok")
    not_literal: list[str] = Field(default_factory=list, max_length=3,
                                   description="Alarming words NOT meant literally or denied (figure of speech, joke, past event), e.g. 'heart attack (figure of speech)'")
    user_wants: Optional[str] = Field(None, description="What the user is asking for, in a few English words")
    no_more_questions: bool = Field(False, description="True if the user refuses further questions or says just give me a doctor")
    asks_medicine_or_diagnosis: bool = Field(False, description="True if the user asks which medicine/dose to take or what disease it is")

    @model_validator(mode="before")
    @classmethod
    def _lenient(cls, data):
        """One unexpected value (a preference the list doesn't have, a reading given as an object) must not void the whole
        reading of a perfectly clear message: unknown list items are dropped, odd types coerced, bad enums cleared."""
        if not isinstance(data, dict):
            return data
        d = dict(data)
        for name, field in cls.model_fields.items():
            if name not in d or d[name] is None:
                continue
            v, ann = d[name], field.annotation
            allowed = _literal_values(ann)
            if getattr(ann, "__origin__", None) is list or str(ann).startswith("list"):
                items = v if isinstance(v, list) else [v]
                items = [json.dumps(i, ensure_ascii=False) if isinstance(i, (dict, list)) else str(i) for i in items]
                d[name] = [i for i in items if not allowed or i in allowed]
            elif allowed and v not in allowed:
                d[name] = field.default if field.default is not PydanticUndefined else None
            elif ann is bool and not isinstance(v, bool):
                d[name] = str(v).lower() in ("true", "yes", "1")
            elif isinstance(v, (dict, list)) and "str" in str(ann):
                d[name] = json.dumps(v, ensure_ascii=False)
        return d


class Decision(BaseModel):
    """Classifier output: decided from CaseFacts alone."""
    status: Literal["final", "need_more_info"]
    care_type: OrganiserCare
    red_flags: list[str] = Field(max_length=4, description="Red-flag findings present, empty if none")
    missing_info: list[str] = Field(max_length=2, description="At most 2 plain-English questions whose answers could change the decision; empty if final")
    reasoning: str = Field(description="One short English sentence, no diagnosis, no medication")
