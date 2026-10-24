"""Smart-mode seriousness analyser: CaseFacts + keyword hints -> SmartDecision (how serious, what kind of care,
which specialty, which first tests and procedures). MedGemma is the medical judge; Qwen runs in parallel as a
second opinion. Either can escalate to an emergency."""
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app import intake, llm, protocols
from app.lexicon import SERVICES, SPECIALTY_TAGS, TEST_KINDS, Hints

TEST_TAGS = tuple(t for t, v in SERVICES.items() if v[1] in TEST_KINDS)
PROC_TAGS = tuple(t for t, v in SERVICES.items() if v[1] not in TEST_KINDS and t != "consultation")
SMART_CARE = ("emergency_112", "hospital_visit", "clinic_visit", "specialist_visit", "diagnostic_test", "lab_test_home",
              "home_nursing", "paramedic_home_visit", "teleconsultation")
SERIOUSNESS = ("routine", "soon", "urgent", "emergency")


class SmartDecision(BaseModel):
    status: Literal["final", "need_more_info"]
    seriousness: Literal[SERIOUSNESS]
    care_type: Literal[SMART_CARE]
    specialty: Literal[tuple(SPECIALTY_TAGS) + ("none",)]
    recommended_tests: list[Literal[TEST_TAGS]] = Field(max_length=4, description="First tests a doctor usually orders; empty if none")
    services_needed: list[Literal[PROC_TAGS]] = Field(max_length=3, description="Procedures needed, e.g. tetanus_shot, wound_dressing")
    consult_first: bool = Field(description="True if a doctor should see the patient before any test, or the user only wants a consultation")
    red_flags: list[str] = Field(max_length=4)
    missing_info: list[str] = Field(max_length=2, description="At most 2 plain questions whose answers change seriousness or care; empty if final")
    reasoning: str = Field(description="One short English sentence. No disease names, no medicines.")
    possible_causes: list[str] = Field(default_factory=list, max_length=3, description="Internal only, never shown unless enabled")


SYSTEM = f"""You are the medical brain of a care-navigation app in Delhi NCR. You receive structured facts about a request plus
keyword hints. You route; you do not treat. Decide:
seriousness: emergency (call 112 now), urgent (needs care today, e.g. a deep cut that may need stitches, a possible fracture,
  an animal bite, high fever in a small child), soon (within 2-3 days), routine (can be planned).
care_type: emergency_112 | hospital_visit (urgent, needs a hospital but not an ambulance) | clinic_visit (general physician in person) |
  specialist_visit | diagnostic_test (the user mainly needs a scan or test) | lab_test_home | home_nursing | paramedic_home_visit |
  teleconsultation (user wants video, or a mild question).
specialty: the doctor who should see this, from {list(SPECIALTY_TAGS)} or none.
recommended_tests: the first tests a doctor usually orders for these facts, from {list(TEST_TAGS)}. Keep it short; empty if none.
services_needed: procedures needed, from {list(PROC_TAGS)}.
consult_first: true when a doctor should examine first or the user asked only for a consultation.
status: need_more_info when seriousness depends on details the user has not given. Bleeding: where from, how much, how it
  happened, does it stop with pressure. Injury: how it happened, can they move it, swelling. Pain: where, how bad, since when.
  Fever: age, how high, how long. Ask at most 2 plain questions in missing_info, only when the answer could change seriousness
  or care_type. Once who/how old and since when are known, or a service is named, decide (final). Never ask what is already
  answered or already asked (listed after the facts). If the user wants no more questions, decide with what you have.
Any red flag (uncontrolled bleeding, chest pain, breathlessness, unconsciousness, stroke signs, seizure, poisoning, infant fever,
suicidal thoughts) => emergency_112, seriousness emergency, status final.

Examples (facts -> decision):
{{"symptoms_present":["burning urination","frequent urination"],"duration":"3 days"}} -> {{"status":"final","seriousness":"soon","care_type":"specialist_visit","specialty":"urology","recommended_tests":["urine_test","urine_culture"],"services_needed":[],"consult_first":false,"red_flags":[],"missing_info":[],"reasoning":"Urinary burning for 3 days; a urine test guides the doctor."}}
{{"services_requested":["MRI of knee"]}} -> {{"status":"final","seriousness":"routine","care_type":"diagnostic_test","specialty":"orthopedics","recommended_tests":["mri"],"services_needed":[],"consult_first":false,"red_flags":[],"missing_info":[],"reasoning":"Scan requested by name."}}
{{"symptoms_present":["bleeding"]}} -> {{"status":"need_more_info","seriousness":"urgent","care_type":"clinic_visit","specialty":"general_medicine","recommended_tests":[],"services_needed":[],"consult_first":true,"red_flags":[],"missing_info":["Where is the bleeding from, and does it stop when you press on it?","How did it happen?"],"reasoning":"How serious bleeding is depends on its source, amount and cause."}}
{{"symptoms_present":["small cut on finger"],"existing_conditions":["kitchen knife injury"],"severity":"mild"}} -> {{"status":"final","seriousness":"soon","care_type":"clinic_visit","specialty":"general_medicine","recommended_tests":[],"services_needed":["wound_dressing","tetanus_shot"],"consult_first":false,"red_flags":[],"missing_info":[],"reasoning":"Minor cut that stopped bleeding; needs cleaning, dressing and a tetanus shot."}}
{{"symptoms_present":["deep cut on hand","bleeding slowed with pressure"]}} -> {{"status":"final","seriousness":"urgent","care_type":"hospital_visit","specialty":"general_medicine","recommended_tests":[],"services_needed":["stitches","tetanus_shot"],"consult_first":false,"red_flags":[],"missing_info":[],"reasoning":"Deep cut likely needs stitches today."}}
{{"symptoms_present":["dog bite on leg"]}} -> {{"status":"final","seriousness":"urgent","care_type":"clinic_visit","specialty":"general_medicine","recommended_tests":[],"services_needed":["anti_rabies","wound_dressing"],"consult_first":false,"red_flags":[],"missing_info":[],"reasoning":"Animal bite needs wound care and anti-rabies vaccine today."}}
{{"symptoms_present":["knee pain"],"duration":"3 months"}} -> {{"status":"final","seriousness":"routine","care_type":"specialist_visit","specialty":"orthopedics","recommended_tests":["xray"],"services_needed":[],"consult_first":false,"red_flags":[],"missing_info":[],"reasoning":"Long-standing knee pain; an orthopaedic review with an X-ray."}}
{{"symptoms_present":["cough"],"services_requested":["consultation"],"preferences":["consult_only"]}} -> {{"status":"final","seriousness":"soon","care_type":"clinic_visit","specialty":"general_medicine","recommended_tests":[],"services_needed":[],"consult_first":true,"red_flags":[],"missing_info":[],"reasoning":"User wants a doctor's consultation before any tests."}}
{{"services_requested":["tetanus shot"]}} -> {{"status":"final","seriousness":"soon","care_type":"clinic_visit","specialty":"general_medicine","recommended_tests":[],"services_needed":["tetanus_shot"],"consult_first":false,"red_flags":[],"missing_info":[],"reasoning":"Routine tetanus shot requested."}}
{{"symptoms_present":["severe chest pain","sweating"]}} -> {{"status":"final","seriousness":"emergency","care_type":"emergency_112","specialty":"cardiology","recommended_tests":[],"services_needed":[],"consult_first":false,"red_flags":["chest pain"],"missing_info":[],"reasoning":"Red-flag symptoms."}}"""

RANK = {s: i for i, s in enumerate(SERIOUSNESS)}


def _payload(facts, hints: Hints, asked: list[str] = ()) -> str:
    d = json.loads(intake.facts_for_models(facts))
    if hints.preferences:
        d["preferences"] = hints.preferences
    h = {k: v for k, v in hints.as_dict().items() if k != "preferences"}
    out = f"Patient facts: {json.dumps(d, ensure_ascii=False)}\nKeyword hints: {json.dumps(h)}"
    if asked:
        out += ("\nQuestions already asked (a detail asked but still missing means the user did not say: do not ask it again): "
                + json.dumps(list(asked), ensure_ascii=False))
    if facts.no_more_questions:
        out += "\nThe user wants no more questions: decide now."
    if g := protocols.guide(facts):
        out += "\n" + g
    return out


def _safe(fn):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return None


def classify(facts, hints: Hints, asked: list[str] = ()) -> tuple[Optional[SmartDecision], dict]:
    user = _payload(facts, hints, asked)
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_med = ex.submit(llm.medgemma, SYSTEM, user, SmartDecision) if llm.CFG["smart"].get("medgemma_brain", True) else None
        f_qwen = ex.submit(lambda: _safe(lambda: llm.chat(SYSTEM, user, SmartDecision)))
        med, qwen = (f_med.result() if f_med else None), f_qwen.result()
    votes = {"medgemma": med, "qwen": qwen}
    ds = [d for d in (med, qwen) if d]
    if not ds:
        return None, votes
    emergency = next((d for d in ds if d.care_type == "emergency_112" or d.seriousness == "emergency"), None)
    if emergency:
        return emergency.model_copy(update={"care_type": "emergency_112", "seriousness": "emergency", "status": "final"}), votes
    # conversation flow from Qwen (asks fewer needless questions); medical content from MedGemma when it answered
    talk = qwen or med
    brain = med or qwen
    worst = max(ds, key=lambda d: RANK[d.seriousness])
    merged = brain.model_copy(update={
        "status": talk.status,
        "missing_info": talk.missing_info or brain.missing_info,
        "seriousness": worst.seriousness,
        "care_type": worst.care_type if RANK[worst.seriousness] > RANK[brain.seriousness] else brain.care_type,
        "recommended_tests": list(dict.fromkeys(brain.recommended_tests or (qwen.recommended_tests if qwen else [])))[:4],
        "services_needed": list(dict.fromkeys(brain.services_needed + (qwen.services_needed if qwen else [])))[:3],
        "consult_first": brain.consult_first or bool(qwen and qwen.consult_first),
        "reasoning": talk.reasoning,
    })
    return merged, votes


def fallback(hints: Hints) -> SmartDecision:
    """No model answered: route from keywords alone."""
    tests = [t for t in hints.services if t in TEST_TAGS] or hints.suggested_tests
    procs = [s for s in hints.services + hints.suggested_services if s in PROC_TAGS]
    spec = hints.specialties[0] if hints.specialties else "general_medicine"
    care = "diagnostic_test" if tests and not procs and not hints.specialties else ("specialist_visit" if spec != "general_medicine" else "clinic_visit")
    if "home" in hints.preferences:
        care = "home_nursing"
    if "online" in hints.preferences:
        care = "teleconsultation"
    return SmartDecision(status="final", seriousness="soon", care_type=care, specialty=spec, recommended_tests=tests[:4],
                         services_needed=procs[:3], consult_first="consult_only" in hints.preferences, red_flags=[], missing_info=[],
                         reasoning="Routed from the words used; models were unavailable.")
