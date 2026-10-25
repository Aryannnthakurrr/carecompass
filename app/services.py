"""Caregiver directory (browse + filter) and a simulated ambulance dispatch.

The dispatch endpoints mirror the shape of an ambulance aggregator's developer API (Dial4242:
POST /v1/fare/calculate, GET /v1/ambulances/types, booking, GET /v1/bookings/{id}/tracking).
For the hackathon nothing is sent anywhere: a fake ambulance drives towards the patient."""
import math
import secrets
import time
from datetime import datetime
from typing import Literal, Optional

import requests
from fastapi import APIRouter, Cookie, HTTPException, Query
from pydantic import BaseModel, Field

from app import auth
from app.matching import DEFAULT_LOC, IST, ROLE_LABEL, caregivers, haversine, next_slot

router = APIRouter()

CARE_FOR_ROLE = {"doctor": "teleconsultation", "medic": "paramedic_home_visit", "phlebotomist": "lab_test_home"}


# ---------------------------------------------------------------- directory

def _card(c: dict, here: tuple, now: datetime) -> dict:
    virtual = c["role"] == "doctor"
    d = haversine(here, (c["lat"], c["lng"]))
    return {
        "caregiver_id": c["caregiver_id"], "name": c["name"], "role": f"{ROLE_LABEL[c['role']]} · {c['qualification']}",
        "kind": c["role"], "care_type": CARE_FOR_ROLE[c["role"]], "gender": c["gender"],
        "skills": c["skills"], "languages": c["languages"], "rating": c["rating"],
        "experience_years": int(c["experience_years"]), "area": c["base_area"],
        "distance_km": round(d, 1), "serves_you": virtual or d <= c["radius"], "video": virtual,
        "next_slot": next_slot(c["free_from"], c["free_until"], now), "hours": f"{c['free_from']}–{c['free_until']}",
    }


# smart mode browses data/providers.csv: grouped into the kinds a patient thinks in
SMART_KIND = {"hospital": "hospital", "gp_clinic": "clinic", "polyclinic": "clinic", "specialist_clinic": "clinic", "physio_centre": "clinic",
              "diagnostic_centre": "diagnostic", "lab": "diagnostic", "home_lab": "diagnostic", "home_care": "home", "teleconsult_doctor": "video"}
SMART_CARE = {"hospital": "hospital_visit", "gp_clinic": "clinic_visit", "polyclinic": "clinic_visit", "specialist_clinic": "specialist_visit",
              "physio_centre": "specialist_visit", "diagnostic_centre": "diagnostic_test", "lab": "diagnostic_test", "home_lab": "lab_test_home",
              "home_care": "home_nursing", "teleconsult_doctor": "teleconsultation"}


def _smart_cards(here: tuple, now: datetime, kind, skill, q, min_rating, max_km, sort) -> dict:
    from app import lexicon, smart_match as sm
    out = []
    for p in sm.providers():
        if (kind and SMART_KIND[p.type] != kind) or (skill and skill not in p.services) or p.rating < min_rating:
            continue
        d = haversine(here, (p.lat, p.lng))
        video = p.type == "teleconsult_doctor"
        if max_km and not video and d > max_km:
            continue
        specs = [lexicon.label(s) for s in sorted(p.specialties)]
        if q and q.lower() not in " ".join([p.name, *specs, *(lexicon.label(s) for s in p.services)]).lower():
            continue
        _, hours_now, slot = sm.availability(p, now)
        out.append({
            "caregiver_id": p.id, "name": p.name, "kind": SMART_KIND[p.type], "care_type": SMART_CARE[p.type],
            "role": " · ".join([sm.TYPE_LABEL[p.type]] + ([f"{len(specs)} specialties"] if len(specs) > 2 else specs)),
            "gender": "F" if p.female_doctor else "", "skills": sorted(p.services, key=lambda s: sm.coverage().get(s, 1)),  # rarest first
            "languages": sorted(p.languages, key=lambda x: x != "Hindi"), "rating": p.rating, "reviews": p.reviews, "fee_inr": p.fee,
            "experience_years": None, "distance_km": round(d, 1), "video": video,
            "serves_you": video or (d <= (p.radius or 15) if p.home_visit and p.type in ("home_care", "home_lab") else d <= 40),
            "next_slot": slot, "hours": "24×7" if p.is_24x7 else f"{p.open_from}–{p.open_until}", "open_text": hours_now,
        })
    keys = {
        "distance": lambda x: (x["distance_km"], -x["rating"]),
        "rating": lambda x: (-x["rating"], -x["reviews"]),
        "experience": lambda x: (-x["reviews"], x["distance_km"]),  # most reviewed stands in for experience
        "best": lambda x: (not x["serves_you"], -(x["rating"] - 3) / 2 * 0.5 + min(x["distance_km"], 20) / 20 * 0.5),
    }
    out.sort(key=keys[sort])
    all_services = sorted({s for p in sm.providers() for s in p.services}, key=lexicon.label)
    return {"count": len(out), "caregivers": out, "skills": all_services, "skill_labels": {s: lexicon.label(s) for s in all_services}}


@router.get("/caregivers")
def browse(
    lat: float = Query(DEFAULT_LOC[0], ge=-90, le=90), lng: float = Query(DEFAULT_LOC[1], ge=-180, le=180),
    kind: Optional[Literal["doctor", "medic", "phlebotomist", "hospital", "clinic", "diagnostic", "home", "video"]] = None,
    skill: Optional[str] = Query(None, max_length=40),
    q: Optional[str] = Query(None, max_length=60),
    min_rating: float = Query(0, ge=0, le=5),
    min_experience: int = Query(0, ge=0, le=60),
    max_km: Optional[float] = Query(None, gt=0, le=100),
    sort: Literal["best", "distance", "rating", "experience"] = "best",
    mode: Literal["dataset", "smart"] = "smart",
):
    """Active caregivers with distance from (lat, lng). max_km ignores video doctors (they don't travel).
    mode=smart lists the full provider directory (data/providers.csv) instead of the organiser roster."""
    now, here = datetime.now(IST), (lat, lng)
    if mode == "smart":
        return _smart_cards(here, now, kind, skill, q, min_rating, max_km, sort)
    out = []
    for c in caregivers():
        if not c["active"] or (kind and c["role"] != kind) or (skill and skill not in c["skills"]):
            continue
        card = _card(c, here, now)
        if card["rating"] < min_rating or card["experience_years"] < min_experience:
            continue
        if max_km and not card["video"] and card["distance_km"] > max_km:
            continue
        if q and q.lower() not in " ".join([c["name"], c["qualification"], c["base_area"], *c["skills"]]).lower().replace("_", " "):
            continue
        out.append(card)
    keys = {
        "distance": lambda x: (x["distance_km"], -x["rating"]),
        "rating": lambda x: (-x["rating"], x["distance_km"]),
        "experience": lambda x: (-x["experience_years"], x["distance_km"]),
        # who can actually come to you first, then a rating/proximity blend
        "best": lambda x: (not x["serves_you"], -(x["rating"] - 3) / 2 * 0.5 + min(x["distance_km"], 20) / 20 * 0.5),
    }
    out.sort(key=keys[sort])
    return {"count": len(out), "caregivers": out,
            "skills": sorted({s for c in caregivers() if c["active"] for s in c["skills"]})}


# ---------------------------------------------------------------- simulated ambulance dispatch

AMBULANCE_TYPES = [
    {"code": "BLS", "name": "Basic Life Support", "crew": "Driver + EMT", "base_fare": 1200, "per_km": 40},
    {"code": "ALS", "name": "Advanced Life Support", "crew": "Driver + paramedic nurse, cardiac monitor, oxygen", "base_fare": 2500, "per_km": 60},
    {"code": "ICU", "name": "Mobile ICU", "crew": "Driver + nurse + doctor, ventilator", "base_fare": 4500, "per_km": 90},
]
# red flags that need a monitored (ALS) ambulance rather than basic transport
ALS_FLAGS = {"chest_pain", "breathlessness", "unconsciousness", "stroke_symptoms", "seizure", "severe_bleeding"}
SPEED_KMH = 32  # city traffic
DRIVERS = [("Ramesh Yadav", "DL 1C AB 4821"), ("Sunil Kumar", "DL 3C AK 1907"), ("Imran Khan", "HR 26 DK 5530"), ("Vikas Rana", "UP 16 BT 7712")]
NURSES = ["Pooja Sharma (GNM)", "Anjali Verma (B.Sc Nursing)", "Rekha Negi (GNM)", "Neha Joshi (B.Sc Nursing)"]
DISPATCHES: dict[str, dict] = {}  # ponytail: in-memory, lost on restart; a real provider keeps the booking


class Point(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=2000)


class DispatchIn(BaseModel):
    location: Point
    address: Optional[str] = Field(default=None, max_length=200)
    conversation_history: list[Turn] = Field(default=[], max_length=40)
    red_flags: list[str] = Field(default=[], max_length=12)
    reasoning: Optional[str] = Field(default=None, max_length=600)
    contact_phone: Optional[str] = Field(default=None, max_length=20)


def _road_route(a: tuple, b: tuple) -> list[list[float]]:
    """Driving route a -> b as [[lat, lng], ...] from the public OSRM demo server; straight line if it is unreachable."""
    try:
        r = requests.get(
            f"https://router.project-osrm.org/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}",
            params={"overview": "full", "geometries": "geojson"}, timeout=4,
        )
        coords = r.json()["routes"][0]["geometry"]["coordinates"]
        if len(coords) >= 2:
            return [[la, lo] for lo, la in coords]
    except Exception:
        pass
    return [list(a), list(b)]


def _start_point(here: tuple, km: float, seed: int) -> tuple:
    """A point ~km away on a random bearing: the 'nearest free ambulance'."""
    bearing = math.radians(seed % 360)
    dlat = km / 111.0 * math.cos(bearing)
    dlng = km / (111.0 * math.cos(math.radians(here[0]))) * math.sin(bearing)
    return here[0] + dlat, here[1] + dlng


def _crew_brief(body: DispatchIn, contact: Optional[str]) -> dict:
    """What the driver and nurse see before they arrive: the patient's own words, never a diagnosis."""
    return {
        "red_flags": body.red_flags,
        "patient_said": [t.content for t in body.conversation_history if t.role == "user"][-6:],
        "assessment": body.reasoning,
        "contact_phone": contact,
        "address": body.address,
    }


@router.get("/emergency/ambulance-types")
def ambulance_types():
    return AMBULANCE_TYPES


@router.post("/emergency/dispatch")
def dispatch(body: DispatchIn, cc_session: Optional[str] = Cookie(default=None)):
    """Simulated: picks the nearest 'free' ambulance, routes it to the patient and shares the case with the crew.
    Works without sign-in: an emergency must never wait on a login."""
    contact = body.contact_phone
    if not contact and cc_session:
        try:
            contact = auth.current_user(cc_session).get("phone")
        except HTTPException:
            pass
    here = (body.location.lat, body.location.lng)
    seed = secrets.randbelow(10_000)
    route_km = lambda r: sum(haversine(tuple(r[i]), tuple(r[i + 1])) for i in range(len(r) - 1))
    # a start across a river or highway gives a long detour; try a few bearings and keep the shortest
    best = None
    for k in range(4):
        start = _start_point(here, 2.2 + (seed % 15) / 10, seed + k * 90)
        r = _road_route(start, here)
        if best is None or route_km(r) < route_km(best):
            best = r
        if route_km(best) < 2.2 * haversine(start, here):
            break
    route, km = best, route_km(best)
    tier = next(t for t in AMBULANCE_TYPES if t["code"] == ("ALS" if ALS_FLAGS & set(body.red_flags) else "BLS"))
    driver, vehicle = DRIVERS[seed % len(DRIVERS)]
    ref = "AMB-" + secrets.token_hex(3).upper()
    DISPATCHES[ref] = {
        "id": ref, "provider": "Dial4242 (simulated)", "created_at": time.time(),
        "ambulance": {**tier, "vehicle_no": vehicle, "driver": driver, "driver_phone": f"+91 98{seed:04d}1108",
                      "nurse": NURSES[seed % len(NURSES)]},
        "fare_estimate": round(tier["base_fare"] + tier["per_km"] * km),
        "route": route, "route_km": round(km, 2), "eta_total_s": max(240, round(km / SPEED_KMH * 3600)),
        "patient": list(here), "demo_drive_s": DEMO_DRIVE_S,
        "crew_brief": _crew_brief(body, contact),
        # the live calls a real deployment would make, shown in the UI for transparency
        "would_call": ["GET /v1/ambulances/types", "POST /v1/fare/calculate", "POST /v1/bookings",
                       f"GET /v1/bookings/{ref}/tracking"],
    }
    return {**DISPATCHES[ref], **tracking(ref)}


# ponytail: the demo compresses the drive into DEMO_DRIVE_S so judges see it arrive; the ETA shown stays realistic.
DEMO_DRIVE_S = 75


@router.get("/emergency/{ref}/tracking")
def tracking(ref: str):
    d = DISPATCHES.get(ref)
    if not d:
        raise HTTPException(404, "Unknown dispatch")
    frac = min(1.0, (time.time() - d["created_at"]) / DEMO_DRIVE_S)
    route = d["route"]
    seg = [haversine(tuple(route[i]), tuple(route[i + 1])) for i in range(len(route) - 1)]
    left, pos = frac * sum(seg), route[-1]
    for i, s in enumerate(seg):
        if left <= s:
            k = left / s if s else 1
            pos = [route[i][0] + (route[i + 1][0] - route[i][0]) * k, route[i][1] + (route[i + 1][1] - route[i][1]) * k]
            break
        left -= s
    status = "arrived" if frac >= 1 else "arriving" if frac > 0.85 else "en_route"
    return {"id": ref, "status": status, "position": pos, "progress": round(frac, 3),
            "eta_s": round(d["eta_total_s"] * (1 - frac)), "remaining_km": round(d["route_km"] * (1 - frac), 2)}
