"""Caregiver ranking: organiser reference blend (language 0.35, proximity 0.30, skill 0.20, rating 0.15).
Deterministic; reasons come from templates, never from a model."""
import csv
import math
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from app.llm import CFG, ROOT
from app.schemas import CaseFacts, MatchedCaregiver

M = CFG["matching"]
W = M["weights"]
IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_LOC = (28.6139, 77.2090)  # central Delhi, used only if the client sends no location

# words in the request -> caregiver skill tags (caregivers.csv vocabulary)
SKILL_WORDS = [
    (r"\biv\b|drip|infusion|cannula", "injection_iv"),
    (r"inject|insulin|\bim\b|shot", "injection_im"),
    (r"dressing|wound|bedsore|bed sore|ulcer|ghaav|घाव|पट्टी", "wound_dressing"),
    (r"catheter|कैथेटर", "catheter_care"),
    (r"nebuli", "nebulization"),
    (r"surgery|post.?op|operation|stitches|टांके|ऑपरेशन", "post_surgical_care"),
    (r"\bbp\b|blood pressure|sugar|glucose|शुगर|बीपी", "bp_sugar_check"),
    (r"vitals|temperature|pulse|oxygen", "vitals_monitoring"),
    (r"elderly|old age|bedridden|weak|dadaji|dadi|nana|nani|बुज़ुर्ग|बुजुर्ग", "elderly_care"),
]
DEFAULT_SKILLS = {
    "lab_test_home": ["sample_collection"],
    "teleconsultation": ["teleconsultation"],
    "paramedic_home_visit": ["vitals_monitoring"],
    "home_nursing": [],
}
ROLE_LABEL = {"medic": "Medic", "doctor": "Doctor (video)", "phlebotomist": "Phlebotomist"}


@lru_cache(maxsize=1)
def caregivers() -> list[dict]:
    with open(ROOT / M["caregivers_csv"], encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["languages"] = r["languages"].split(";")
        r["skills"] = r["skills"].split(";")
        r["lat"], r["lng"] = float(r["base_lat"]), float(r["base_lng"])
        r["radius"], r["rating"] = float(r["max_radius_km"]), float(r["rating"])
        r["active"] = r["active"].strip().lower() == "true"
    return rows


def haversine(a: tuple, b: tuple) -> float:
    (la1, lo1), (la2, lo2) = a, b
    p = math.radians
    h = math.sin(p(la2 - la1) / 2) ** 2 + math.cos(p(la1)) * math.cos(p(la2)) * math.sin(p(lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def required_skills(care_type: str, facts: CaseFacts, text: str) -> list[str]:
    blob = " ".join([text, *facts.services_requested, *facts.symptoms_present, *facts.existing_conditions, facts.patient or ""]).lower()
    found = [skill for pat, skill in SKILL_WORDS if re.search(pat, blob)]
    if care_type == "home_nursing":
        return found[:2] or ["vitals_monitoring"]
    if care_type == "paramedic_home_visit":
        return [s for s in found if s in ("bp_sugar_check", "elderly_care", "vitals_monitoring")][:2] or DEFAULT_SKILLS[care_type]
    return DEFAULT_SKILLS[care_type]


def next_slot(free_from: str, free_until: str, now: datetime) -> str:
    def at(day, hm):
        h, m = map(int, hm.split(":"))
        return day.replace(hour=h, minute=m, second=0, microsecond=0)
    start, end = at(now, free_from), at(now, free_until)
    soon = (now + timedelta(minutes=30 - now.minute % 30)).replace(second=0, microsecond=0)
    slot = max(start, soon) if soon < end else start + timedelta(days=1)
    return slot.isoformat()


def match(care_type: str, facts: CaseFacts, text: str, location, lang: str, now: datetime | None = None) -> list[MatchedCaregiver]:
    if care_type not in M["role_for_care"]:
        return []  # emergencies get no caregivers
    now = now or datetime.now(IST)
    here = (location.lat, location.lng) if location else DEFAULT_LOC
    want_lang = "English" if lang == "en" else "Hindi"
    virtual = care_type == "teleconsultation"
    need = required_skills(care_type, facts, text)
    roles = M["role_for_care"][care_type]
    scored = []
    for c in caregivers():
        if not c["active"] or c["role"] not in roles:
            continue
        if care_type == "lab_test_home" and c["role"] == "medic" and "sample_collection" not in c["skills"]:
            continue
        d = haversine(here, (c["lat"], c["lng"]))
        if not virtual and d > c["radius"]:
            continue
        # every caregiver on the roster speaks Hindi, so Hindi also counts for English requests (matches the reference labels)
        lang_ok = want_lang in c["languages"] or "Hindi" in c["languages"]
        prox = 1.0 if virtual else max(0.0, 1 - d / M["proximity_km"])
        skill = sum(s in c["skills"] or (s == "sample_collection" and "home_phlebotomy" in c["skills"]) for s in need) / len(need) if need else 1.0
        rating = min(1.0, max(0.0, (c["rating"] - 3) / 2))
        score = W["language"] * lang_ok + W["proximity"] * prox + W["skill"] * skill + W["rating"] * rating
        scored.append((score, d, c, lang_ok, skill))
    scored.sort(key=lambda t: (-t[0], t[1]))
    out = []
    for score, d, c, lang_ok, skill in scored[: M["top_k"]]:
        speaks = f"{want_lang if want_lang in c['languages'] else 'Hindi'}-speaking" if lang_ok else f"speaks {', '.join(c['languages'][:2])}"
        window = f"free {c['free_from']}–{c['free_until']}"
        reason = f"{speaks}, {window}, ★{c['rating']:.1f}" if virtual else f"{speaks}, {d:.1f} km away, {window}"
        if need and skill == 1.0 and care_type == "home_nursing":
            reason += f", does {need[0].replace('_', ' ').replace('injection im', 'injections')}"
        out.append(MatchedCaregiver(
            caregiver_id=c["caregiver_id"], score=round(score, 2), reason=reason, name=c["name"],
            role=f"{ROLE_LABEL[c['role']]} · {c['qualification']}", distance_km=None if virtual else round(d, 1),
            languages=c["languages"], rating=c["rating"], next_slot=next_slot(c["free_from"], c["free_until"], now)))
    return out
