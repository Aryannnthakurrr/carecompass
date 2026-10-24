"""MedGemma emergency screener: the structured facts, written as a short clinical note, and one focused question.

Asked to pick one of six care types, MedGemma under-calls emergencies (37 of 77 held-out cases). Asked a focused question
about danger, the same model caught 73 of 77 (training/medgemma_probe.py), but a yes/no question framed as "a miss is far
worse" also flagged sprains and ordinary fevers. So it grades instead: emergency_now / see_doctor_today / routine, taught
by worked examples. Only emergency_now is acted on, and even then the pipeline first asks the screener's own confirming
question with a pre-written call-112 line, escalating only if the reported danger is still there after it.
"""
import logging
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app import llm
from app.schemas import CaseFacts

log = logging.getLogger("carecompass.screen")
CFG = llm.MODELS["classifier"].get("screener", {})


class Screen(BaseModel):
    danger_signs: list[str] = Field(max_length=5, description="Reported findings that could mean a life-threatening problem; empty if none")
    reasoning: str = Field(description="One or two short sentences")
    level: Literal["emergency_now", "see_doctor_today", "routine"]
    check_question: str = Field("", description="If emergency_now: one short plain question about the reported danger sign; else empty")

    @property
    def emergency(self) -> bool:
        return self.level == "emergency_now"


SYSTEM = """You are an experienced emergency triage nurse in India. Grade the case:
- emergency_now: a REPORTED finding could be life-threatening or time-critical for this patient right now: chest pain or
  pressure, breathing difficulty, fainting, unresponsive or unusually drowsy/hard to wake, new confusion, stroke signs,
  seizure, heavy or non-stop bleeding, bleeding in pregnancy, fever in a baby under 3 months, severe dehydration (not
  drinking, no urine, cannot stand), head injury with vomiting or drowsiness, poisoning, throat or face swelling, suicidal
  thoughts, an elderly or frail person who suddenly cannot get up, eat or respond normally.
- see_doctor_today: needs a doctor soon but not an ambulance (high fever in a child or adult, a possible fracture, a deep cut,
  an animal bite, vomiting many times).
- routine: can be planned.
When unsure between emergency_now and see_doctor_today for a child under 5, a very old or a pregnant patient, or anyone
showing dehydration (cannot keep water down, not passing urine), choose emergency_now.
Judge only what is reported. Do not diagnose. For emergency_now, check_question asks the family about that reported sign.

Examples:
Case: Patient: self, 30 years. Presenting with: fever. Duration: since yesterday. Reported readings: temperature 101F.
-> {"danger_signs":[],"reasoning":"Fever in an alert adult without warning signs.","level":"see_doctor_today","check_question":""}
Case: Patient: son, 5 years (child). Presenting with: fever. Duration: since yesterday. Reported readings: temperature 102F.
-> {"danger_signs":[],"reasoning":"High fever in a school-age child, no warning signs reported.","level":"see_doctor_today","check_question":""}
Case: Patient: mother, 65 years (elderly). Presenting with: fall; swollen wrist; wrist pain. Duration: 1 hour.
-> {"danger_signs":[],"reasoning":"Wrist injury after a fall; needs an X-ray today, not an ambulance.","level":"see_doctor_today","check_question":""}
Case: Patient: grandfather, 82 years (elderly). Presenting with: excessive sleepiness; not eating. Onset: sudden.
-> {"danger_signs":["sudden excessive sleepiness in an 82-year-old"],"reasoning":"Sudden drowsiness in the very old can be time-critical.","level":"emergency_now","check_question":"Is he hard to wake or not making sense right now?"}
Case: Patient: baby, 2 months (infant). Presenting with: fever. Duration: since morning.
-> {"danger_signs":["fever in a 2-month-old"],"reasoning":"Any fever under 3 months needs emergency care.","level":"emergency_now","check_question":"Does the baby feel hot to touch right now?"}
Case: Patient: wife, 6 months pregnant (pregnant). Presenting with: headache; blurred vision.
-> {"danger_signs":["headache with blurred vision in pregnancy"],"reasoning":"These signs in pregnancy can be time-critical.","level":"emergency_now","check_question":"Is her vision still blurred right now?"}
Case: Patient: daughter, 2 years (child). Presenting with: high fever; not eating since last night; weakness.
-> {"danger_signs":["high fever with weakness and not eating in a 2-year-old"],"reasoning":"A toddler with fever who is weak and not eating may be getting dehydrated.","level":"emergency_now","check_question":"Is she drinking anything and passing urine?"}
Case: Patient: son, 4 years (child). Presenting with: vomiting many times; cannot keep water down. Onset: sudden.
-> {"danger_signs":["cannot keep water down in a 4-year-old"],"reasoning":"Repeated vomiting with no fluids kept down risks dehydration in a young child.","level":"emergency_now","check_question":"Has he passed urine in the last 6 hours?"}
Case: Patient: wife. Presenting with: heavy menstrual bleeding; dizziness; pale face.
-> {"danger_signs":["heavy bleeding with dizziness and paleness"],"reasoning":"Heavy bleeding with dizziness can mean dangerous blood loss.","level":"emergency_now","check_question":"Is she dizzy or faint right now?"}
Case: Patient: father, 68 years (elderly). Presenting with: breathless when climbing stairs. Onset: gradual. Duration: 4 months.
-> {"danger_signs":[],"reasoning":"Slowly worsening breathlessness on exertion over months needs a doctor soon, not an ambulance.","level":"see_doctor_today","check_question":""}
Case: Patient: self. Presenting with: mild acidity. Duration: since morning.
-> {"danger_signs":[],"reasoning":"Mild, stable complaint.","level":"routine","check_question":""}"""


def note(facts: CaseFacts) -> str:
    """The structured facts as a short clinical note (MedGemma reads medical prose better than JSON keys)."""
    f = facts
    pop = ", ".join(f.special_population)
    parts = [f"Patient: {f.patient or 'not stated'}{f' ({pop})' if pop else ''}."]
    if f.symptoms_present:
        parts.append("Presenting with: " + "; ".join(f.symptoms_present) + ".")
    for value, label in ((f.onset, "Onset"), (f.duration, "Duration"), (f.severity, "Severity as described")):
        if value:
            parts.append(f"{label}: {value}.")
    if f.symptoms_denied:
        parts.append("Denies: " + "; ".join(f.symptoms_denied) + ".")
    if f.existing_conditions:
        parts.append("Background: " + "; ".join(f.existing_conditions) + ".")
    if f.vitals_reported:
        parts.append("Reported readings: " + "; ".join(f.vitals_reported) + ".")
    if f.services_requested:
        parts.append("Asking for: " + "; ".join(f.services_requested) + ".")
    if f.setting_requested not in (None, "unspecified"):
        parts.append(f"Setting requested: {f.setting_requested.replace('_', ' ')}.")
    return " ".join(parts)


def wanted(facts: CaseFacts) -> bool:
    """Screen only when there is something to screen: a request with no symptom (an injection, a test) is skipped."""
    return bool(CFG.get("enabled", True)) and bool(facts.symptoms_present)


def screen(facts: CaseFacts) -> Optional[Screen]:
    """-> MedGemma's grade, or None when skipped, Ollama is down or the output is invalid (then nothing changes)."""
    if not wanted(facts):
        return None
    prompt = f"Case: {note(facts)}"
    if CFG.get("retrieval", True):
        from app import retrieval
        near = retrieval.similar(facts, CFG.get("retrieve_k", 4))
        if near:
            shown = "\n".join(f"- {n} -> {g}" for n, g in near)
            prompt = f"Similar past cases and how they were graded (judge THIS case on its own findings):\n{shown}\n\n{prompt}"
    out = llm.medgemma(SYSTEM, prompt, Screen, num_predict=CFG.get("num_predict", 500))
    if out is not None:
        log.info("screen level=%s signs=%s", out.level, out.danger_signs)
    return out
