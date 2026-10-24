"""Red-flag rule checks, offline (no model calls): python eval/test_redflags.py
Every phrase here came from a real miss or false alarm found while building the MedGemma dataset (training/sft/)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import redflags  # noqa: E402

FIRE = [
    "chest pain", "patient has chest pain", "urgent: chest pain", "no fever but chest pain since morning",
    "seene mein dard hai aur khansi nahi", "सीने में दर्द है और खांसी नहीं है", "dull ache in center of chest",
    "saans nahi aa rahi", "he can't breathe", "tight breathing", "exertional dyspnea",
    "papa behosh ho gaye", "poor responsiveness", "sudden loss of consciousness", "hard to wake",
    "मम्मी का मुंह टेढ़ा हो गया", "drooping corner of mouth", "numbness in left hand", "left-sided weakness",
    "inability to close one eye", "papa ka ek taraf sunn ho gaya", "मम्मी का एक तरफ कमज़ोर हो गया", "sudden confusion",
    "she had a stroke just now, face drooping",
    "heavy bleeding that won't stop", "accident ke baad bleeding ruk nahi rahi", "bleeding not stopping despite pressure",
    "pregnant woman, bleeding a lot", "heavy vaginal bleeding", "passing clots",
    "limb jerking", "head injury from cane, increased drowsiness and vomiting", "सिर में चोट लगी और उल्टी हो रही है",
    "my baby is 2 months old and has fever",
]
QUIET = [
    "no chest pain", "seene mein dard nahi hai", "he doesn’t have chest pain", "he doesnt have chest pain",
    "not complaining of chest pain, shortness of breath, or palpitations",
    "उन्हें अभी न साँस लेने में दिक्कत है, न छाती में दर्द", "तेज़ उल्टी, बेहोशी, या सीने में दर्द नहीं है",
    "bedridden after his stroke", "mild food poisoning", "coughing fits at night", "he nearly fainted",
    "सिर में चोट लगी थी, सिलाई हो गई", "mild acidity, burning in chest after food", "heavy periods every month",
    "chest congestion with cold", "tingling in fingers", "confusion about medicine dose", "not eating",
    "weakness, high body temperature, throat pain, difficulty speaking",
]


def main():
    bad = [("should fire", t) for t in FIRE if not any(redflags.detect(t))]
    bad += [("should stay quiet", t) for t in QUIET if any(redflags.detect(t))]
    # facts are checked as separate phrases: a denial in one must not reach the next
    if not redflags.detect("not eating", "chest pain")[0]:
        bad.append(("should fire", "facts ['not eating', 'chest pain']"))
    for why, t in bad:
        print(f"FAIL  {why}: {t}")
    print(f"{len(FIRE) + len(QUIET) + 1 - len(bad)}/{len(FIRE) + len(QUIET) + 1} red-flag checks passed")

    data = ROOT / "training/sft/labelled.jsonl"
    if data.exists():  # coverage on the panel-labelled dataset, raw words + structured symptoms as in the pipeline
        rows = [json.loads(line) for line in data.read_text(encoding="utf-8").splitlines() if line.strip()]
        review = ROOT / "training/sft/manual_review.jsonl"  # the recall-first manual review overrides panel labels
        if review.exists():
            final = {json.loads(l)["id"]: json.loads(l)["target"] for l in review.read_text(encoding="utf-8").splitlines() if l.strip()}
            rows = [{**r, "target": final.get(r["id"], r["target"])} for r in rows]
        def hit(r):
            return any(redflags.detect(*(r["turns"] + json.loads(r["facts_json"]).get("symptoms_present", []))))
        em = [r for r in rows if r["target"] == "emergency_112"]
        non = [r for r in rows if r["target"] != "emergency_112"]
        print(f"dataset: emergencies caught by rules {sum(map(hit, em))}/{len(em)}, false alarms {sum(map(hit, non))}/{len(non)}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
