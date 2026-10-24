"""Hand-written "ask a follow-up" examples for the MedGemma dataset -> training/sft/manual.jsonl (no API calls).

The generated data has almost none: writers asked for vague messages still added symptoms, and the organiser's
vague messages all collapse to the facts {} or {"patient": "mother"}. These cover what the structurer actually emits
for vague requests: a patient with no complaint, a generic service ("doctor", "nurse", "consultation"), a setting
or a preference alone. None names a symptom or a specific procedure, so each needs one plain question.
build_sft.py export merges this file in.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import triage  # noqa: E402
from app.schemas import CaseFacts, Decision  # noqa: E402

ASK_WHAT = "What is the main health concern or symptom, and who is it for?"
CASES = [  # (facts, care_type default, question)
    ({"patient": "father"}, "teleconsultation", "What is the main health concern for your father?"),
    ({"patient": "grandmother, 80 years", "special_population": ["elderly"]}, "teleconsultation", "What problem is your grandmother having, or what service does she need?"),
    ({"patient": "son, 5 years", "special_population": ["child"]}, "teleconsultation", "What is bothering your son, and since when?"),
    ({"patient": "wife"}, "teleconsultation", "What health concern does your wife have?"),
    ({"patient": "self"}, "teleconsultation", "What symptoms are you having, or what service do you need?"),
    ({"patient": "baby", "special_population": ["infant"]}, "teleconsultation", "How old is the baby, and what is worrying you?"),
    ({"patient": "father, 70 years", "special_population": ["elderly"], "setting_requested": "home_visit", "preferences": ["home"]}, "paramedic_home_visit", "What is the problem your father is having, so we send the right person home?"),
    ({"services_requested": ["doctor"]}, "teleconsultation", ASK_WHAT),
    ({"services_requested": ["doctor visit"], "setting_requested": "home_visit", "preferences": ["home"]}, "paramedic_home_visit", "What is the main complaint, and who is the doctor for?"),
    ({"services_requested": ["nurse"]}, "home_nursing", "What does the nurse need to do, for example an injection, dressing or catheter care?"),
    ({"services_requested": ["nurse"], "patient": "mother", "setting_requested": "home_visit"}, "home_nursing", "What care does your mother need from the nurse?"),
    ({"services_requested": ["consultation"]}, "teleconsultation", "What would you like to consult the doctor about?"),
    ({"services_requested": ["appointment"]}, "teleconsultation", "What is the appointment for: a symptom, a test or a procedure?"),
    ({"services_requested": ["help"]}, "teleconsultation", ASK_WHAT),
    ({"services_requested": ["home visit"], "setting_requested": "home_visit"}, "paramedic_home_visit", "What is the reason for the home visit, and who is it for?"),
    ({"services_requested": ["booking"], "patient": "grandfather"}, "teleconsultation", "What should we book for your grandfather: a doctor, a test or a nurse, and for what problem?"),
    ({"services_requested": ["medical advice"]}, "teleconsultation", "What would you like advice about?"),
    ({"services_requested": ["caregiver"], "patient": "mother", "special_population": ["elderly"]}, "home_nursing", "What kind of help does your mother need: daily care, a procedure, or a health problem checked?"),
    ({"services_requested": ["specialist"]}, "teleconsultation", "Which problem do you want to see a specialist for?"),
    ({"services_requested": ["test"]}, "lab_test_home", "Which test do you need, or what symptoms is it for?"),
    ({"services_requested": ["medicine"]}, "teleconsultation", "Which medicine is it about, and what is it for?"),
    ({"setting_requested": "home_visit"}, "paramedic_home_visit", "Who needs the visit and what is the problem?"),
    ({"setting_requested": "video_consult"}, "teleconsultation", "What would you like to discuss with the doctor on video?"),
    ({"setting_requested": "lab_test"}, "lab_test_home", "Which test do you need done?"),
    ({"preferences": ["budget"]}, "teleconsultation", ASK_WHAT),
    ({"preferences": ["soon"]}, "teleconsultation", "What is the problem, and how serious does it feel right now?"),
    ({"preferences": ["female"]}, "teleconsultation", "What is the health concern you would like to see a lady doctor for?"),
    ({"preferences": ["nearby", "quality"]}, "teleconsultation", ASK_WHAT),
    ({"preferences": ["online"], "setting_requested": "video_consult"}, "teleconsultation", "What would you like to ask the doctor online?"),
    ({"patient": "mother", "preferences": ["soon"], "setting_requested": "unspecified"}, "teleconsultation", "What is happening with your mother right now?"),
    ({"patient": "daughter, 12 years", "special_population": ["child"], "services_requested": ["doctor"]}, "teleconsultation", "What is bothering your daughter, and since when?"),
    ({"existing_conditions": ["diabetes"]}, "teleconsultation", "What do you need help with for the diabetes: a test, a medicine question, or a new symptom?"),
    ({"existing_conditions": ["recent surgery"], "patient": "father"}, "teleconsultation", "What does your father need after the surgery: a dressing, a check-up, or is there a new problem?"),
    ({"patient": "pregnant wife", "special_population": ["pregnant"]}, "teleconsultation", "What is your wife's concern right now, and how many weeks pregnant is she?"),
]


def main():
    rows = []
    for facts, care, q in CASES:
        f = CaseFacts(symptoms_present=[], symptoms_denied=[], detected_language="en", **facts)
        fj = triage._facts_json(f)
        d = Decision(status="need_more_info", care_type=care, red_flags=[], missing_info=[q],
                     reasoning="No symptom or specific service given yet, so one question first.")
        h = hashlib.md5(fj.encode()).hexdigest()[:10]
        rows.append({"id": f"man-{h}", "batch": f"man-{h}", "source": "manual", "target": "need_more_info",
                     "written_for": "need_more_info", "lang": "en", "turns": [], "facts_json": fj,
                     "rules_raw": [], "rules_facts": [], "decision": d.model_dump(), "how": "hand-written"})
    out = ROOT / "training/sft/manual.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"wrote {len(rows)} rows -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
