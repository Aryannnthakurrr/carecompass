"""Latency of the red-flag rules on the laptop CPU: every organiser message plus the training split (facts JSON).
    python eval/time_redflags.py"""
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import redflags  # noqa: E402

texts = [json.loads(l)["request"]["user_input"] for l in (ROOT / "training/triage_dataset.jsonl").read_text(encoding="utf-8").splitlines()]
texts += [json.loads(l)["messages"][1]["content"][:600] for l in (ROOT / "training/sft/train.jsonl").read_text(encoding="utf-8").splitlines()]
ms = []
for t in texts:
    start = time.perf_counter()
    redflags.detect(t)
    ms.append((time.perf_counter() - start) * 1000)
ms.sort()
print(f"{len(ms)} texts: median {statistics.median(ms):.2f} ms, p95 {ms[int(0.95 * len(ms))]:.2f} ms, max {ms[-1]:.2f} ms")
