"""Two-stage architecture benchmark.

Stage 1 (structurer): user's words -> CaseFacts JSON in English (no decisions, no questions).
Stage 2 (classifier): CaseFacts only -> TriageDecision JSON (status, care_type, urgency,
red_flags, missing_info). The structurer later turns missing_info into a question in the
user's language; Bulbul speaks it.

Usage:
  python eval/bench_v2.py structure   # structurer candidates on raw text
  python eval/bench_v2.py classify    # classifier candidates on Flash-Lite's CaseFacts
  python eval/bench_v2.py phrase      # missing_info -> follow-up in user's language/script
"""
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal, Optional

import httpx
from openai import OpenAI
from pydantic import BaseModel, Field
from sklearn.metrics import f1_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_pipeline import CASES, RESULTS, gem, jparse, lat_stats, matches, script_of, with_retry  # noqa: E402
from google.genai import types  # noqa: E402

OLLAMA = "http://localhost:11434"
MEDGEMMA = "hf.co/unsloth/medgemma-4b-it-GGUF:Q4_K_M"
PRINT = threading.Lock()


# ---------------------------------------------------------------- schemas

class CaseFacts(BaseModel):
    symptoms_present: list[str] = Field(description="Symptoms the patient HAS, short English clinical phrases")
    symptoms_denied: list[str] = Field(description="Symptoms the patient explicitly says are absent, English")
    onset: Optional[Literal["sudden", "gradual"]] = Field(description="How it started, if stated")
    duration: Optional[str] = Field(description="How long, in English")
    patient_age: Optional[str] = Field(description="Age as stated, e.g. '62 years', '2 months'")
    special_population: list[Literal["infant", "child", "elderly", "pregnant"]] = Field(description="Only if stated or clearly implied")
    existing_conditions: list[str] = Field(description="Known conditions or circumstances, e.g. diabetes, recent surgery, on BP medication")
    vitals_reported: list[str] = Field(description="Measurements the user gave, e.g. 'temperature 101F', 'blood sugar 350'")
    services_requested: list[str] = Field(description="Care tasks asked for, e.g. wound dressing, injection, bedside nursing care")
    severity: Optional[Literal["mild", "moderate", "severe"]]
    detected_language: Literal["en", "hi", "hinglish"] = Field(description="hi = Devanagari Hindi, hinglish = romanised Hindi/English")


class TriageDecision(BaseModel):
    status: Literal["final", "need_more_info"]
    care_type: Literal["paramedic_home_visit", "nurse", "gp_teleconsult", "specialist", "emergency"]
    urgency: Literal["routine", "same_day", "immediate"]
    red_flags: list[str] = Field(max_length=4, description="Red-flag findings present, empty if none")
    missing_info: list[str] = Field(max_length=2, description="At most 2 facts (English) whose answer could change care_type or urgency; empty if final")
    reasoning: str = Field(description="One sentence, no diagnosis, no medication")


STRUCT_SYS = """You turn a patient's message (English, Devanagari Hindi, or romanised Hinglish) into facts for a triage system.
Record only what the user said; never guess, diagnose or decide urgency. Translate everything to short English phrases.
Symptoms the user denies ("bukhar nahi", "no vomiting") go ONLY in symptoms_denied. Care tasks (dressing, injection,
nursing care) go in services_requested, not symptoms."""

CLASSIFY_SYS = """You are the triage engine of an Indian home-healthcare app. You receive structured facts about a patient.
Do not diagnose or prescribe. Decide:
care_type:
- emergency: life-threatening or red-flag signs (chest pain or tightness, breathing difficulty, blue lips, stroke signs such as facial droop, one-sided weakness or slurred speech, seizure, unconsciousness, heavy bleeding, poisoning, bleeding in pregnancy, fever in an infant under 3 months, suicidal thoughts)
- paramedic_home_visit: needs an in-person check of vitals at home (e.g. fever in the elderly, vomiting with dizziness, high fever in a child)
- nurse: care tasks or procedures (wound dressing, injections, bedside care, non-healing wounds)
- gp_teleconsult: mild/moderate symptoms a doctor can handle remotely
- specialist: long-standing or chronic problems needing a specialist
urgency: immediate (emergency), same_day (should be seen today), routine (can wait days).
When unsure between two options, choose the MORE urgent one.
status: "final" if you can decide; "need_more_info" only if the answer to at most 2 questions could change care_type or
urgency, listed in missing_info. Any red flag => care_type emergency, urgency immediate, status final.
Always fill care_type and urgency with your best current decision."""

PHRASE_SYS = """You are the voice of a home-healthcare app talking to a patient or family member.
Write ONE short, kind follow-up question (max 20 words) that asks for the missing facts in plain everyday words.
Write it in the reply language you are given, exactly. No diagnosis, no advice. Output only the question text."""

REPLY_LANG = {"en": "English", "hi": "Hindi in Devanagari script", "hinglish": "Hinglish: romanised Hindi in Latin script, like the user writes"}


# ---------------------------------------------------------------- generic callers

def schema_prompt(cls):
    return f"\nReturn ONLY a JSON object matching this JSON schema:\n{json.dumps(cls.model_json_schema())}"


class OAICompat:
    """OpenAI-compatible endpoint with per-model fallbacks for JSON mode / reasoning params."""

    def __init__(self, base_url, key_env, model, extra_variants):
        self.client = OpenAI(base_url=base_url, api_key=os.environ[key_env], timeout=120, max_retries=2)
        self.model = model
        self.variants = extra_variants + [{"response_format": {"type": "json_object"}}, {}]
        self.ok = None
        self.ok_text = None

    def __call__(self, system, user, cls):
        msgs = [{"role": "system", "content": system + (schema_prompt(cls) if cls else "")}, {"role": "user", "content": user}]
        attr = "ok" if cls else "ok_text"
        variants = self.variants if cls else [{k: x for k, x in v.items() if k != "response_format"} for v in self.variants]
        cached = getattr(self, attr)
        tries = [cached] if cached is not None else variants
        last = None
        for v in tries:
            try:
                r = self.client.chat.completions.create(model=self.model, messages=msgs, **v)
                setattr(self, attr, v)
                text = r.choices[0].message.content
                return jparse(text) if cls else text.strip()
            except Exception as e:  # noqa: BLE001
                last = e
                if cached is not None:
                    raise
        raise last


def gemini(model, level="minimal", native=True):
    def call(system, user, cls):
        cfg = dict(temperature=0, thinking_config=types.ThinkingConfig(thinking_level=level))
        if cls is None:
            cfg["system_instruction"] = system
            return with_retry(lambda: gem.models.generate_content(model=model, contents=user, config=types.GenerateContentConfig(**cfg))).text.strip()
        cfg["response_mime_type"] = "application/json"
        if native:
            cfg.update(system_instruction=system, response_schema=cls)
            contents = user
        else:
            contents = f"{system}{schema_prompt(cls)}\n\n{user}"
        return jparse(with_retry(lambda: gem.models.generate_content(model=model, contents=contents, config=types.GenerateContentConfig(**cfg))).text)
    return call


def ollama(model):
    def call(system, user, cls):
        body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "stream": False, "keep_alive": -1, "options": {"temperature": 0}}
        if cls:
            body["format"] = cls.model_json_schema()
        body["options"]["num_predict"] = 600
        r = httpx.post(f"{OLLAMA}/api/chat", json=body, timeout=180)
        r.raise_for_status()
        text = r.json()["message"]["content"]
        return jparse(text) if cls else text.strip()
    return call


T0 = [{"temperature": 0, "response_format": {"type": "json_object"}}, {"temperature": 0}]
CEREBRAS, SAMBA = "https://api.cerebras.ai/v1", "https://api.sambanova.ai/v1"


def key_works(base_url, key_env, model):
    try:
        OpenAI(base_url=base_url, api_key=os.environ.get(key_env, "x"), timeout=30, max_retries=0).chat.completions.create(
            model=model, messages=[{"role": "user", "content": "ok"}], max_tokens=1)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"skipping {key_env} models: {str(e)[:80]}")
        return False


SAMBA_OK = None


def candidates():
    global SAMBA_OK
    if SAMBA_OK is None:
        SAMBA_OK = key_works(SAMBA, "SAMBANOVA_API_KEY", "Meta-Llama-3.3-70B-Instruct")
    c = {
        "gemini-3.5-flash-lite": gemini("gemini-3.5-flash-lite"),
        "gemma-4-26b-a4b (Google)": gemini("gemma-4-26b-a4b-it", native=False),
        "gpt-oss-120b (Cerebras)": OAICompat(CEREBRAS, "CEREBRAS_API_KEY", "gpt-oss-120b",
                                             [{"temperature": 0, "reasoning_effort": "low", "response_format": {"type": "json_object"}}, {"reasoning_effort": "low"}]),
        "qwen-3.8-27b (Cerebras)": OAICompat(CEREBRAS, "CEREBRAS_API_KEY", "qwen-3.8-27b",
                                             [{"temperature": 0, "reasoning_effort": "none", "response_format": {"type": "json_object"}}, {"temperature": 0, "reasoning_effort": "low"}] + T0),
        "gemma-4-31b (SambaNova)": OAICompat(SAMBA, "SAMBANOVA_API_KEY", "gemma-4-31B-it", T0),
        "llama-3.3-70b (SambaNova)": OAICompat(SAMBA, "SAMBANOVA_API_KEY", "Meta-Llama-3.3-70B-Instruct", T0),
        "deepseek-v3.2 (SambaNova)": OAICompat(SAMBA, "SAMBANOVA_API_KEY", "DeepSeek-V3.2", T0),
        "gpt-5.4-nano (OpenAI)": OAICompat(None, "OPENAI_API_KEY", "gpt-5.4-nano",
                                           [{"reasoning_effort": "none", "response_format": {"type": "json_object"}}, {"reasoning_effort": "minimal", "response_format": {"type": "json_object"}}, {"reasoning_effort": "low", "response_format": {"type": "json_object"}}]),
        "gpt-5.4-mini (OpenAI)": OAICompat(None, "OPENAI_API_KEY", "gpt-5.4-mini",
                                           [{"reasoning_effort": "none", "response_format": {"type": "json_object"}}, {"reasoning_effort": "minimal", "response_format": {"type": "json_object"}}, {"reasoning_effort": "low", "response_format": {"type": "json_object"}}]),
    }
    return {k: v for k, v in c.items() if SAMBA_OK or "SambaNova" not in k}


def run_all(cands, per_case):
    """Run each candidate over all cases; candidates in parallel, cases sequential (latency stays per call)."""
    def one(name, fn):
        rows, times = [], []
        for c in CASES:
            t0 = time.perf_counter()
            try:
                row = per_case(fn, c)
                dt = time.perf_counter() - t0
                times.append(dt)
                row.update(id=c["id"], valid=True, latency=round(dt, 2))
            except Exception as e:  # noqa: BLE001
                row = {"id": c["id"], "valid": False, "error": str(e)[:200]}
            rows.append(row)
            with PRINT:
                print(f"[{name}] {c['id']} {'ok' if row['valid'] else 'ERR ' + row['error'][:100]}", flush=True)
        return name, rows, times
    with ThreadPoolExecutor(max_workers=len(cands)) as ex:
        return list(ex.map(lambda kv: one(*kv), cands.items()))


# ---------------------------------------------------------------- stage 1: structurer

def part_structure():
    cands = {k: v for k, v in candidates().items() if k != "gemma-4-26b-a4b (Google)"}  # already tested

    def per_case(fn, c):
        return score_struct(CaseFacts.model_validate(fn(STRUCT_SYS, f"Patient message: {c['text']}", CaseFacts)).model_dump(), c)

    return summarise_struct(run_all(cands, per_case))


def score_struct(out, c):
    if True:
        pool = out["symptoms_present"] + out["services_requested"] + out["existing_conditions"] + out["vitals_reported"]
        hit = sum(any(matches(p, g) for p in pool) for g in c["present"])
        leak = sum(any(matches(p, g) for p in out["symptoms_present"]) for g in c["negated"])
        extra = sum(not any(matches(p, g) for g in c["present"] + c["negated"]) for p in out["symptoms_present"])
        age_ok = any(a in (out["patient_age"] or "").lower() for a in c["age"]) if c["age"] else None
        return dict(out=out, groups=len(c["present"]), hit=hit, leak=leak, neg_total=len(c["negated"]), extra=extra,
                    redflag_kept=(hit == len(c["present"])) if c["red_flag"] else None, age_ok=age_ok,
                    lang_ok=out["detected_language"] == c["lang"])


def summarise_struct(results):
    report = {}
    for name, rows, times in results:
        ok = [r for r in rows if r["valid"]]
        s = {
            "valid": f"{len(ok)}/{len(rows)}",
            "fact_recall": round(sum(r["hit"] for r in ok) / max(sum(r["groups"] for r in ok), 1), 3),
            "cases_all_facts": f"{sum(r['hit'] == r['groups'] for r in ok)}/{len(rows)}",
            "red_flag_cases_kept": f"{sum(bool(r['redflag_kept']) for r in ok)}/{sum(c['red_flag'] for c in CASES)}",
            "denial_leaks": f"{sum(r['leak'] for r in ok)}/{sum(r['neg_total'] for r in ok)}",
            "unmatched_extra_symptoms": sum(r["extra"] for r in ok),
            "age_correct": f"{sum(bool(r['age_ok']) for r in ok)}/{sum(bool(c['age']) for c in CASES)}",
            "language_correct": f"{sum(r['lang_ok'] for r in ok)}/{len(rows)}",
            **lat_stats(times),
        }
        print(f"\n=== {name}\n   {json.dumps(s)}")
        report[name] = {"summary": s, "rows": rows}
    return report


def part_rescore():
    """Re-score saved structurer outputs after a scorer/gold fix (no model calls)."""
    saved = json.loads((RESULTS / "v2_structure.json").read_text(encoding="utf-8"))
    gold = {c["id"]: c for c in CASES}
    results = []
    for name, v in saved.items():
        rows = [dict(score_struct(r["out"], gold[r["id"]]), id=r["id"], valid=True, latency=r["latency"]) if r["valid"] else r for r in v["rows"]]
        results.append((name, rows, [r["latency"] for r in rows if r["valid"]]))
    return summarise_struct(results)


# ---------------------------------------------------------------- stage 2: classifier

ORDER = {"routine": 0, "same_day": 1, "immediate": 2}


def facts_for_classifier():
    """Qwen 3.8 27B CaseFacts from part_structure (best open structurer; fixed input for every classifier)."""
    rows = json.loads((RESULTS / "v2_structure.json").read_text(encoding="utf-8"))["qwen-3.8-27b (Cerebras)"]["rows"]
    facts = {}
    for r in rows:
        f = dict(r["out"])
        f.pop("detected_language", None)
        facts[r["id"]] = json.dumps(f, ensure_ascii=False)
    return facts


def part_classify():
    facts = facts_for_classifier()
    cands = candidates()
    cands["medgemma-4b (local)"] = ollama(MEDGEMMA)
    httpx.post(f"{OLLAMA}/api/generate", json={"model": MEDGEMMA, "prompt": "hi", "keep_alive": -1, "options": {"num_predict": 1}}, timeout=300)

    def per_case(fn, c):
        d = TriageDecision.model_validate(fn(CLASSIFY_SYS, f"Patient facts: {facts[c['id']]}", TriageDecision)).model_dump()
        return dict(pred=d, care_ok=d["care_type"] == c["care_type"], urg_ok=d["urgency"] == c["urgency"],
                    emergency=c["care_type"] == "emergency", pred_emergency=d["care_type"] == "emergency",
                    under=ORDER[d["urgency"]] < ORDER[c["urgency"]], asked=d["status"] == "need_more_info")

    report = {}
    gold = {c["id"]: c for c in CASES}
    for name, rows, times in run_all(cands, per_case):
        s = classify_summary(rows, times, gold)
        print(f"\n=== {name}\n   {json.dumps(s)}")
        report[name] = {"summary": s, "rows": rows}
    return report


def classify_summary(rows, times, gold):
    if True:
        ok = [r for r in rows if r["valid"]]
        em = [r for r in ok if r["emergency"]]
        non = [r for r in ok if not r["emergency"]]
        f1 = lambda key: round(f1_score([gold[r["id"]][key] for r in ok], [r["pred"][key] for r in ok], average="macro", zero_division=0), 3) if ok else 0
        s = {
            "valid": f"{len(ok)}/{len(rows)}",
            "care_macro_f1": f1("care_type"),
            "urgency_macro_f1": f1("urgency"),
            "care_correct": f"{sum(r['care_ok'] for r in ok)}/{len(rows)}",
            "urgency_correct": f"{sum(r['urg_ok'] for r in ok)}/{len(rows)}",
            "emergencies_caught": f"{sum(r['pred_emergency'] for r in em)}/{sum(c['care_type'] == 'emergency' for c in CASES)}",
            "false_emergencies": sum(r["pred_emergency"] and not r["emergency"] for r in ok),
            "under_triaged": sum(r["under"] for r in ok),
            "asks_follow_up_non_emergency": f"{sum(r['asked'] for r in non)}/{len(non)}",
            "asks_follow_up_on_emergency": sum(r["asked"] for r in em),
            **lat_stats(times),
        }
        return s


FEW_SHOT = """

Examples (facts -> decision):
{"symptoms_present":["vomiting","loose motions"],"patient_age":"70 years","duration":"1 day"} -> {"status":"final","care_type":"paramedic_home_visit","urgency":"same_day","red_flags":[],"missing_info":[]}
{"services_requested":["catheter change"],"existing_conditions":["bedridden"]} -> {"status":"final","care_type":"nurse","urgency":"routine","red_flags":[],"missing_info":[]}
{"symptoms_present":["lower back pain"],"duration":"4 months"} -> {"status":"final","care_type":"specialist","urgency":"routine","red_flags":[],"missing_info":[]}
{"symptoms_present":["severe abdominal pain"],"special_population":["pregnant"]} -> {"status":"final","care_type":"emergency","urgency":"immediate","red_flags":["severe abdominal pain in pregnancy"],"missing_info":[]}
{"symptoms_present":["fever"],"duration":"1 day","patient_age":null} -> {"status":"need_more_info","care_type":"gp_teleconsult","urgency":"same_day","red_flags":[],"missing_info":["patient age","any breathing difficulty"]}
{"symptoms_present":["sudden confusion"],"patient_age":"75 years"} -> {"status":"final","care_type":"emergency","urgency":"immediate","red_flags":["sudden confusion"],"missing_info":[]}"""


def part_classify_medgemma():
    """MedGemma re-run with capped lists, plus a few-shot variant; merged into v2_classify.json."""
    facts = facts_for_classifier()
    gold = {c["id"]: c for c in CASES}
    fn = ollama(MEDGEMMA)
    path = RESULTS / "v2_classify.json"
    report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for name, system in (("medgemma-4b (local)", CLASSIFY_SYS), ("medgemma-4b + 6 examples (local)", CLASSIFY_SYS + FEW_SHOT)):
        if name in report:
            continue

        def per_case(f, c, system=system):
            d = TriageDecision.model_validate(f(system, f"Patient facts: {facts[c['id']]}", TriageDecision)).model_dump()
            return dict(pred=d, care_ok=d["care_type"] == c["care_type"], urg_ok=d["urgency"] == c["urgency"],
                        emergency=c["care_type"] == "emergency", pred_emergency=d["care_type"] == "emergency",
                        under=ORDER[d["urgency"]] < ORDER[c["urgency"]], asked=d["status"] == "need_more_info")
        (_, rows, times), = run_all({name: fn}, per_case)
        report[name] = {"summary": classify_summary(rows, times, gold), "rows": rows}
        print(f"\n=== {name}\n   {json.dumps(report[name]['summary'])}")
    return report


# ---------------------------------------------------------------- follow-up phrasing

def part_phrase():
    cls = json.loads((RESULTS / "v2_classify.json").read_text(encoding="utf-8"))
    src = cls.get("medgemma-4b (local)") or next(iter(cls.values()))
    asks = {r["id"]: r["pred"]["missing_info"] for r in src["rows"] if r.get("valid") and r["pred"]["missing_info"]}
    if len(asks) < 6:  # make sure every language gets exercised
        for c in CASES:
            asks.setdefault(c["id"], ["patient's age", "whether there is difficulty breathing"])
            if len(asks) >= 12:
                break
    cases = [c for c in CASES if c["id"] in asks]
    all_c = candidates()
    names = [n for n in ["gemini-3.5-flash-lite", "gpt-oss-120b (Cerebras)", "qwen-3.8-27b (Cerebras)", "gemma-4-31b (SambaNova)", "gpt-5.4-nano (OpenAI)", "gpt-5.4-mini (OpenAI)"] if n in all_c]
    report = {}
    for name in names:
        fn = all_c[name]
        rows, times = [], []
        for c in cases:
            t0 = time.perf_counter()
            try:
                q = fn(PHRASE_SYS, f"Reply language: {REPLY_LANG[c['lang']]}\nUser's message: {c['text']}\nMissing facts: {asks[c['id']]}", None)
                dt = time.perf_counter() - t0
                times.append(dt)
                want = "deva" if c["lang"] == "hi" else "latin"
                rows.append(dict(id=c["id"], lang=c["lang"], missing=asks[c["id"]], question=q, script_ok=script_of(q) == want, latency=round(dt, 2)))
            except Exception as e:  # noqa: BLE001
                rows.append(dict(id=c["id"], error=str(e)[:150]))
        ok = [r for r in rows if "question" in r]
        s = {"script_correct": f"{sum(r['script_ok'] for r in ok)}/{len(rows)}", **lat_stats(times)}
        print(f"\n=== {name}  {json.dumps(s)}")
        for r in ok:
            print(f"   {r['id']} [{r['lang']}] {r['question']}")
        report[name] = {"summary": s, "rows": rows}
    return report


if __name__ == "__main__":
    part = sys.argv[1]
    part_name = {"rescore": "structure", "classify_medgemma": "classify"}.get(part, part)
    result = {"classify_medgemma": part_classify_medgemma, "rescore": part_rescore, "structure": part_structure, "classify": part_classify, "phrase": part_phrase}[part]()
    (RESULTS / f"v2_{part_name}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved eval/results/v2_{part}.json")
