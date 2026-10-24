"""Smoke test: Sarvam speech-to-text with auto language detection.

Usage: python smoke_tests/test_stt.py [audio.wav]
Defaults to the WAV produced by test_tts.py.
"""
import sys
import time
from pathlib import Path

from sarvamai import SarvamAI

from _common import CFG, OUT, require_env

client = SarvamAI(api_subscription_key=require_env("SARVAM_API_KEY"))
m = CFG["models"]["stt"]

audio = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT / "tts_hindi_devanagari.wav"
if not audio.exists():
    sys.exit(f"{audio} not found. Run test_tts.py first or pass a WAV path.")

for mode in (m["mode"], "codemix"):
    t0 = time.perf_counter()
    with audio.open("rb") as f:
        r = client.speech_to_text.transcribe(
            file=f, model=m["model"], mode=mode, language_code=m["language_code"]
        )
    dt = time.perf_counter() - t0
    print(f"[{m['model']}/{mode}] {dt:.2f}s  lang={r.language_code} p={getattr(r, 'language_probability', None)}")
    print("   ", r.transcript)
