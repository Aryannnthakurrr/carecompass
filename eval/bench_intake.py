"""Benchmark candidate intake LLMs on latency and output quality.

Usage: python eval/bench_intake.py [runs_per_case]
"""
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "smoke_tests"))
from _common import CFG, require_env  # noqa: E402

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402
from openai import OpenAI  # noqa: E402
from sarvamai import SarvamAI  # noqa: E402

RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 2

SYSTEM = """You are a medical intake assistant for a home-healthcare app. Never diagnose or prescribe.
Extract information from the patient's message and return ONLY a JSON object with keys:
symptoms (list of short English strings), duration (string|null), patient_age (string|null),
severity (mild|moderate|severe|null), missing_slots (list of strings),
follow_up_question (ONE short question in the SAME language and script as the patient, or null if enough info),
detected_language (en|hi|hinglish)."""

CASES = [
    "papa ko do din se bukhar hai aur khansi bhi",
    "मेरी माँ को सुबह से सीने में दर्द है और पसीना आ रहा है",
    "my 6 year old has had a rash on his arms since yesterday, no fever",
]

gem = genai.Client(api_key=require_env("GEMINI_API_KEY"))
sarvam = SarvamAI(api_subscription_key=require_env("SARVAM_API_KEY"))
ollama = OpenAI(base_url=CFG["models"]["triage"]["base_url"], api_key="ollama")


def gemini(model, thinking_level=None):
    def call(text):
        cfg = dict(temperature=0, response_mime_type="application/json", system_instruction=SYSTEM)
        if thinking_level:
            cfg["thinking_config"] = types.ThinkingConfig(thinking_level=thinking_level)
        return gem.models.generate_content(
            model=model, contents=text, config=types.GenerateContentConfig(**cfg)
        ).text
    return call


def gemma(model):
    # Gemma on the Gemini API: system prompt inlined to be safe
    def call(text):
        return gem.models.generate_content(
            model=model,
            contents=f"{SYSTEM}\n\nPatient message: {text}",
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(thinking_level="minimal"),
            ),
        ).text
    return call


def sarvam_chat(model):
    def call(text):
        r = sarvam.chat.completions(
            model=model,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
            temperature=0,
            reasoning_effort="low",
        )
        return r.choices[0].message.content
    return call


def local(model):
    def call(text):
        r = ollama.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
            temperature=0,
            response_format={"type": "json_object"},
        )
        return r.choices[0].message.content
    return call


CANDIDATES = {
    "gemma-4-26b-a4b (minimal)": gemma("gemma-4-26b-a4b-it"),
    "gemini-3.5-flash-lite": gemini("gemini-3.5-flash-lite", "minimal"),
    "gemini-3.8-flash (minimal)": gemini("gemini-3.8-flash", "minimal"),
    "gemini-flash-lite-latest": gemini("gemini-flash-lite-latest"),
    "sarvam-105b (low)": sarvam_chat("sarvam-105b"),
    "medgemma-4b local": local(CFG["models"]["triage"]["model"]),
}


def parse(text):
    t = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(t[t.find("{"): t.rfind("}") + 1])


for name, fn in CANDIDATES.items():
    print(f"\n=== {name}")
    times = []
    for case in CASES:
        out = None
        for _ in range(RUNS):
            t0 = time.perf_counter()
            try:
                raw = fn(case)
                times.append(time.perf_counter() - t0)
                out = parse(raw)
            except Exception as e:
                print(f"   ERROR: {str(e)[:160]}")
                break
        if out:
            print(f"   {case[:40]!r}")
            print(f"     symptoms={out.get('symptoms')} sev={out.get('severity')} lang={out.get('detected_language')}")
            print(f"     follow_up={out.get('follow_up_question')!r}")
    if times:
        print(f"   latency median {statistics.median(times):.2f}s  max {max(times):.2f}s  (n={len(times)})")
