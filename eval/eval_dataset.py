"""Score the full /triage pipeline on the organiser dataset (training/triage_dataset.jsonl) and write predictions.

Usage: python eval/eval_dataset.py [--no-medgemma]
Writes eval/results/dataset_eval.json and eval/results/predictions.csv.
The model stages are cached per message text (the 340 rows hold ~66 distinct messages); matching runs per row.
"""
import csv
import json
import sys
import time
from functools import lru_cache
from pathlib import Path

from sklearn.metrics import f1_score

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import explain, intake, llm, pipeline, screen, triage  # noqa: E402
from app.schemas import TriageRequest  # noqa: E402

if "--no-medgemma" in sys.argv:
    llm.medgemma = lambda *a, **k: None

# cache the model stages by text / facts (the 340 rows hold ~66 distinct messages); explanations are not scored
_structure, _classify, _screen = intake.structure, triage.classify, screen.screen
STRUCT_CACHE, CLASS_CACHE, SCREEN_CACHE = {}, {}, {}


def cached_structure(history, user_input, *args, **kw):
    if user_input not in STRUCT_CACHE:
        STRUCT_CACHE[user_input] = _structure(history, user_input, *args, **kw)
    return STRUCT_CACHE[user_input]


def cached_classify(facts, *args, **kw):
    key = facts.model_dump_json()
    if key not in CLASS_CACHE:
        CLASS_CACHE[key] = _classify(facts, *args, **kw)
    return CLASS_CACHE[key]


def cached_screen(facts):
    key = facts.model_dump_json()
    if key not in SCREEN_CACHE:
        SCREEN_CACHE[key] = _screen(facts)
    return SCREEN_CACHE[key]


intake.structure = cached_structure
triage.classify = cached_classify
screen.screen = cached_screen
explain.explanation = lambda *a, **k: ""
explain.follow_up = lambda *a, **k: "follow-up"

rows = [json.loads(l) for l in (ROOT / "training/triage_dataset.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
preds, times = [], []
for i, r in enumerate(rows):
    t0 = time.perf_counter()
    res = pipeline.run(TriageRequest(**r["request"]))
    times.append(time.perf_counter() - t0)
    preds.append(res)
    if i % 20 == 0:
        print(f"{i}/{len(rows)}", flush=True)

FU = "FOLLOW_UP"
gold_care = [r["response"]["care_type"] or FU for r in rows]
pred_care = [p.care_type or FU for p in preds]
gold_urg = [r["response"]["urgency"] or FU for r in rows]
pred_urg = [p.urgency or FU for p in preds]
em = [i for i, g in enumerate(gold_care) if g == "emergency_112"]
rank = {"scheduled": 0, "same_day": 1, "emergency": 2}
under = [i for i in range(len(rows)) if gold_urg[i] in rank and pred_urg[i] in rank and rank[pred_urg[i]] < rank[gold_urg[i]]]

with_cg = [i for i, r in enumerate(rows) if r["response"]["matched_caregivers"] and preds[i].care_type == r["response"]["care_type"]]
top1 = sum(preds[i].matched_caregivers and preds[i].matched_caregivers[0].caregiver_id == rows[i]["response"]["matched_caregivers"][0]["caregiver_id"] for i in with_cg)
overlap = sum(bool({m.caregiver_id for m in preds[i].matched_caregivers} & {m["caregiver_id"] for m in rows[i]["response"]["matched_caregivers"]}) for i in with_cg)

wrong = sorted({(rows[i]["request"]["user_input"], gold_care[i], pred_care[i]) for i in range(len(rows)) if gold_care[i] != pred_care[i]})
summary = {
    "rows": len(rows),
    "care_macro_f1": round(f1_score(gold_care, pred_care, average="macro"), 4),
    "urgency_macro_f1": round(f1_score(gold_urg, pred_urg, average="macro"), 4),
    "care_accuracy": round(sum(a == b for a, b in zip(gold_care, pred_care)) / len(rows), 4),
    "emergency_recall": f"{sum(pred_care[i] == 'emergency_112' for i in em)}/{len(em)}",
    "false_emergencies": sum(p == "emergency_112" and g != "emergency_112" for g, p in zip(gold_care, pred_care)),
    "under_triaged": len(under),
    "follow_up": f"asked {sum(p == FU for p in pred_care)}, gold {sum(g == FU for g in gold_care)}, both {sum(p == g == FU for g, p in zip(gold_care, pred_care))}",
    "matching_top1_agreement": f"{top1}/{len(with_cg)}",
    "matching_any_overlap": f"{overlap}/{len(with_cg)}",
    "median_latency_s_uncached": round(sorted(times)[len(times) // 2], 2),
    "medgemma": "off" if "--no-medgemma" in sys.argv else "on",
}
print(json.dumps(summary, indent=1))
print("\nDistinct misclassified messages (text, gold, predicted):")
for w in wrong:
    print("  ", w)

out = ROOT / "eval/results"
(out / "dataset_eval.json").write_text(json.dumps({"summary": summary, "wrong": wrong}, indent=1, ensure_ascii=False), encoding="utf-8")
with open(out / "predictions.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["id", "follow_up_question", "care_type", "urgency", "red_flags", "matched_caregivers"])
    for r, p in zip(rows, preds):
        w.writerow([r["id"], p.follow_up_question or "", p.care_type or "", p.urgency or "", ";".join(p.red_flags),
                    ";".join(f"{m.caregiver_id}:{m.score}" for m in p.matched_caregivers)])
print("\nWrote eval/results/dataset_eval.json and eval/results/predictions.csv")
