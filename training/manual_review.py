"""Recall-first manual review of the panel's labels -> training/sft/manual_review.jsonl (no API calls).

Policy (24 Oct 2026): missing an emergency is far worse than a false alarm. A row becomes emergency_112 when a finding
could plausibly be time-critical for THIS patient: new drowsiness, excessive sleepiness, confusion or abnormal behaviour;
fainting or near-fainting; an elderly or frail person who is not eating/drinking together with weakness, lethargy or
sleepiness, or who suddenly cannot get up or walk; dehydration signs; a lethargic or unusually sleepy child; a baby not
feeding; high fever in a young child WITH a danger sign (lethargy, weakness, not eating, vomiting, rash); blood sugar
around 400 or more; very high BP with blurred vision; reduced urine output; severe abdominal pain; abnormal BP in
pregnancy; a fall from height in the elderly; a wound that reopens and bleeds after pressure.
Kept as they were: fractures without other danger signs, single vomiting episodes, chronic fatigue, mild complaints,
toddler fever without a danger sign (a same-day home visit).

The 666 rows reviewed were the panel's non-emergency labels with symptoms and a risk factor (see select() below);
106 were relabelled. The 17 review rows where an adjudicator overruled an emergency vote (all "2-year-old, fever ~103F")
are added with the policy's label instead of being dropped.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.schemas import Decision  # noqa: E402

SFT = ROOT / "training/sft"
RISK = re.compile(r"drows|sleep|letharg|confus|disorient|faint|dizz|vertigo|unsteady|vomit|bleed|blood|breath|wheez|chest|palpit|"
                  r"weak|unable|inability|cannot|can't|not eating|not drinking|refus|urine|dehydrat|unrespons|not respond|seiz|jerk|"
                  r"high fever|103|104|105|fever|pain|swell|rash|burn|bite|injur|fall|fell|head|numb|tingl|speech|vision|cold|blue|pale|"
                  r"sugar|glucose|bp |blood pressure|4\d\d|3\d\d|acting|strange|crying|limp|stiff|neck", re.I)

# positions in the review order (non-emergency candidates sorted by label, then id) judged emergency_112
EMERGENCY_AT = {
    1, 27, 44, 68, 73, 78, 85, 105,
    191, 196, 200, 204, 206, 207, 208, 211, 212, 219, 220, 223, 226, 228, 230, 233, 245, 246, 247, 248, 250, 254, 255, 256, 257,
    260, 264, 267, 268, 269, 270, 271, 278, 279, 283, 284, 286, 287, 301, 305, 306, 314, 315, 317, 319, 321, 326, 327, 330, 331,
    332, 336, 337, 342, 346, 349, 351, 364, 366, 367, 368, 372, 373, 379, 385, 387, 388, 389, 390, 394, 395, 396, 397, 398,
    405, 418, 419, 421, 423, 424, 434, 435, 445, 448, 451, 456, 484, 486, 488, 489, 490, 491, 493, 496, 497, 498, 506, 526,
}
# second-sign rule (chosen 25 Oct): an elderly person who is only weak / not eating / tired stays a home visit; these
# relabels had no second sign (drowsiness, confusion, cannot get up, not drinking, dizziness or fainting) and are reverted
SECOND_SIGN_REVERTED = {283, 284, 286, 321, 337, 349, 372, 490, 493}
EMERGENCY_AT -= SECOND_SIGN_REVERTED
# overruled-emergency review rows (in file order): toddler fever WITH a danger sign
OVERRULED_EMERGENCY_AT = {0, 1, 3, 5, 9, 16}


def read(name):
    return [json.loads(l) for l in (SFT / name).read_text(encoding="utf-8").splitlines() if l.strip()]


def select(labelled):
    out = []
    for r in labelled:
        if r["target"] == "emergency_112" or r["source"] != "synthetic":
            continue
        f = json.loads(r["facts_json"])
        sym = f.get("symptoms_present", [])
        if not sym:
            continue
        risky = bool(set(f.get("special_population", [])) & {"infant", "child", "elderly", "pregnant"})
        if risky or f.get("onset") == "sudden" or f.get("severity") == "severe" or any(RISK.search(s) for s in sym + f.get("vitals_reported", [])):
            out.append(r)
    return sorted(out, key=lambda r: (r["target"], r["id"]))


def emergency(r):
    sym = json.loads(r["facts_json"]).get("symptoms_present", [])
    flags = [s for s in sym][:3]
    named = " and ".join(flags[:2]) if flags else "warning signs"
    return Decision(status="final", care_type="emergency_112", red_flags=flags, missing_info=[],
                    reasoning=f"Possible time-critical signs ({named}) in this patient; emergency care now.")


def main():
    labelled = read("labelled.jsonl")
    order = select(labelled)
    assert len(order) == 666, f"review set changed ({len(order)} rows): re-review before applying"
    out = []
    for k in sorted(EMERGENCY_AT):
        r = order[k]
        out.append({**r, "written_for": r.get("written_for", r["target"]), "panel_target": r["target"], "target": "emergency_112",
                    "decision": emergency(r).model_dump(), "how": "manual review (recall-first)"})
    overruled = [r for r in read("review.jsonl") if r["why"].startswith("adjudicator overruled")]
    for k, r in enumerate(overruled):
        if k in OVERRULED_EMERGENCY_AT:
            d = emergency(r)
        else:
            d = Decision(status="final", care_type="paramedic_home_visit", red_flags=[], missing_info=[],
                         reasoning="High fever in a young child without danger signs needs a same-day home assessment.")
        out.append({**r, "target": d.care_type, "decision": d.model_dump(), "keep": True, "how": "manual review (overruled vote)"})
    (SFT / "manual_review.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in out), encoding="utf-8")
    print(f"wrote {len(out)} rows: {len(EMERGENCY_AT)} relabelled to emergency ({len(SECOND_SIGN_REVERTED)} reverted by the second-sign rule), {len(overruled)} overruled-vote rows "
          f"({len(OVERRULED_EMERGENCY_AT)} emergency)")


if __name__ == "__main__":
    main()
