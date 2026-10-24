"""Is MedGemma held back by how we ask it, not by what it knows? Local Ollama only, no paid APIs.

Variants, on the test split's emergencies + a fixed sample of non-emergencies:
  json_decide    facts JSON in, decision first (production today, few-shot prompt)
  json_think     facts JSON in, reasoning BEFORE the decision
  note_think     the same facts written as a short clinical note (MedGemma's home turf), reasoning first
  note_screen    clinical note in, one narrow question: is this an emergency? danger signs first, recall over precision

Usage: python training/medgemma_probe.py [--variants json_think,note_screen] [--non 60] [--model <ollama tag>]
Writes eval/results/medgemma_probe.json.
"""
import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import llm, triage  # noqa: E402
from app.schemas import Decision  # noqa: E402

NEED = "need_more_info"
CARE = Literal[tuple(llm.CFG["labels"]["care_types"])]


class ThinkDecision(BaseModel):
    """Decision with the reasoning first, so the model thinks before it commits."""
    reasoning: str = Field(description="Go through each finding: could it be dangerous in this patient? Then decide. 2-4 sentences.")
    red_flags: list[str] = Field(max_length=4)
    care_type: CARE
    status: Literal["final", "need_more_info"]
    missing_info: list[str] = Field(max_length=2)


class Screen(BaseModel):
    danger_signs: list[str] = Field(max_length=5, description="Findings that could mean a life-threatening problem; empty if none")
    reasoning: str = Field(description="1-3 sentences")
    emergency: bool


SCREEN_SYSTEM = """You are an experienced emergency triage nurse in India. Read the case and decide one thing: does this person
need emergency services (call 112 / go to an emergency department now)?
Missing an emergency is far worse than a false alarm. Answer emergency=true when any finding could reasonably signal a
life-threatening or time-critical problem for THIS patient, considering age (infants, young children, the elderly),
pregnancy and existing illness: for example chest pain or pressure, breathing difficulty, fainting or reduced
responsiveness, new confusion or unusual drowsiness, stroke signs (face drooping, one-sided weakness or numbness, speech
trouble), seizures, heavy or non-stop bleeding, bleeding in pregnancy, high fever in a baby, severe dehydration (not
drinking, not passing urine, cannot stand), head injury with vomiting or drowsiness, poisoning or overdose, severe
allergic swelling, suicidal thoughts, or an elderly or frail person who suddenly cannot get up, eat or respond normally.
Answer emergency=false for routine requests and mild, stable complaints. Do not diagnose."""

THINK_RULES = triage.RULES + """
Missing an emergency is far worse than a false alarm: if a finding could signal a time-critical problem for this patient
(consider age, pregnancy, frailty), choose emergency_112."""


def note(facts: dict) -> str:
    """The structured facts as a short clinical note."""
    parts = []
    who = facts.get("patient") or "not stated"
    pop = ", ".join(facts.get("special_population", []))
    parts.append(f"Patient: {who}{f' ({pop})' if pop else ''}.")
    if facts.get("symptoms_present"):
        parts.append("Presenting with: " + "; ".join(facts["symptoms_present"]) + ".")
    for key, label in (("onset", "Onset"), ("duration", "Duration"), ("severity", "Severity as described")):
        if facts.get(key):
            parts.append(f"{label}: {facts[key]}.")
    if facts.get("symptoms_denied"):
        parts.append("Denies: " + "; ".join(facts["symptoms_denied"]) + ".")
    if facts.get("existing_conditions"):
        parts.append("Background: " + "; ".join(facts["existing_conditions"]) + ".")
    if facts.get("vitals_reported"):
        parts.append("Reported readings: " + "; ".join(facts["vitals_reported"]) + ".")
    if facts.get("services_requested"):
        parts.append("Asking for: " + "; ".join(facts["services_requested"]) + ".")
    if facts.get("setting_requested") not in (None, "unspecified"):
        parts.append(f"Setting requested: {facts['setting_requested'].replace('_', ' ')}.")
    if len(parts) == 1:
        parts.append("No symptoms or specific service mentioned.")
    return " ".join(parts)


def ask(model, system, user, cls, num_predict=500):
    body = {"model": model, "stream": False, "keep_alive": -1, "format": cls.model_json_schema(),
            "options": {"temperature": 0, "num_predict": num_predict},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    t0 = time.perf_counter()
    try:
        r = httpx.post("http://localhost:11434/api/chat", json=body, timeout=120)
        r.raise_for_status()
        return cls.model_validate(llm._parse(r.json()["message"]["content"])), time.perf_counter() - t0
    except Exception:  # noqa: BLE001
        return None, time.perf_counter() - t0


def run_variant(name, rows, model):
    out = []
    for i, r in enumerate(rows):
        facts_json = r["messages"][1]["content"][len("Patient facts: "):]
        facts = json.loads(facts_json)
        if name == "json_decide":
            d, t = ask(model, triage.SYSTEM, f"Patient facts: {facts_json}", Decision, 400)
            pred = None if d is None else NEED if d.status == NEED else d.care_type
            em = pred == "emergency_112"
        elif name == "json_think":
            d, t = ask(model, THINK_RULES, f"Patient facts: {facts_json}", ThinkDecision)
            pred = None if d is None else NEED if d.status == NEED else d.care_type
            em = pred == "emergency_112"
        elif name == "note_think":
            d, t = ask(model, THINK_RULES, f"Case: {note(facts)}", ThinkDecision)
            pred = None if d is None else NEED if d.status == NEED else d.care_type
            em = pred == "emergency_112"
        else:  # note_screen
            d, t = ask(model, SCREEN_SYSTEM, f"Case: {note(facts)}", Screen, 300)
            pred = None if d is None else ("emergency_112" if d.emergency else "not_emergency")
            em = bool(d and d.emergency)
        out.append({"id": r["id"], "gold": r["target"], "pred": pred, "emergency": em, "s": round(t, 2),
                    "why": d.reasoning if d is not None and hasattr(d, "reasoning") else None})
        if i % 20 == 0:
            print(f"  {name} {i}/{len(rows)}", flush=True)
    return out


def summarise(res):
    em = [x for x in res if x["gold"] == "emergency_112"]
    non = [x for x in res if x["gold"] != "emergency_112"]
    s = {"emergencies_caught": f"{sum(x['emergency'] for x in em)}/{len(em)}",
         "false_emergencies": f"{sum(x['emergency'] for x in non)}/{len(non)}",
         "invalid": sum(x["pred"] is None for x in res),
         "median_s": round(statistics.median(x["s"] for x in res[1:]), 2)}
    if any(x["pred"] not in ("emergency_112", "not_emergency", None) for x in res):
        s["care_accuracy"] = round(sum(x["pred"] == x["gold"] for x in res) / len(res), 3)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="json_decide,json_think,note_think,note_screen")
    ap.add_argument("--non", type=int, default=60, help="non-emergency rows to sample")
    ap.add_argument("--model", default=llm.MODELS["classifier"]["model"])
    a = ap.parse_args()
    test = [json.loads(l) for l in (ROOT / "training/sft/test.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    em = [r for r in test if r["target"] == "emergency_112"]
    non = random.Random(7).sample([r for r in test if r["target"] != "emergency_112"], a.non)
    rows = em + non
    out_path = ROOT / "eval/results/medgemma_probe.json"
    report = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    for v in a.variants.split(","):
        res = run_variant(v, rows, a.model)
        report[v] = {"summary": summarise(res), "rows": res}
        print(v, json.dumps(report[v]["summary"]), flush=True)
        out_path.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print("\n".join(f"{k:12} {json.dumps(v['summary'])}" for k, v in report.items()))


if __name__ == "__main__":
    main()
