"""Behaviour checks for smart mode.

Part A (fast, no models): the matcher on fixed decisions. Rare capabilities filter, preferences shift the ranking,
language is strict for consultations but negotiable for a quick procedure.
Part B (--e2e, calls the models): whole conversations through POST /triage logic.

Usage: python eval/eval_smart.py [--e2e]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import lexicon, smart_match as sm  # noqa: E402
from app.schemas import CaseFacts, Location, TriageRequest  # noqa: E402
from app.smart import SmartDecision  # noqa: E402

OKHLA, CYBER_CITY, ROHINI = Location(lat=28.5402, lng=77.2700), Location(lat=28.4950, lng=77.0895), Location(lat=28.7495, lng=77.0565)
BY_ID = {p.id: p for p in sm.providers()}
results = []


def check(name, ok, detail=""):
    ok = bool(ok)
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"\n      {detail}" if detail else ""))


def decide(care="clinic_visit", seriousness="soon", specialty="general_medicine", tests=(), services=(), consult_first=False):
    return SmartDecision(status="final", seriousness=seriousness, care_type=care, specialty=specialty, recommended_tests=list(tests),
                         services_needed=list(services), consult_first=consult_first, red_flags=[], missing_info=[], reasoning="test")


def run(text, decision, loc=OKHLA, lang="en"):
    facts = CaseFacts(symptoms_present=[], symptoms_denied=[], detected_language="en")
    out, summary, need = sm.match(decision, lexicon.analyse(text), facts, loc, lang)
    return out, summary, need, [BY_ID[m.caregiver_id] for m in out]


# ---------------------------------------------------------------- Part A
out, summ, need, ps = run("I need an MRI of my knee", decide("diagnostic_test", "routine", "orthopedics", tests=["mri"]))
check("MRI request: every pick has an MRI", ps and all("mri" in p.services for p in ps), summ)
check("MRI request: treated as a rare capability", need.profile == "rare", need.profile)

out, summ, need, ps = run("need a chest x-ray", decide("diagnostic_test", "routine", "none", tests=["xray"]))
check("X-ray request: common test profile, every pick has X-ray", need.profile == "test" and all("xray" in p.services for p in ps), summ)

out, summ, need, ps = run("tetanus shot chahiye", decide(services=["tetanus_shot"]), CYBER_CITY, "hi")
top = out[0]
check("Tetanus shot: procedure profile, low communication", need.profile == "procedure" and not need.high_comm, need.profile)
mismatch = [m for m, p in zip(out, ps) if "Hindi" not in p.languages]
check("Tetanus shot for a Hindi speaker: a nearby English-only clinic may appear, with the trade-off explained",
      all("fine for a quick" in m.reason for m in mismatch), "; ".join(f"{m.name}: {m.reason}" for m in out))

out, summ, need, ps = run("mujhe doctor ko dikhana hai, bukhar hai", decide(), CYBER_CITY, "hi")
check("Consultation for a Hindi speaker: every pick speaks Hindi", all("Hindi" in p.languages for p in ps),
      "; ".join(f"{p.name} {sorted(p.languages)}" for p in ps))

base, _, _, bp = run("need a dermatologist", decide("specialist_visit", specialty="dermatology"))
best, summ, _, qp = run("need the best dermatologist, last one was useless", decide("specialist_visit", specialty="dermatology"))
check("'best' raises the top pick's rating (or keeps the best)", qp[0].rating >= bp[0].rating, f"{bp[0].name} ★{bp[0].rating} -> {qp[0].name} ★{qp[0].rating}")

far, _, _, _ = run("need a dermatologist", decide("specialist_visit", specialty="dermatology"), ROHINI)
near, _, _, _ = run("need a dermatologist nearby", decide("specialist_visit", specialty="dermatology"), ROHINI)
check("'nearby' does not pick a farther place", near[0].distance_km <= far[0].distance_km, f"{far[0].distance_km} km -> {near[0].distance_km} km")

pricey, _, _, pp = run("need an ultrasound", decide("diagnostic_test", tests=["ultrasound"], specialty="none"))
cheap, _, _, cp = run("need a cheap ultrasound", decide("diagnostic_test", tests=["ultrasound"], specialty="none"))
check("'cheap' does not pick a dearer place", (cp[0].fee or 0) <= (pp[0].fee or 0), f"₹{pp[0].fee} -> ₹{cp[0].fee}")

out, summ, need, ps = run("ghar pe injection lagwana hai", decide("home_nursing", services=["injection"]))
check("Home injection: only home-visit providers whose radius covers the user",
      ps and all(p.home_visit and (m.distance_km or 0) <= (p.radius or 15) for m, p in zip(out, ps)), summ)

out, summ, need, ps = run("video call pe doctor se baat karni hai", decide("teleconsultation"))
check("Video consult: only teleconsult-capable doctors, distance ignored", ps and all(p.teleconsult for p in ps) and need.profile == "online", summ)

out, summ, need, ps = run("lady doctor chahiye periods ke liye", decide("specialist_visit", specialty="gynecology"), lang="hinglish")
check("Lady doctor request: top pick has a female doctor", ps and ps[0].female_doctor, f"{ps[0].name}")

out, summ, need, ps = run("burning urination 3 days", decide("specialist_visit", specialty="urology", tests=["urine_test", "urine_culture"]))
check("Urinary symptoms: urologist (or GP fallback) favouring places that run the urine tests",
      all("urology" in p.specialties or "general_medicine" in p.specialties for p in ps) and need.soft_tests, summ)
cons, summ2, need2, ps2 = run("just need a consultation for burning urination, no tests",
                              decide("specialist_visit", specialty="urology", tests=["urine_test", "urine_culture"], consult_first=True))
check("Consult-only: tests become information, not a ranking factor", not need2.soft_tests and need2.info_tests, summ2)

check("Every pick carries factor chips and a reason", all(m.factors and m.reason for m in out + cons))

# ---------------------------------------------------------------- Part B
if "--e2e" in sys.argv:
    from app.pipeline import run as triage

    def ask(text, history=(), loc=OKHLA):
        return triage(TriageRequest(user_input=text, location=loc, conversation_history=list(history), mode="smart"))

    r = ask("I am bleeding")
    check("e2e: 'I am bleeding' asks about source/amount/cause first", bool(r.follow_up_question), r.follow_up_question or r.care_type)
    r2 = ask("cut my finger while chopping vegetables, it stopped after pressing", [{"role": "user", "content": "I am bleeding"},
             {"role": "assistant", "content": r.follow_up_question or "Where is the bleeding from?"}])
    check("e2e: minor cut -> dressing / tetanus at a nearby place", r2.care_type in ("clinic_visit", "hospital_visit") and r2.matched_caregivers,
          f"{r2.care_type} {r2.services_needed} {r2.match_summary}")
    r = ask("accident ke baad bleeding ruk nahi rahi")
    check("e2e: uncontrolled bleeding is an emergency", r.care_type == "emergency_112", r.care_type)
    r = ask("I need an MRI of my lower back")
    check("e2e: MRI by name -> every pick has MRI", r.matched_caregivers and all("mri" in BY_ID[m.caregiver_id].services for m in r.matched_caregivers),
          r.match_summary)
    r = ask("peshab mein jalan ho rahi hai 3 din se")
    check("e2e: urinary burning -> urine tests suggested", any("Urine" in t for t in r.recommended_tests), f"{r.specialty} {r.recommended_tests}")
    r = ask("kutte ne kaat liya")
    check("e2e: dog bite -> anti-rabies today", "Anti-rabies vaccine" in r.services_needed and r.seriousness == "urgent",
          f"{r.seriousness} {r.services_needed}")

print(f"\n{sum(results)}/{len(results)} checks passed")
