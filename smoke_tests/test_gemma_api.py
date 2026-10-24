"""Smoke test: Gemma via Gemini API — list available Gemma ids, then structured intake JSON."""
import json
import time

from google import genai
from google.genai import types

from _common import CFG, require_env

client = genai.Client(api_key=require_env("GEMINI_API_KEY"))
model = CFG["models"]["intake"]["model"]

gemma = sorted(m.name.removeprefix("models/") for m in client.models.list() if "gemma" in m.name)
print("Gemma models available:", ", ".join(gemma) or "none")

PROMPT = """You are a medical intake assistant. Do not diagnose.
Patient message: "papa ko do din se bukhar hai aur khansi bhi"
Return ONLY JSON with keys: symptoms (list of English strings), duration, patient_age,
severity, missing_slots (list), follow_up_question (in the user's language/script, or null),
detected_language (en | hi | hinglish)."""

t0 = time.perf_counter()
try:
    r = client.models.generate_content(
        model=model,
        contents=PROMPT,
        config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json"),
    )
    json_mode = True
except Exception as e:  # some Gemma endpoints reject JSON mode; retry as plain text
    print(f"JSON mode rejected ({e.__class__.__name__}: {e}); retrying without it")
    r = client.models.generate_content(model=model, contents=PROMPT)
    json_mode = False
dt = time.perf_counter() - t0

text = r.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
print(f"[{model}] {dt:.2f}s  json_mode={json_mode}")
print(json.dumps(json.loads(text), indent=2, ensure_ascii=False))
