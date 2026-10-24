"""Benchmark the intake / translation / triage options on eval/intake_cases.jsonl.

Usage:
  python eval/bench_pipeline.py structure   # LLM structuring: raw text -> intake JSON
  python eval/bench_pipeline.py translate   # translation only: raw text -> English
  python eval/bench_pipeline.py voice       # TTS audio -> Saaras transcribe vs translate
  python eval/bench_pipeline.py classify    # zero-shot care_type/urgency (labels are ours, not doctors')

Results are written to eval/results/<part>.json.
"""
import base64
import json
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Literal, Optional

import httpx
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "smoke_tests"))
from _common import CFG, require_env  # noqa: E402

from google import genai  # noqa: E402
from google.genai import errors, types  # noqa: E402
from sarvamai import SarvamAI  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)
CASES = [json.loads(l) for l in (HERE / "intake_cases.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]

gem = genai.Client(api_key=require_env("GEMINI_API_KEY"))
sarvam = SarvamAI(api_subscription_key=require_env("SARVAM_API_KEY"))
OLLAMA = "http://localhost:11434"
MEDGEMMA = CFG["models"]["classifier"]["model"]


# ---------------------------------------------------------------- schemas / prompts

class Intake(BaseModel):
    symptoms_en: list[str] = Field(description="Symptoms the patient HAS, as short English clinical phrases. Exclude denied symptoms.")
    negated_symptoms_en: list[str] = Field(description="Symptoms the patient explicitly says are ABSENT, in English.")
    duration: Optional[str] = Field(description="How long, in English, or null")
    patient_age: Optional[str] = Field(description="Patient age as stated, e.g. '62 years', '2 months', or null")
    severity: Optional[Literal["mild", "moderate", "severe"]]
    missing_slots: list[str] = Field(description="Important info still missing, e.g. patient_age, duration")
    follow_up_question: Optional[str] = Field(description="ONE short question in the patient's language AND script, or null if enough info")
    detected_language: Literal["en", "hi", "hinglish"] = Field(description="hi = Devanagari Hindi, hinglish = romanised Hindi/English mix")


class Triage(BaseModel):
    care_type: Literal["paramedic_home_visit", "nurse", "gp_teleconsult", "specialist", "emergency"]
    urgency: Literal["routine", "same_day", "immediate"]


INTAKE_SYS = """You are a medical intake assistant for an Indian home-healthcare app. Never diagnose or prescribe.
Read the patient's message (English, Hindi in Devanagari, or romanised Hinglish) and fill the JSON fields.
Translate symptoms into short English clinical phrases. Put symptoms the patient denies ("bukhar nahi", "no vomiting") ONLY in negated_symptoms_en."""

TRIAGE_SYS = """You route patients for an Indian home-healthcare app. Do not diagnose. Choose:
care_type:
- emergency: life-threatening or red-flag signs (chest pain, breathing difficulty, stroke signs, seizure, unconsciousness, heavy bleeding, poisoning, bleeding in pregnancy, fever in infant under 3 months, suicidal thoughts)
- paramedic_home_visit: needs an in-person check of vitals at home (e.g. fever in elderly, vomiting with dizziness, high fever in a child)
- nurse: care tasks or procedures (wound dressing, injections, bedside care, wound care)
- gp_teleconsult: mild/moderate symptoms a doctor can handle remotely
- specialist: long-standing or chronic problems needing a specialist
urgency: immediate (emergency), same_day (should be seen today), routine (can wait days).
When unsure, choose the MORE urgent option."""

SCHEMA_HINT = "\nReturn ONLY a JSON object matching this JSON schema:\n"


def jparse(text):
    t = (text or "").strip()
    t = t[t.find("{"): t.rfind("}") + 1]
    return json.loads(t)


def with_retry(fn, tries=4):
    for i in range(tries):
        try:
            return fn()
        except errors.APIError as e:
            if "PerDay" in str(e):
                raise
            if getattr(e, "code", None) in (429, 500, 503) and i < tries - 1:
                time.sleep(5 * (i + 1))
                continue
            raise


# ---------------------------------------------------------------- model callers

def gemini_json(model, system, schema_cls, thinking_level=None, native_schema=True):
    def call(text):
        cfg = dict(temperature=0, response_mime_type="application/json")
        if thinking_level:
            cfg["thinking_config"] = types.ThinkingConfig(thinking_level=thinking_level)
        if native_schema:
            cfg["response_schema"] = schema_cls
            cfg["system_instruction"] = system
            contents = text
        else:
            contents = f"{system}{SCHEMA_HINT}{json.dumps(schema_cls.model_json_schema())}\n\nPatient message: {text}"
        r = with_retry(lambda: gem.models.generate_content(model=model, contents=contents, config=types.GenerateContentConfig(**cfg)))
        return jparse(r.text)
    return call


def sarvam_json(model, system, schema_cls):
    def call(text):
        r = sarvam.chat.completions(
            model=model,
            messages=[
                {"role": "system", "content": f"{system}{SCHEMA_HINT}{json.dumps(schema_cls.model_json_schema())}"},
                {"role": "user", "content": text},
            ],
            temperature=0,
            reasoning_effort="low",
            max_tokens=2000,
        )
        return jparse(r.choices[0].message.content)
    return call


def ollama_json(model, system, schema_cls):
    def call(text):
        r = httpx.post(
            f"{OLLAMA}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}],
                "format": schema_cls.model_json_schema(),
                "stream": False,
                "keep_alive": -1,
                "options": {"temperature": 0},
            },
            timeout=180,
        )
        r.raise_for_status()
        return jparse(r.json()["message"]["content"])
    return call


def warm_ollama():
    httpx.post(f"{OLLAMA}/api/generate", json={"model": MEDGEMMA, "prompt": "hi", "keep_alive": -1, "options": {"num_predict": 1}}, timeout=300)


# ---------------------------------------------------------------- scoring helpers

def matches(pred: str, group: list[str]) -> bool:
    p = pred.lower().strip()
    return any(s in p or fuzz.token_set_ratio(p, s) >= 85 for s in group)


def in_text(text: str, group: list[str]) -> bool:
    t = text.lower()
    return any(s in t or fuzz.partial_ratio(s, t) >= 88 for s in group)


NEG_CUES = re.compile(r"\b(no|not|without|denies|neither|nor|isn't|is not|doesn't|don't|none|absence of)\b")


def negated_in_text(text: str, group: list[str]) -> bool:
    t = text.lower()
    for s in group:
        i = t.find(s)
        if i >= 0 and NEG_CUES.search(t[max(0, i - 40): i]):
            return True
    return False


def script_of(s: str) -> str:
    dev = sum("ऀ" <= ch <= "ॿ" for ch in s)
    lat = sum(ch.isascii() and ch.isalpha() for ch in s)
    return "deva" if dev > lat else "latin"


def lat_stats(ts):
    if not ts:
        return {}
    ts = sorted(ts)
    return {"median_s": round(statistics.median(ts), 2), "p90_s": round(ts[int(0.9 * (len(ts) - 1))], 2), "max_s": round(ts[-1], 2)}


def timed(fn, *a):
    t0 = time.perf_counter()
    out = fn(*a)
    return out, time.perf_counter() - t0


# ---------------------------------------------------------------- part 1: structuring

def part_structure():
    cands = {
        "gemini-3.5-flash-lite": gemini_json("gemini-3.5-flash-lite", INTAKE_SYS, Intake, "minimal"),
        "gemma-4-26b-a4b": gemini_json("gemma-4-26b-a4b-it", INTAKE_SYS, Intake, "minimal", native_schema=False),
        "sarvam-105b": sarvam_json("sarvam-105b", INTAKE_SYS, Intake),
        "medgemma-4b(local)": ollama_json(MEDGEMMA, INTAKE_SYS, Intake),
    }
    warm_ollama()
    report = {}
    for name, fn in cands.items():
        print(f"\n=== {name}")
        rows, times = [], []
        for c in CASES:
            try:
                out, dt = timed(fn, c["text"])
                Intake.model_validate(out)
                times.append(dt)
            except Exception as e:
                print(f"   {c['id']}: ERROR {str(e)[:120]}")
                rows.append({"id": c["id"], "valid": False})
                continue
            pres = [s for s in out.get("symptoms_en", []) if isinstance(s, str)]
            negs = [s for s in out.get("negated_symptoms_en", []) if isinstance(s, str)]
            hit = sum(any(matches(p, g) for p in pres) for g in c["present"])
            neg_leak = sum(any(matches(p, g) for p in pres) for g in c["negated"])
            neg_caught = sum(any(matches(n, g) for n in negs) for g in c["negated"])
            extra = sum(not any(matches(p, g) for g in c["present"] + c["negated"]) for p in pres)
            redflag_kept = hit == len(c["present"]) if c["red_flag"] else None
            age_ok = (any(a in (out.get("patient_age") or "").lower() for a in c["age"])) if c["age"] else None
            fu = out.get("follow_up_question")
            want_script = "deva" if c["lang"] == "hi" else "latin"
            fu_ok = (script_of(fu) == want_script) if fu else None
            row = dict(id=c["id"], valid=True, groups=len(c["present"]), hit=hit, extra=extra, n_pred=len(pres),
                       neg_total=len(c["negated"]), neg_leak=neg_leak, neg_caught=neg_caught,
                       redflag_kept=redflag_kept, age_ok=age_ok, lang_ok=out.get("detected_language") == c["lang"],
                       fu_script_ok=fu_ok, latency=round(dt, 2), out=out)
            rows.append(row)
            flag = "" if hit == len(c["present"]) and not neg_leak else "  <-- miss"
            print(f"   {c['id']:<4}{dt:5.1f}s  {pres}  neg={negs}{flag}")
        ok = [r for r in rows if r["valid"]]
        g = sum(r["groups"] for r in ok)
        summary = {
            "valid_json": f"{len(ok)}/{len(rows)}",
            "symptom_recall": round(sum(r["hit"] for r in ok) / max(g, 1), 3),
            "unmatched_extra_symptoms": sum(r["extra"] for r in ok),
            "negation_leaks": f"{sum(r['neg_leak'] for r in ok)}/{sum(r['neg_total'] for r in ok)}",
            "negations_captured": f"{sum(r['neg_caught'] for r in ok)}/{sum(r['neg_total'] for r in ok)}",
            "red_flag_cases_fully_kept": f"{sum(bool(r['redflag_kept']) for r in ok if r['redflag_kept'] is not None)}/{sum(c['red_flag'] for c in CASES)}",
            "age_correct": f"{sum(bool(r['age_ok']) for r in ok if r['age_ok'] is not None)}/{sum(bool(c['age']) for c in CASES)}",
            "language_correct": f"{sum(r['lang_ok'] for r in ok)}/{len(CASES)}",
            "follow_up_right_script": f"{sum(bool(r['fu_script_ok']) for r in ok if r['fu_script_ok'] is not None)}/{sum(r['fu_script_ok'] is not None for r in ok)}",
            **lat_stats(times),
        }
        print("   SUMMARY", json.dumps(summary))
        report[name] = {"summary": summary, "rows": rows}
    return report


# ---------------------------------------------------------------- part 2: translation only

def part_translate():
    def sarvam_tr(model):
        def call(text):
            kw = dict(input=text, source_language_code="auto", target_language_code="en-IN", model=model)
            if model == "mayura:v1":
                kw["mode"] = "formal"
            return sarvam.text.translate(**kw).translated_text
        return call

    def llm_tr(model, level):
        def call(text):
            r = with_retry(lambda: gem.models.generate_content(
                model=model,
                contents=f"Translate this patient message to plain English. Output only the translation.\n\n{text}",
                config=types.GenerateContentConfig(temperature=0, thinking_config=types.ThinkingConfig(thinking_level=level)),
            ))
            return r.text.strip()
        return call

    cands = {
        "sarvam mayura:v1": sarvam_tr("mayura:v1"),
        "sarvam-translate:v1": sarvam_tr("sarvam-translate:v1"),
        "gemini-3.5-flash-lite": llm_tr("gemini-3.5-flash-lite", "minimal"),
    }
    cases = [c for c in CASES if c["lang"] != "en"]
    report = {}
    for name, fn in cands.items():
        print(f"\n=== {name}")
        rows, times = [], []
        for c in cases:
            try:
                out, dt = timed(fn, c["text"])
                times.append(dt)
            except Exception as e:
                print(f"   {c['id']}: ERROR {str(e)[:150]}")
                rows.append({"id": c["id"], "ok": False})
                continue
            hit = sum(in_text(out, g) for g in c["present"])
            neg_ok = sum(negated_in_text(out, g) for g in c["negated"])
            rows.append(dict(id=c["id"], ok=True, groups=len(c["present"]), hit=hit, neg_total=len(c["negated"]),
                             neg_ok=neg_ok, red_flag=c["red_flag"], latency=round(dt, 2), out=out))
            print(f"   {c['id']:<4}{dt:5.1f}s  {out}")
        ok = [r for r in rows if r["ok"]]
        summary = {
            "ok": f"{len(ok)}/{len(rows)}",
            "symptom_terms_found(keyword)": round(sum(r["hit"] for r in ok) / max(sum(r["groups"] for r in ok), 1), 3),
            "negations_preserved(keyword)": f"{sum(r['neg_ok'] for r in ok)}/{sum(r['neg_total'] for r in ok)}",
            **lat_stats(times),
        }
        print("   SUMMARY", json.dumps(summary))
        report[name] = {"summary": summary, "rows": rows}
    return report


# ---------------------------------------------------------------- part 3: voice

def part_voice():
    tts = CFG["models"]["tts"]
    audio_dir = RESULTS / "audio"
    audio_dir.mkdir(exist_ok=True)
    cases = [c for c in CASES if c["lang"] != "en"]
    rows = []
    for c in cases:
        wav = audio_dir / f"{c['id']}.wav"
        if not wav.exists():
            r = sarvam.text_to_speech.convert(text=c["text"], language_code="hi-IN", speaker=tts["speaker"], model=tts["model"])
            wav.write_bytes(base64.b64decode("".join(r.audios)))
        row = {"id": c["id"], "text": c["text"]}
        for mode in ("transcribe", "codemix", "translate"):
            with wav.open("rb") as f:
                r, dt = timed(lambda: sarvam.speech_to_text.transcribe(file=f, model="saaras:v4", mode=mode, language_code="unknown"))
            row[mode] = {"text": r.transcript, "latency": round(dt, 2), "lang": r.language_code}
        tr = row["translate"]["text"]
        row["translate_hit"] = sum(in_text(tr, g) for g in c["present"])
        row["translate_groups"] = len(c["present"])
        row["translate_neg_ok"] = sum(negated_in_text(tr, g) for g in c["negated"])
        row["neg_total"] = len(c["negated"])
        rows.append(row)
        print(f"\n{c['id']}  {c['text']}")
        for mode in ("transcribe", "codemix", "translate"):
            print(f"   {mode:<10}{row[mode]['latency']:5.2f}s  {row[mode]['text']}")
    summary = {
        "translate_symptom_terms_found(keyword)": round(sum(r["translate_hit"] for r in rows) / sum(r["translate_groups"] for r in rows), 3),
        "translate_negations_preserved": f"{sum(r['translate_neg_ok'] for r in rows)}/{sum(r['neg_total'] for r in rows)}",
        **{f"{m}_latency": lat_stats([r[m]["latency"] for r in rows]) for m in ("transcribe", "codemix", "translate")},
    }
    print("\nSUMMARY", json.dumps(summary, indent=1))
    return {"summary": summary, "rows": rows}


# ---------------------------------------------------------------- part 4: zero-shot classification

def part_classify():
    tr_path = RESULTS / "translate.json"
    english = {}
    if tr_path.exists():
        for r in json.loads(tr_path.read_text(encoding="utf-8"))["sarvam mayura:v1"]["rows"]:
            if r.get("ok"):
                english[r["id"]] = r["out"]
    cands = {
        "gemini-3.5-flash-lite": (gemini_json("gemini-3.5-flash-lite", TRIAGE_SYS, Triage, "minimal"), False),
        "gemma-4-26b-a4b": (gemini_json("gemma-4-26b-a4b-it", TRIAGE_SYS, Triage, "minimal", native_schema=False), False),
        "medgemma-4b raw text": (ollama_json(MEDGEMMA, TRIAGE_SYS, Triage), False),
        "medgemma-4b on English (mayura)": (ollama_json(MEDGEMMA, TRIAGE_SYS, Triage), True),
    }
    warm_ollama()
    order = {"routine": 0, "same_day": 1, "immediate": 2}
    report = {}
    for name, (fn, use_en) in cands.items():
        print(f"\n=== {name}")
        rows, times = [], []
        for c in CASES:
            text = english.get(c["id"], c["text"]) if use_en else c["text"]
            try:
                out, dt = timed(fn, text)
                Triage.model_validate(out)
                times.append(dt)
            except Exception as e:
                print(f"   {c['id']}: ERROR {str(e)[:120]}")
                rows.append({"id": c["id"], "valid": False})
                continue
            rows.append(dict(id=c["id"], valid=True, care_ok=out["care_type"] == c["care_type"], urg_ok=out["urgency"] == c["urgency"],
                             emergency=c["care_type"] == "emergency", pred_emergency=out["care_type"] == "emergency",
                             under=order[out["urgency"]] < order[c["urgency"]], pred=out, latency=round(dt, 2)))
            mark = "" if out["care_type"] == c["care_type"] and out["urgency"] == c["urgency"] else f"  (gold {c['care_type']}/{c['urgency']})"
            print(f"   {c['id']:<4}{dt:5.1f}s  {out['care_type']}/{out['urgency']}{mark}")
        ok = [r for r in rows if r["valid"]]
        em = [r for r in ok if r["emergency"]]
        summary = {
            "valid": f"{len(ok)}/{len(rows)}",
            "care_type_acc": round(sum(r["care_ok"] for r in ok) / max(len(ok), 1), 3),
            "urgency_acc": round(sum(r["urg_ok"] for r in ok) / max(len(ok), 1), 3),
            "emergency_recall": f"{sum(r['pred_emergency'] for r in em)}/{len(em)}",
            "false_emergencies": sum(r["pred_emergency"] and not r["emergency"] for r in ok),
            "under_triaged": sum(r["under"] for r in ok),
            **lat_stats(times),
        }
        print("   SUMMARY", json.dumps(summary))
        report[name] = {"summary": summary, "rows": rows}
    return report


if __name__ == "__main__":
    part = sys.argv[1] if len(sys.argv) > 1 else "structure"
    result = {"structure": part_structure, "translate": part_translate, "voice": part_voice, "classify": part_classify}[part]()
    (RESULTS / f"{part}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved eval/results/{part}.json")
