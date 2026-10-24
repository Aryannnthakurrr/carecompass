"""Curated smart-mode queries replayed through the real matcher (no models, no API): same need, different words.
Fixed decisions and a fixed clock (Sun 25 Oct 2026, 11:00) so the picks are reproducible.
    python eval/curated_queries.py"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import lexicon, smart_match as sm  # noqa: E402
from app.schemas import CaseFacts, Location  # noqa: E402
from app.smart import SmartDecision  # noqa: E402

OKHLA, CYBER_CITY = Location(lat=28.5402, lng=77.2700), Location(lat=28.4950, lng=77.0895)
NOW = datetime(2026, 10, 25, 11, 0)


def decide(care="clinic_visit", seriousness="soon", specialty="general_medicine", tests=(), services=()):
    return SmartDecision(status="final", seriousness=seriousness, care_type=care, specialty=specialty, recommended_tests=list(tests),
                         services_needed=list(services), consult_first=False, red_flags=[], missing_info=[], reasoning="curated")


QUERIES = [
    ("need a dermatologist", decide("specialist_visit", specialty="dermatology"), OKHLA, "en"),
    ("need the best dermatologist, last one was useless", decide("specialist_visit", specialty="dermatology"), OKHLA, "en"),
    ("need an ultrasound", decide("diagnostic_test", tests=["ultrasound"], specialty="none"), OKHLA, "en"),
    ("need a cheap ultrasound", decide("diagnostic_test", tests=["ultrasound"], specialty="none"), OKHLA, "en"),
    ("I need an MRI of my knee", decide("diagnostic_test", "routine", "orthopedics", tests=["mri"]), OKHLA, "en"),
    ("tetanus shot chahiye", decide(services=["tetanus_shot"]), CYBER_CITY, "hi"),
    ("burning urination 3 days", decide("specialist_visit", specialty="urology", tests=["urine_test", "urine_culture"]), OKHLA, "en"),
    ("lady doctor chahiye periods ke liye", decide("specialist_visit", specialty="gynecology"), OKHLA, "hinglish"),
    ("ghar pe injection lagwana hai", decide("home_nursing", services=["injection"]), OKHLA, "hi"),
    ("video call pe doctor se baat karni hai", decide("teleconsultation"), OKHLA, "hi"),
]

if __name__ == "__main__":
    for text, decision, loc, lang in QUERIES:
        facts = CaseFacts(symptoms_present=[], symptoms_denied=[], detected_language=lang)
        out, summary, need = sm.match(decision, lexicon.analyse(text), facts, loc, lang, now=NOW)
        print(f"\n{text!r}  [{need.profile}]\n  {summary}")
        for m in out:
            print(f"  - {m.name}: {' · '.join(m.factors or [])}\n      {m.reason}")
