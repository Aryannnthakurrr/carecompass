"""MedGemma emergency screener: one focused question on the structured facts, written as a short clinical note.

Asked to pick one of six care types, MedGemma under-calls emergencies (37 of 77 on held-out cases). Asked only
"does this person need emergency services now?", with the danger signs spelled out and the facts as a clinical note,
the same model caught 73 of 77 (training/medgemma_probe.py, eval/eval_screener.py). It runs beside the classifier and
can only ADD an emergency: a "no" changes nothing, so routing is exactly as before unless it fires.
"""
import logging
from typing import Optional

from pydantic import BaseModel, Field

from app import llm
from app.schemas import CaseFacts

log = logging.getLogger("carecompass.screen")
CFG = llm.MODELS["classifier"].get("screener", {})


class Screen(BaseModel):
    danger_signs: list[str] = Field(max_length=5, description="Findings that could mean a life-threatening problem; empty if none")
    reasoning: str = Field(description="One or two short sentences")
    emergency: bool


SYSTEM = """You are an experienced emergency triage nurse in India. Read the case and decide one thing: does this person
need emergency services (call 112 / go to an emergency department now)?
Missing an emergency is far worse than a false alarm. Answer emergency=true when any finding could reasonably signal a
life-threatening or time-critical problem for THIS patient, considering age (infants, young children, the elderly),
pregnancy and existing illness: for example chest pain or pressure, breathing difficulty, fainting or reduced
responsiveness, new confusion or unusual drowsiness, stroke signs (face drooping, one-sided weakness or numbness, speech
trouble), seizures, heavy or non-stop bleeding, bleeding in pregnancy, high fever in a baby, severe dehydration (not
drinking, not passing urine, cannot stand), head injury with vomiting or drowsiness, poisoning or overdose, severe
allergic swelling, suicidal thoughts, or an elderly or frail person who suddenly cannot get up or respond normally.
An elderly person who is only weak or not eating, with no other warning sign, is NOT an emergency (a home visit); it
becomes one with drowsiness or unusual sleepiness, confusion, inability to get up, not drinking, dizziness or fainting.
Routine requests (wound dressing, injections, catheter care, tests, check-ups) without a warning sign are NOT emergencies.
Answer emergency=false for routine requests and mild, stable complaints. Do not diagnose."""


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
    """-> MedGemma's screen, or None when skipped, Ollama is down or the output is invalid (then nothing changes)."""
    if not wanted(facts):
        return None
    out = llm.medgemma(SYSTEM, f"Case: {note(facts)}", Screen, num_predict=CFG.get("num_predict", 500))
    if out is not None:
        log.info("screen emergency=%s signs=%s", out.emergency, out.danger_signs)
    return out
