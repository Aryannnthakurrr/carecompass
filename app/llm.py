"""Model clients. Qwen on Cerebras (structurer, second-opinion classifier, phrasing) with a Gemini
Flash-Lite fallback; MedGemma on a local Ollama. Every name comes from config.yaml."""
import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

import httpx
import yaml
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
MODELS = CFG["models"]
log = logging.getLogger("carecompass.llm")


def _schema_hint(cls: type[BaseModel]) -> str:
    return f"\nReturn ONLY a JSON object matching this JSON schema:\n{json.dumps(cls.model_json_schema())}"


def _parse(text: str) -> dict:
    t = (text or "").strip()
    return json.loads(t[t.find("{"): t.rfind("}") + 1])


@lru_cache(maxsize=1)
def _qwen() -> OpenAI:
    m = MODELS["conversation"]
    return OpenAI(base_url=m["base_url"], api_key=os.environ["CEREBRAS_API_KEY"], timeout=15, max_retries=1)


@lru_cache(maxsize=1)
def _gemini():
    from google import genai
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def _qwen_call(system: str, user: str, cls: Optional[type[BaseModel]]) -> str:
    m = MODELS["conversation"]
    kw = dict(model=m["model"], temperature=0, reasoning_effort=m.get("reasoning_effort", "none"),
              messages=[{"role": "system", "content": system + (_schema_hint(cls) if cls else "")},
                        {"role": "user", "content": user}])
    if cls:
        kw["response_format"] = {"type": "json_object"}
    return _qwen().chat.completions.create(**kw).choices[0].message.content


def _gemini_call(system: str, user: str, cls: Optional[type[BaseModel]]) -> str:
    from google.genai import types
    fb = MODELS["conversation"]["fallback"]
    cfg = dict(temperature=0, system_instruction=system,
               thinking_config=types.ThinkingConfig(thinking_level=fb.get("thinking_level", "minimal")))
    if cls:
        cfg.update(response_mime_type="application/json", response_schema=cls)
    return _gemini().models.generate_content(model=fb["model"], contents=user, config=types.GenerateContentConfig(**cfg)).text


def chat(system: str, user: str, cls: Optional[type[BaseModel]] = None):
    """Qwen first, Flash-Lite if Qwen fails. Returns a validated model instance (cls) or text."""
    last = None
    for name, fn in (("qwen", _qwen_call), ("flash-lite", _gemini_call)):
        try:
            out = fn(system, user, cls)
            return cls.model_validate(_parse(out)) if cls else out.strip()
        except Exception as e:  # noqa: BLE001 - any provider error falls through to the next
            log.warning("%s failed: %s", name, str(e)[:200])
            last = e
    raise RuntimeError(f"all conversation models failed: {last}")


def medgemma(system: str, user: str, cls: type[BaseModel], num_predict: int = 400) -> Optional[BaseModel]:
    """MedGemma via Ollama with grammar-constrained JSON. None if Ollama is down, slow or the output is invalid."""
    m = MODELS["classifier"]
    base = m["base_url"].removesuffix("/v1")
    body = {"model": m["model"], "stream": False, "keep_alive": -1, "format": cls.model_json_schema(),
            "options": {"temperature": 0, "num_predict": num_predict},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    try:
        r = httpx.post(f"{base}/api/chat", json=body, timeout=m.get("timeout_s", 20))
        r.raise_for_status()
        return cls.model_validate(_parse(r.json()["message"]["content"]))
    except Exception as e:  # noqa: BLE001
        log.warning("medgemma unavailable: %s", str(e)[:200])
        return None
