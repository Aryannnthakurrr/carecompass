"""Smoke test: call local models through Ollama's OpenAI-compatible endpoint."""
import sys
import time
from pathlib import Path

import yaml
from openai import OpenAI

cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text(encoding="utf-8"))

PROMPT = (
    "Patient: 'papa ko do din se bukhar hai aur khansi bhi' (father, fever and cough for 2 days). "
    'Reply ONLY with JSON: {"care_type": one of [paramedic_home_visit, nurse, gp_teleconsult, '
    'specialist, emergency], "urgency": one of [routine, same_day, immediate]}'
)

failed = False
for key in ("triage", "local_fallback"):
    m = cfg["models"][key]
    client = OpenAI(base_url=m["base_url"], api_key="ollama")
    t0 = time.perf_counter()
    try:
        r = client.chat.completions.create(
            model=m["model"],
            messages=[{"role": "user", "content": PROMPT}],
            temperature=0,
            max_tokens=60,
        )
        print(f"[{key}] {m['model']}  {time.perf_counter() - t0:.2f}s")
        print("   ", r.choices[0].message.content.strip())
    except Exception as e:
        failed = True
        print(f"[{key}] {m['model']} FAILED: {e}")

sys.exit(1 if failed else 0)
