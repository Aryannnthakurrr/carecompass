"""Smart-mode provider matching over data/providers.csv.

1. Work out what is needed: services asked for by name are hard requirements; procedures the classifier says are needed
   are hard too; first tests a doctor would order are soft (they favour one-stop places) unless the user wants only a
   consultation. The specialty, home visit, video call or hospital setting narrow the pool further.
2. Pick a weight profile from the kind of need. A rare capability (MRI) leaves few candidates, so rating and reach
   matter; a common test lets rating and distance decide; a quick procedure (tetanus shot) is mostly about the
   nearest open place; a consultation weighs a shared language and the doctor's rating heavily.
3. Shift the weights with the user's own words (best, nearby, cheap, today, lady doctor) and with seriousness.
4. Language is a multiplier, not a tick-box: a mismatch costs a consultation most of its score, a quick procedure little.
5. Explain every pick: factor chips plus one line on why it beat the next option.
"""
import csv
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

from app.lexicon import SERVICES, SPECIALTIES, Hints, label, map_free_text
from app.llm import CFG, ROOT
from app.schemas import MatchedCaregiver

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_LOC = (28.6139, 77.2090)
USER_LANGS = {"en": {"English"}, "hi": {"Hindi"}, "hinglish": {"Hindi", "English"}}
HOME_TYPES = {"home_care", "home_lab"}
TYPE_LABEL = {"hospital": "Hospital", "polyclinic": "Polyclinic", "gp_clinic": "Clinic", "specialist_clinic": "Specialist clinic",
              "diagnostic_centre": "Diagnostic centre", "lab": "Pathology lab", "physio_centre": "Physiotherapy centre",
              "home_care": "Home-care medic", "home_lab": "Home sample collection", "teleconsult_doctor": "Video doctor"}
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

#            distance rating language availability   scale_km  max_km
PROFILES = {
    "rare":      (dict(distance=.30, rating=.35, language=.20, availability=.15), 15, 40),
    "test":      (dict(distance=.35, rating=.30, language=.15, availability=.20), 8, 25),
    "procedure": (dict(distance=.50, rating=.10, language=.15, availability=.25), 3, 20),
    "consult":   (dict(distance=.25, rating=.30, language=.30, availability=.15), 6, 25),
    "home":      (dict(distance=.35, rating=.25, language=.25, availability=.15), 5, 15),
    "online":    (dict(distance=0.0, rating=.45, language=.35, availability=.20), 1, 1e9),
}
RARE_BELOW = 0.12  # a capability offered by fewer than 12% of providers counts as rare


@dataclass
class Provider:
    id: str
    name: str
    type: str
    specialties: set
    services: set
    languages: set
    lat: float
    lng: float
    radius: Optional[float]
    rating: float
    reviews: int
    fee: Optional[int]
    open_from: str
    open_until: str
    closed_on: str
    is_24x7: bool
    home_visit: bool
    teleconsult: bool
    female_doctor: bool


@lru_cache(maxsize=1)
def providers() -> list[Provider]:
    out = []
    with open(ROOT / CFG["smart"]["providers_csv"], encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["active"] != "True":
                continue
            split = lambda s: {x for x in s.split(";") if x}  # noqa: E731
            out.append(Provider(
                r["provider_id"], r["name"], r["type"], split(r["specialties"]), split(r["services"]), split(r["languages"]),
                float(r["lat"]), float(r["lng"]), float(r["service_radius_km"]) if r["service_radius_km"] else None,
                float(r["rating"]), int(r["reviews"]), int(r["fee_inr"]) if r["fee_inr"] else None, r["open_from"], r["open_until"],
                r["closed_on"], r["is_24x7"] == "True", r["home_visit"] == "True", r["teleconsult"] == "True", r["female_doctor"] == "True"))
    return out


@lru_cache(maxsize=1)
def coverage() -> dict:
    ps = providers()
    return {tag: sum(tag in p.services for p in ps) / len(ps) for tag in SERVICES}


def haversine(a, b) -> float:
    (la1, lo1), (la2, lo2) = a, b
    r = math.radians
    h = math.sin(r(la2 - la1) / 2) ** 2 + math.cos(r(la1)) * math.cos(r(la2)) * math.sin(r(lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def _hm(day: datetime, hm: str) -> datetime:
    h, m = map(int, hm.split(":"))
    return day.replace(hour=h, minute=m, second=0, microsecond=0)


def _clock(t: datetime) -> str:
    return t.strftime("%I:%M %p").lstrip("0").replace(":00", "").lower()


def availability(p: Provider, now: datetime) -> tuple[float, str, str]:
    """-> (score, short text, next slot ISO)."""
    soon = (now + timedelta(minutes=30 - now.minute % 30)).replace(second=0, microsecond=0)
    if p.is_24x7:
        return 1.0, "Open 24×7", soon.isoformat()
    closed = p.closed_on.split(";")
    start, end = _hm(now, p.open_from), _hm(now, p.open_until)
    if DAYS[now.weekday()] not in closed and start <= now < end:
        return 1.0, f"Open now · till {_clock(end)}", soon.isoformat()
    if DAYS[now.weekday()] not in closed and now < start:
        return 0.6, f"Opens {_clock(start)}", start.isoformat()
    nxt = start + timedelta(days=1)
    while DAYS[nxt.weekday()] in closed:
        nxt += timedelta(days=1)
    day = "tomorrow" if (nxt.date() - now.date()).days == 1 else DAYS[nxt.weekday()]
    return 0.25, f"Opens {day} {_clock(nxt)}", nxt.isoformat()


def rating_score(p: Provider) -> float:
    adjusted = (p.rating * p.reviews + 3.9 * 25) / (p.reviews + 25)  # few reviews -> pulled toward the average
    return min(1.0, max(0.0, (adjusted - 3.0) / 2.0))


@dataclass
class Need:
    required: list = field(default_factory=list)   # hard: provider must offer every one
    soft_tests: list = field(default_factory=list)  # favoured: one-stop places
    info_tests: list = field(default_factory=list)  # shown to the user, not used for matching (consult-only)
    specialty: Optional[str] = None
    consult: bool = False
    setting: str = "in_person"                       # in_person | home | online | hospital
    profile: str = "consult"
    high_comm: bool = True


def build_need(decision, hints: Hints, facts) -> Need:
    explicit = list(dict.fromkeys(hints.services + map_free_text(facts.services_requested)))
    wants_consult = "consultation" in explicit
    explicit = [s for s in explicit if s != "consultation"]
    consult_only = "consult_only" in hints.preferences or decision.consult_first and not explicit
    tests = list(dict.fromkeys(decision.recommended_tests + hints.suggested_tests))
    procs = list(dict.fromkeys(decision.services_needed + hints.suggested_services))
    n = Need()
    n.required = list(dict.fromkeys(explicit + [p for p in procs if p not in explicit]))
    if "injection" in n.required and any(SERVICES[r][1] == "vaccine" for r in n.required):
        n.required.remove("injection")  # "tetanus injection" is the tetanus shot
    if decision.care_type == "diagnostic_test" and not consult_only:
        n.required += [t for t in tests if t not in n.required][:2]  # the scan or test IS the visit
        tests = []
    n.soft_tests = [] if consult_only else [t for t in tests if t not in n.required]
    n.info_tests = tests if consult_only else []
    spec = decision.specialty if decision.specialty != "none" else (hints.specialties[0] if hints.specialties else None)
    n.specialty = spec
    # a named procedure (tetanus shot, dressing) needs a place that does it, not a consultation, unless one was asked for
    n.consult = wants_consult or consult_only or decision.care_type in ("specialist_visit", "teleconsultation") or not n.required
    # setting
    if "online" in hints.preferences or (decision.care_type == "teleconsultation" and "nearby" not in hints.preferences):
        n.setting = "online"
    elif decision.care_type in ("home_nursing", "paramedic_home_visit", "lab_test_home") or "home" in hints.preferences:
        n.setting = "home"
    elif decision.care_type == "hospital_visit" or "hospital" in hints.preferences:
        n.setting = "hospital"
    if n.setting == "home" and decision.care_type == "lab_test_home":
        n.required = list(dict.fromkeys(n.required + ["sample_collection_home"]))
    if n.setting == "home" and decision.care_type == "paramedic_home_visit" and not n.required:
        n.required = ["home_care"]
    # profile
    cov = coverage()
    kinds = {SERVICES[s][1] for s in n.required}
    if n.setting == "online":
        n.profile = "online"
    elif n.setting == "home":
        n.profile = "home"
    elif any(cov.get(s, 0) < RARE_BELOW for s in n.required):
        n.profile = "rare"
    elif n.required and kinds & {"imaging", "lab", "cardiac", "scope", "neuro"} and not n.consult:
        n.profile = "test"
    elif n.required and not n.consult:
        n.profile = "procedure"
    else:
        n.profile = "consult"
    n.high_comm = n.consult or any(SERVICES[s][2] == "high" for s in n.required)
    if n.profile == "procedure" and all(SERVICES[s][2] == "low" for s in n.required):
        n.high_comm = False
    return n


def _weights(need: Need, prefs: list, seriousness: str):
    w, scale, max_km = PROFILES[need.profile]
    w = dict(w, cost=0.0, coverage=0.0)
    if need.soft_tests:
        w["coverage"] = 0.20
    if seriousness == "urgent":
        w["availability"] *= 1.8
    if "quality" in prefs:
        w["rating"] *= 2.2
    if "nearby" in prefs and need.profile != "online":
        w["distance"] *= 2.2
        scale *= 0.6
    if "budget" in prefs:
        w["cost"] = 0.20
    if "soon" in prefs:
        w["availability"] *= 2.0
    total = sum(w.values())
    return {k: v / total for k, v in w.items()}, scale, max_km


def _short_km(d: float) -> str:
    return f"{d:.1f} km" if d < 10 else f"{d:.0f} km"


def match(decision, hints: Hints, facts, location, lang: str, now: Optional[datetime] = None, top_k: int = 3):
    """-> (matched providers with factor chips and reasons, one-line summary of how they were chosen, need)."""
    now = now or datetime.now(IST)
    here = (location.lat, location.lng) if location else DEFAULT_LOC
    need = build_need(decision, hints, facts)
    prefs = hints.preferences
    w, scale, max_km = _weights(need, prefs, decision.seriousness)
    want = USER_LANGS.get(lang, {"Hindi", "English"})

    def eligible(p: Provider, d: float, strict_required=True) -> bool:
        if need.setting == "online":
            if not p.teleconsult or "consultation" not in p.services:
                return False
        elif need.setting == "home":
            if not p.home_visit or d > (p.radius or max_km):
                return False
        else:
            if p.type in HOME_TYPES or p.type == "teleconsult_doctor" or d > max_km:
                return False
            if need.setting == "hospital" and p.type != "hospital":
                return False
        if strict_required and not set(need.required) <= p.services:
            return False
        if need.consult:
            if "consultation" not in p.services:
                return False
            if need.specialty and need.specialty not in p.specialties and "general_medicine" not in p.specialties and need.setting != "online":
                return False
        return True

    pool = [(p, haversine(here, (p.lat, p.lng))) for p in providers()]
    cands = [(p, d) for p, d in pool if eligible(p, d)]
    relaxed = False
    if not cands and need.required:  # nobody offers everything: keep whoever offers the most
        relaxed = True
        cands = [(p, d) for p, d in pool if eligible(p, d, strict_required=False) and set(need.required) & p.services]
    if not cands:
        return [], "No provider in range offers this yet; try a video consultation or widen the area.", need

    fees = [p.fee for p, _ in cands if p.fee]
    fmin, fmax = (min(fees), max(fees)) if fees else (0, 1)
    scored = []
    for p, d in cands:
        av, av_text, slot = availability(p, now)
        f = {
            "distance": 1.0 if need.setting == "online" else math.exp(-d / scale),
            "rating": rating_score(p),
            "language": 1.0 if p.languages & want else 0.0,
            "availability": av,
            "cost": 1.0 - ((p.fee or fmax) - fmin) / max(1, fmax - fmin),
            "coverage": (sum(t in p.services for t in need.soft_tests) / len(need.soft_tests)) if need.soft_tests else 0.0,
        }
        mult = 1.0
        if not f["language"]:
            mult *= 0.45 if need.high_comm else 0.85  # a consultation needs a shared language; a jab barely does
        spec_fit = not need.specialty or need.specialty in p.specialties or not need.consult
        if need.consult and need.specialty and not spec_fit:
            mult *= 0.75  # a general physician when the right specialist isn't in range
        if "female" in prefs and not p.female_doctor:
            mult *= 0.6
        if relaxed:
            mult *= len(set(need.required) & p.services) / len(need.required)
        score = sum(w[k] * f[k] for k in w) * mult
        scored.append(dict(p=p, d=d, f=f, score=score, av_text=av_text, slot=slot, spec_fit=spec_fit, contrib={k: w[k] * f[k] * mult for k in w}))
    scored.sort(key=lambda s: (-s["score"], s["d"]))
    top = scored[:top_k]

    nearest_same_lang = min((s["d"] for s in scored if s["f"]["language"]), default=None)
    out = []
    for i, s in enumerate(top):
        p = s["p"]
        out.append(MatchedCaregiver(
            caregiver_id=p.id, score=round(min(1.0, s["score"]), 2), reason=_reason(s, top, i, need, prefs, nearest_same_lang),
            name=p.name, role=_role(p, need), distance_km=None if need.setting == "online" else round(s["d"], 1),
            languages=sorted(p.languages), rating=p.rating, next_slot=s["slot"], factors=_factors(s, need, prefs), fee_inr=p.fee,
            provider_type=p.type))
    return out, _summary(need, len(cands), relaxed, prefs, scored, want), need


def _role(p: Provider, need: Need) -> str:
    spec = next((SPECIALTIES[s][0] for s in p.specialties if s == need.specialty), None) or \
        (SPECIALTIES[sorted(p.specialties)[0]][0] if len(p.specialties) == 1 else None)
    return " · ".join(x for x in (TYPE_LABEL.get(p.type, p.type), spec) if x)


def _factors(s: dict, need: Need, prefs: list) -> list[str]:
    p, f = s["p"], s["f"]
    chips = [("Has " if SERVICES[r][1] in ("imaging", "cardiac", "scope", "neuro") else "Does ") + label(r) for r in need.required if r in p.services]
    extra = [label(t) for t in need.soft_tests if t in p.services]
    if extra:
        chips.append("Also does " + ", ".join(extra))
    if need.consult and need.specialty:
        chips.append(label(need.specialty) if s["spec_fit"] else "General physician")
    chips.append("Video call" if need.setting == "online" else ("Comes home · " if need.setting == "home" else "") + _short_km(s["d"]))
    langs = " & ".join(sorted(p.languages, key=lambda x: x != "Hindi"))
    chips.append(f"Speaks {langs}" if f["language"] else "English only" if p.languages == {"English"} else "Hindi only")
    chips.append(f"★{p.rating:.1f} ({p.reviews} reviews)")
    chips.append(s["av_text"])
    if p.fee and ("budget" in prefs or need.profile != "home"):
        chips.append(f"₹{p.fee}")
    if "female" in prefs and p.female_doctor:
        chips.append("Female doctor")
    return chips


def _rating_cmp(a, b, better: bool) -> str:
    pa, pb = a["p"], b["p"]
    if pa.rating == pb.rating:
        return f"same ★{pa.rating} but {'more' if better else 'fewer'} reviews ({pa.reviews} vs {pb.reviews})"
    return f"{'rated higher' if better else 'rated lower'} (★{pa.rating} vs ★{pb.rating})"


GAIN = {
    "distance": lambda a, b: f"{b['d'] - a['d']:.1f} km closer",
    "rating": lambda a, b: _rating_cmp(a, b, True),
    "language": lambda a, b: "speaks your language",
    "availability": lambda a, b: a["av_text"].split(" · ")[0].lower(),
    "cost": lambda a, b: f"cheaper (₹{a['p'].fee} vs ₹{b['p'].fee})",
    "coverage": lambda a, b: "runs more of the likely tests on site",
}
LOSS = {
    "distance": lambda a, b: f"{a['d'] - b['d']:.1f} km farther",
    "rating": lambda a, b: _rating_cmp(a, b, False),
    "language": lambda a, b: "doesn't share your language",
    "availability": lambda a, b: a["av_text"].split(" · ")[0].lower(),
    "cost": lambda a, b: f"costs more (₹{a['p'].fee} vs ₹{b['p'].fee})",
    "coverage": lambda a, b: "does fewer of the likely tests",
}


def _reason(s: dict, top: list, i: int, need: Need, prefs: list, nearest_same_lang) -> str:
    parts = []
    other = top[1] if i == 0 and len(top) > 1 else (top[0] if i > 0 else None)
    if other:
        diff = {k: s["contrib"][k] - other["contrib"][k] for k in s["contrib"]}
        gain, loss = max(diff, key=diff.get), min(diff, key=diff.get)
        if i == 0:
            if diff[gain] > 0.005:
                parts.append(f"Picked over {other['p'].name}: {GAIN[gain](s, other)}")
        else:
            g = GAIN[gain](s, other) if diff[gain] > 0.005 else None
            lo = LOSS[loss](s, other) if diff[loss] < -0.005 else None
            if g or lo:
                parts.append(f"Compared with #1: {g}, but {lo}" if g and lo else f"Compared with #1: {g or lo}")
    if not s["f"]["language"] and nearest_same_lang is not None and not need.high_comm:
        task = " / ".join(label(r) for r in need.required).lower() or "this visit"
        parts.append(f"doesn't share your language — fine for a quick {task}; the nearest provider who does is {_short_km(nearest_same_lang)} away")
    if not parts:
        parts.append("Closest strong match for what you need")
    text = "; ".join(parts)
    return text[0].upper() + text[1:]


def _summary(need: Need, n_cands: int, relaxed: bool, prefs: list, scored: list, want: set) -> str:
    total = len(providers())
    labels = [label(r) for r in need.required]
    names = " and ".join([", ".join(labels[:-1]), labels[-1]]) if len(labels) > 1 else "".join(labels)
    is_ = "are" if len(labels) > 1 else "is"
    if relaxed:
        return f"No single provider in range offers all of {names}; these offer the most of them."
    if need.profile == "online":
        s = f"Video consultation: distance doesn't matter, so a shared language and rating decide ({n_cands} doctors available)."
    elif need.profile == "home":
        s = f"Home visit by providers whose service area covers you ({n_cands} available)."
    elif need.profile == "rare":
        k = round(max(coverage().get(r, 0) for r in need.required if coverage().get(r, 1) < RARE_BELOW) * total)
        s = f"{names} {is_} offered by only about {k} of {total} providers, so having it decides; {n_cands} are in reach, ranked by rating and distance."
    elif need.profile == "test":
        s = f"{names} {is_} widely available ({n_cands} places nearby), so rating and distance decide."
    elif need.profile == "procedure":
        common = min(coverage().get(r, 0) for r in need.required) >= 0.3
        s = (f"{names} {is_} available almost everywhere ({n_cands} places nearby), so the nearest open place that speaks your language wins."
             if common else f"{names} {is_} offered by {n_cands} places in reach; the nearest open one wins.")
    else:
        who = label(need.specialty) if need.specialty else "General physician"
        s = f"{who} consultation from {n_cands} doctors in reach"
        s += f", favouring places that can also run {', '.join(label(t) for t in need.soft_tests)} on site." if need.soft_tests else "."
    if need.high_comm and any(not x["f"]["language"] for x in scored):
        s += " Providers who don't speak your language were ranked down, because this visit needs conversation."
    boosts = {"quality": "rating", "nearby": "distance", "budget": "price", "soon": "opening hours", "female": "a female doctor"}
    picked = [boosts[p] for p in prefs if p in boosts]
    if picked:
        s += f" You asked about {', '.join(picked)}, so that counted for more."
    return s
