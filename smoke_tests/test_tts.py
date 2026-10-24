"""Smoke test: Sarvam Bulbul text-to-speech. Saves WAVs to smoke_tests/out/."""
import base64
import time

from sarvamai import SarvamAI

from _common import CFG, OUT, require_env

client = SarvamAI(api_subscription_key=require_env("SARVAM_API_KEY"))
m = CFG["models"]["tts"]

samples = {
    "hinglish_roman": "Papa ko do din se bukhar hai.",
    "hindi_devanagari": "पापा को दो दिन से बुखार है।",
}

for name, text in samples.items():
    t0 = time.perf_counter()
    r = client.text_to_speech.convert(
        text=text, language_code=m["language_code"], speaker=m["speaker"], model=m["model"]
    )
    dt = time.perf_counter() - t0
    path = OUT / f"tts_{name}.wav"
    path.write_bytes(base64.b64decode("".join(r.audios)))
    print(f"[{m['model']}/{m['speaker']}] {name}: {dt:.2f}s -> {path}")
