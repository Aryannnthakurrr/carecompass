"""Classifier: CaseFacts only -> Decision. MedGemma (local) and Qwen run in parallel; the more urgent answer wins."""
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from app import llm
from app.schemas import CaseFacts, Decision

URGENCY_FOR_CARE = llm.CFG["labels"]["urgency_for_care"]
RANK = {"scheduled": 0, "same_day": 1, "emergency": 2}

SYSTEM = """You are the triage engine of an Indian home-healthcare service. You receive structured facts about a request.
Do not diagnose or prescribe. Choose care_type:
- emergency_112: any red-flag sign (chest pain, breathlessness, unconsciousness or collapse, severe bleeding, stroke signs, seizure,
  high fever in an infant, suicidal thoughts, severe dehydration, poisoning). Route to emergency services, never home care.
- home_nursing: a specific nursing procedure is requested (injection, IV, wound or post-surgery dressing, catheter care, bedsore care,
  nebulization, insulin).
- lab_test_home: a diagnostic test or home sample collection is requested (blood test, sugar test, full body checkup, thyroid, etc.).
- teleconsultation: a non-urgent question a virtual doctor can handle (mild acidity, reports review, second opinion, prescription query).
- paramedic_home_visit: a general complaint or check-up that needs someone to see the patient at home (fever with cough, weakness,
  BP and sugar check, elderly not eating).
status: "need_more_info" ONLY when the request is too vague to choose a care type (no symptom and no specific service, e.g. "I need help",
"doctor chahiye", "book something for my mother"). Then put ONE plain question in missing_info. Otherwise "final".
Any red flag => emergency_112, status final. When unsure between two care types, choose the one that sends help sooner.

Examples (facts -> decision):
{"services_requested":["intramuscular injection"],"setting_requested":"home_visit"} -> {"status":"final","care_type":"home_nursing","red_flags":[],"missing_info":[],"reasoning":"Specific nursing procedure requested at home."}
{"services_requested":["full body checkup"],"setting_requested":"lab_test"} -> {"status":"final","care_type":"lab_test_home","red_flags":[],"missing_info":[],"reasoning":"Diagnostic test with home sample collection requested."}
{"symptoms_present":["fever","cough"],"duration":"2 days","patient":"father"} -> {"status":"final","care_type":"paramedic_home_visit","red_flags":[],"missing_info":[],"reasoning":"Fever with cough for 2 days needs a home assessment."}
{"services_requested":["BP and sugar check"],"patient":"mother","setting_requested":"home_visit"} -> {"status":"final","care_type":"paramedic_home_visit","red_flags":[],"missing_info":[],"reasoning":"Routine vitals check at home by a Medic."}
{"symptoms_present":["mild acidity"],"setting_requested":"video_consult"} -> {"status":"final","care_type":"teleconsultation","red_flags":[],"missing_info":[],"reasoning":"Mild, non-urgent complaint suitable for a virtual doctor."}
{"services_requested":["doctor"],"setting_requested":"unspecified"} -> {"status":"need_more_info","care_type":"teleconsultation","red_flags":[],"missing_info":["Who is this for and what is the main concern or service needed?"],"reasoning":"Request is too vague to choose a care type."}
{"symptoms_present":["severe chest pain","cannot breathe"],"patient":"father"} -> {"status":"final","care_type":"emergency_112","red_flags":["chest pain","breathlessness"],"missing_info":[],"reasoning":"Red-flag symptoms; route to emergency services."}"""


def _facts_json(facts: CaseFacts) -> str:
    d = facts.model_dump(exclude={"detected_language"}, exclude_none=True)
    return json.dumps({k: v for k, v in d.items() if v not in ([], "")}, ensure_ascii=False)


def _rank(d: Decision) -> int:
    return RANK[URGENCY_FOR_CARE[d.care_type]]


def classify(facts: CaseFacts) -> tuple[Optional[Decision], dict]:
    """-> (combined decision or None if both models failed, per-model decisions for logging/eval)."""
    user = f"Patient facts: {_facts_json(facts)}"
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_med = ex.submit(llm.medgemma, SYSTEM, user, Decision)
        f_qwen = ex.submit(lambda: _safe(lambda: llm.chat(SYSTEM, user, Decision)))
        med, qwen = f_med.result(), f_qwen.result()
    votes = {"medgemma": med, "qwen": qwen}
    ds = [d for d in (med, qwen) if d]
    if not ds:
        return None, votes
    if any(d.care_type == "emergency_112" for d in ds):
        return next(d for d in ds if d.care_type == "emergency_112"), votes
    primary = qwen or med  # Qwen asks fewer needless questions (bench: 6/19 vs 9/16)
    if primary.status == "need_more_info":
        return primary, votes
    finals = [d for d in ds if d.status == "final"]
    top = max(_rank(d) for d in finals)
    best = [d for d in finals if _rank(d) == top]
    tie = llm.MODELS["classifier"].get("tie_break", "second_opinion")
    pick = (qwen if qwen in best else best[0]) if tie == "second_opinion" else (med if med in best else best[0])
    return pick, votes


def _safe(fn):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return None
