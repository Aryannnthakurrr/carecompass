"""Base vs fine-tuned MedGemma on the held-out split, on the exact production task (CaseFacts -> Decision).

Usage:
  python training/eval_finetune.py --model hf.co/unsloth/medgemma-4b-it-GGUF:Q4_K_M --prompt few_shot      # today's setup
  python training/eval_finetune.py --model hf.co/unsloth/medgemma-4b-it-GGUF:Q4_K_M --prompt rules_only    # base, no examples
  python training/eval_finetune.py --model carecompass-medgemma --prompt rules_only                         # the fine-tune
  python training/eval_finetune.py --qwen                                                                   # reference

Reads training/sft/test.jsonl (or --split val / --split hard for the hand-labelled set). Writes eval/results/finetune_<name>.json.
With --pair, also scores the production combination (MedGemma + Qwen, app/triage.combine) and caches Qwen's answers.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from sklearn.metrics import f1_score

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import llm, triage  # noqa: E402
from app.schemas import Decision  # noqa: E402

NEED = "need_more_info"
RANK = {NEED: -1, "scheduled": 0, "same_day": 1, "emergency": 2}
QWEN_CACHE = ROOT / "training" / "sft" / "qwen_cache.json"


def label(d: Decision | None) -> str:
    return "INVALID" if d is None else NEED if d.status == NEED else d.care_type


def urgency(lab: str) -> str:
    return triage.URGENCY_FOR_CARE.get(lab, lab)


def score(rows, preds, times):
    gold = [r["target"] for r in rows]
    pred = [label(p) for p in preds]
    em = [i for i, g in enumerate(gold) if g == "emergency_112"]
    under = [i for i, (g, p) in enumerate(zip(gold, pred))
             if g != NEED and p not in ("INVALID",) and RANK[urgency(p)] < RANK[urgency(g)]]
    s = {
        "rows": len(rows),
        "valid": f"{sum(p != 'INVALID' for p in pred)}/{len(rows)}",
        "care_macro_f1": round(f1_score(gold, pred, average="macro", labels=sorted(set(gold))), 3),
        "urgency_macro_f1": round(f1_score([urgency(g) for g in gold], [urgency(p) for p in pred], average="macro",
                                           labels=sorted({urgency(g) for g in gold})), 3),
        "accuracy": round(sum(g == p for g, p in zip(gold, pred)) / len(rows), 3),
        "emergency_recall": f"{sum(pred[i] == 'emergency_112' for i in em)}/{len(em)}",
        "under_triaged": len(under),
        "false_emergencies": sum(p == "emergency_112" and g != "emergency_112" for g, p in zip(gold, pred)),
        "needless_follow_ups": sum(p == NEED and g != NEED for g, p in zip(gold, pred)),
        "missed_follow_ups": sum(g == NEED and p != NEED for g, p in zip(gold, pred)),
    }
    if times:
        s |= {"median_s": round(statistics.median(times), 2), "p90_s": round(sorted(times)[int(len(times) * 0.9) - 1], 2)}
    for src in ("organiser", "synthetic", "human"):
        idx = [i for i, r in enumerate(rows) if r["source"] == src]
        if idx:
            s[f"accuracy_{src}"] = f"{sum(gold[i] == pred[i] for i in idx)}/{len(idx)}"
    for lang in ("en", "hi", "hinglish"):
        idx = [i for i, r in enumerate(rows) if r["lang"] == lang]
        if idx:
            s[f"accuracy_{lang}"] = f"{sum(gold[i] == pred[i] for i in idx)}/{len(idx)}"
    s["errors"] = [{"facts": rows[i]["messages"][1]["content"][15:], "gold": gold[i], "pred": pred[i]}
                   for i in range(len(rows)) if gold[i] != pred[i]]
    return s


def qwen_votes(rows):
    cache = json.loads(QWEN_CACHE.read_text(encoding="utf-8")) if QWEN_CACHE.exists() else {}
    out = []
    for r in rows:
        user = r["messages"][1]["content"]
        if user not in cache:
            try:
                cache[user] = llm.chat(triage.SYSTEM, user, Decision).model_dump()
            except Exception:  # noqa: BLE001
                cache[user] = None
        out.append(Decision.model_validate(cache[user]) if cache[user] else None)
    QWEN_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=llm.MODELS["classifier"]["model"], help="Ollama tag")
    ap.add_argument("--prompt", choices=["few_shot", "rules_only"], default="few_shot")
    ap.add_argument("--split", default="test")
    ap.add_argument("--qwen", action="store_true", help="score Qwen alone instead of MedGemma")
    ap.add_argument("--pair", action="store_true", help="also score MedGemma + Qwen combined, as in production")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    rows = [json.loads(l) for l in (ROOT / "training/sft" / f"{a.split}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = rows[: a.limit] if a.limit else rows
    report = {}
    if a.qwen:
        t0 = time.perf_counter()
        preds = qwen_votes(rows)
        name = f"qwen__{a.split}"
        report[name] = score(rows, preds, [])
    else:
        llm.MODELS["classifier"]["model"] = a.model
        system = triage.SYSTEM if a.prompt == "few_shot" else triage.RULES
        preds, times = [], []
        for i, r in enumerate(rows):
            t0 = time.perf_counter()
            preds.append(llm.medgemma(system, r["messages"][1]["content"], Decision))
            times.append(time.perf_counter() - t0)
            if i % 10 == 0:
                print(f"{i}/{len(rows)}", flush=True)
        name = f"{a.model.split('/')[-1].replace(':', '_')}__{a.prompt}__{a.split}"
        report[name] = score(rows, preds, times[1:] or times)  # first call includes model load
        if a.pair:
            report[f"{name}__pair_with_qwen"] = score(rows, [triage.combine(m, q) for m, q in zip(preds, qwen_votes(rows))], [])

    out = ROOT / "eval/results" / f"finetune_{name}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    for k, v in report.items():
        print(k, json.dumps({x: y for x, y in v.items() if x != "errors"}, indent=1))
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
