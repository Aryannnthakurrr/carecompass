"""MedGemma emergency screener (app/screen.py) on the probe's 133 held-out cases (local Ollama, no API).
Same cases and recall-first labels as training/medgemma_probe.py, so the numbers compare directly.
    python eval/eval_screener.py      -> eval/results/screener.json"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import screen  # noqa: E402
from app.schemas import CaseFacts  # noqa: E402

probe = json.loads((ROOT / "eval/results/medgemma_probe.json").read_text(encoding="utf-8"))["note_screen"]["rows"]
relabel = {json.loads(l)["id"]: json.loads(l)["target"] for l in (ROOT / "training/sft/manual_review.jsonl").read_text(encoding="utf-8").splitlines()}
rows = {json.loads(l)["id"]: json.loads(l) for l in (ROOT / "training/sft/test_v2.jsonl").read_text(encoding="utf-8").splitlines()}
out = []
for i, p in enumerate(probe):
    r = rows[p["id"]]
    facts = CaseFacts(**json.loads(r["messages"][1]["content"][len("Patient facts: "):]))
    s = screen.screen(facts)
    gold = relabel.get(p["id"], p["gold"])
    out.append({"id": p["id"], "gold": gold, "level": s.level if s else None, "signs": s.danger_signs if s else [], "note": screen.note(facts)})
    if i % 20 == 0:
        print(f"{i}/{len(probe)}", flush=True)
em = [x for x in out if x["gold"] == "emergency_112"]; non = [x for x in out if x["gold"] != "emergency_112"]
summary = {"emergencies_caught": f"{sum(x['level'] == 'emergency_now' for x in em)}/{len(em)}",
           "false_alarms": f"{sum(x['level'] == 'emergency_now' for x in non)}/{len(non)}",
           "invalid": sum(x["level"] is None for x in out)}
print(json.dumps(summary))
(ROOT / "eval/results/screener.json").write_text(json.dumps({"summary": summary, "rows": out}, indent=1, ensure_ascii=False), encoding="utf-8")
