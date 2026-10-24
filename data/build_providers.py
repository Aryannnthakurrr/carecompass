"""Build data/providers.csv: a synthetic Delhi NCR healthcare-provider directory for smart-mode matching.

It keeps the organiser's 60 caregivers (home-care medics, video doctors, phlebotomists) and adds clinics, hospitals,
diagnostic centres, labs and physiotherapy centres with realistic capability spread: MRI is rare, X-ray is common,
tetanus shots are almost everywhere. Languages are limited to Hindi and English. Names are fictional.

Run: python data/build_providers.py   (deterministic; seed fixed)
"""
import csv
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
rng = random.Random(7)

AREAS = {  # name: (lat, lng, language profile)
    "Okhla": (28.5402, 77.2700, "mixed"), "Saket": (28.5245, 77.2066, "mixed"), "Lajpat Nagar": (28.5677, 77.2433, "mixed"),
    "Connaught Place": (28.6315, 77.2167, "english"), "Laxmi Nagar": (28.6304, 77.2777, "hindi"), "Vasant Kunj": (28.5200, 77.1590, "english"),
    "Dwarka": (28.5921, 77.0460, "mixed"), "Rohini": (28.7495, 77.0565, "hindi"), "Noida Sector 18": (28.5708, 77.3260, "mixed"),
    "Gurugram Cyber City": (28.4950, 77.0895, "english"), "Karol Bagh": (28.6519, 77.1909, "hindi"), "Janakpuri": (28.6219, 77.0878, "mixed"),
    "Pitampura": (28.7033, 77.1323, "mixed"), "Mayur Vihar": (28.6085, 77.2930, "mixed"), "Greater Kailash": (28.5482, 77.2380, "english"),
    "Hauz Khas": (28.5494, 77.2001, "english"), "Preet Vihar": (28.6417, 77.2947, "mixed"), "Rajouri Garden": (28.6492, 77.1222, "mixed"),
    "Noida Sector 62": (28.6270, 77.3727, "mixed"), "Indirapuram": (28.6460, 77.3695, "mixed"), "Gurugram Sector 29": (28.4687, 77.0630, "english"),
    "Faridabad Sector 15": (28.3960, 77.3260, "hindi"), "Greater Noida": (28.4744, 77.5040, "hindi"), "Shahdara": (28.6735, 77.2899, "hindi"),
    "Malviya Nagar": (28.5355, 77.2104, "mixed"), "Kalkaji": (28.5494, 77.2588, "mixed"), "Chandni Chowk": (28.6506, 77.2303, "hindi"),
}
PREFIX = ["Shanti", "Lotus", "Sanjeevani", "Arogya", "Jeevan", "Sunrise", "Lifeline", "CarePoint", "Prakash", "Aastha", "Silverline",
          "Greenleaf", "Healwell", "Vardaan", "Swasthya", "Dhanvantari", "Neelkanth", "Kalpataru", "Navjeevan", "Sparsh", "Udaan",
          "Sahyog", "Mangalam", "Suraksha", "Amrit", "Kiran", "Nirmal", "Pragati", "Satyam", "Utkarsh"]
SURNAME = ["Mehta", "Sharma", "Gupta", "Kapoor", "Verma", "Bansal", "Malhotra", "Arora", "Khanna", "Saxena", "Iyer", "Nair", "Reddy",
           "Chauhan", "Rawat", "Bhatia", "Sethi", "Joshi", "Tandon", "Agarwal", "Singh", "Kohli", "Chopra", "Pillai", "Rao"]

SPECIALTY_SERVICES = {  # what a specialist clinic typically offers on top of consultation
    "dermatology": ["allergy_test", "minor_surgery"], "dentistry": ["dental_cleaning", "root_canal", "tooth_extraction", "xray"],
    "ophthalmology": ["eye_test", "cataract_surgery"], "ent": ["audiometry", "ear_wax_removal"], "orthopedics": ["xray", "plaster_cast", "physiotherapy"],
    "gynecology": ["ultrasound", "pap_smear", "vaccination"], "pediatrics": ["vaccination", "nebulization", "tetanus_shot"],
    "psychiatry": ["counselling"], "cardiology": ["ecg", "echo", "tmt", "holter"], "urology": ["ultrasound", "urine_test", "urine_culture"],
    "neurology": ["eeg"], "pulmonology": ["pft", "nebulization", "xray", "sleep_study"], "gastroenterology": ["endoscopy", "ultrasound"],
    "endocrinology": ["blood_test"],
}
OPTIONAL_SPECIALTY_SERVICES = {"orthopedics": 0.5, "dentistry": 0.6, "cardiology": 0.4, "pulmonology": 0.4, "gastroenterology": 0.4,
                               "neurology": 0.5, "ophthalmology": 0.3, "dermatology": 0.5, "urology": 0.6, "gynecology": 0.7}
HOSPITAL_SPECIALTIES = list(SPECIALTY_SERVICES)


def languages(profile: str) -> str:
    r = rng.random()
    if profile == "english":
        return "English" if r < 0.3 else "Hindi;English"
    if profile == "hindi":
        return "Hindi" if r < 0.45 else "Hindi;English"
    return "Hindi" if r < 0.2 else ("English" if r < 0.25 else "Hindi;English")


def place(area: str):
    lat, lng, profile = AREAS[area]
    return round(lat + rng.uniform(-0.012, 0.012), 4), round(lng + rng.uniform(-0.012, 0.012), 4), profile


def rating(lo=3.2, hi=4.9, mu=4.1):
    return round(min(hi, max(lo, rng.gauss(mu, 0.38))), 1)


def some(items, p):
    return [i for i in items if rng.random() < p]


rows = []


def add(pid, name, ptype, specialties, services, area, lang=None, **kw):
    lat, lng, profile = place(area)
    base = dict(provider_id=pid, name=name, type=ptype, specialties=";".join(specialties), services=";".join(dict.fromkeys(services)),
                languages=lang or languages(profile), area=area, lat=lat, lng=lng, service_radius_km="", rating=rating(), reviews=rng.randint(15, 900),
                fee_inr="", open_from="09:00", open_until="21:00", closed_on="Sun", is_24x7=False, home_visit=False, teleconsult=False,
                female_doctor=rng.random() < 0.4, active=True)
    base.update(kw)
    rows.append(base)


# 1. the organiser's caregivers, kept with their ids
SKILL_MAP = {"injection_im": ["injection", "vaccination", "tetanus_shot"], "injection_iv": ["iv_drip"], "wound_dressing": ["wound_dressing"],
             "catheter_care": ["catheter_care"], "nebulization": ["nebulization"], "post_surgical_care": ["wound_dressing", "home_care"],
             "bp_sugar_check": ["home_care"], "vitals_monitoring": ["home_care"], "elderly_care": ["home_care"], "sample_collection": ["sample_collection_home"],
             "home_phlebotomy": ["sample_collection_home", "blood_test", "urine_test"], "teleconsultation": ["consultation"], "prescription": [], "report_review": []}
with open(ROOT / "training/caregivers.csv", encoding="utf-8") as f:
    for c in csv.DictReader(f):
        langs = [l for l in c["languages"].split(";") if l in ("Hindi", "English")] or ["Hindi"]
        svcs = [s for sk in c["skills"].split(";") for s in SKILL_MAP.get(sk, [])]
        role = c["role"]
        rows.append(dict(
            provider_id=c["caregiver_id"], name=("Dr. " if role == "doctor" else "") + c["name"],
            type={"medic": "home_care", "doctor": "teleconsult_doctor", "phlebotomist": "home_lab"}[role],
            specialties="general_medicine" if role == "doctor" else "", services=";".join(dict.fromkeys(svcs)), languages=";".join(langs),
            area=c["base_area"], lat=c["base_lat"], lng=c["base_lng"], service_radius_km=c["max_radius_km"], rating=c["rating"],
            reviews=rng.randint(10, 300), fee_inr={"medic": 499, "doctor": 399, "phlebotomist": 199}[role], open_from=c["free_from"],
            open_until=c["free_until"], closed_on="", is_24x7=False, home_visit=role != "doctor", teleconsult=role == "doctor",
            female_doctor=c["gender"] == "F", active=c["active"]))

areas = list(AREAS)
n = 0


def pid():
    global n
    n += 1
    return f"HP_{n:03d}"


# 2. hospitals: many specialties, emergency capable, the main home of CT/MRI/dialysis
for i in range(14):
    area = areas[(i * 2) % len(areas)]
    specs = ["general_medicine"] + some(HOSPITAL_SPECIALTIES, 0.65)
    svcs = ["consultation", "xray", "ecg", "blood_test", "urine_test", "urine_culture", "vaccination", "tetanus_shot", "anti_rabies", "injection",
            "iv_drip", "wound_dressing", "stitches", "plaster_cast", "nebulization", "minor_surgery", "ultrasound"]
    svcs += some(["ct_scan"], 0.7) + some(["mri"], 0.45) + some(["echo", "tmt"], 0.7) + some(["endoscopy", "colonoscopy"], 0.5) + some(["dialysis"], 0.45)
    svcs += some(["physiotherapy", "eeg", "pft", "mammography", "holter", "biopsy", "audiometry", "eye_test"], 0.5) + some(["chemotherapy", "pet_ct", "sleep_study", "dexa"], 0.2)
    svcs += [s for sp in specs for s in SPECIALTY_SERVICES.get(sp, [])]
    add(pid(), f"{PREFIX[i]} {'Multispeciality' if i % 3 else 'Super Speciality'} Hospital", "hospital", specs, svcs, area,
        is_24x7=True, open_from="00:00", open_until="23:59", closed_on="", fee_inr=rng.choice([600, 800, 1000, 1200, 1500]),
        reviews=rng.randint(300, 4000), female_doctor=True, teleconsult=rng.random() < 0.5)

# 3. polyclinics: a few specialties, common diagnostics
for i in range(16):
    area = areas[(i * 3 + 1) % len(areas)]
    specs = ["general_medicine"] + rng.sample(HOSPITAL_SPECIALTIES, rng.randint(2, 5))
    svcs = ["consultation", "blood_test", "urine_test", "vaccination", "tetanus_shot", "injection", "wound_dressing", "nebulization", "ecg"]
    svcs += some(["xray"], 0.65) + some(["ultrasound"], 0.5) + some(["stitches", "urine_culture", "iv_drip", "physiotherapy"], 0.5) + some(["anti_rabies", "echo"], 0.25)
    svcs += [s for sp in specs for s in SPECIALTY_SERVICES.get(sp, []) if rng.random() < 0.6]
    add(pid(), f"{PREFIX[(i + 9) % len(PREFIX)]} Polyclinic", "polyclinic", specs, svcs, area, fee_inr=rng.choice([400, 500, 600, 700]),
        teleconsult=rng.random() < 0.5)

# 4. GP clinics: neighbourhood family doctors
for i in range(30):
    area = areas[(i * 5 + 2) % len(areas)]
    specs = ["general_medicine"] + (["pediatrics"] if rng.random() < 0.25 else [])
    svcs = ["consultation", "vaccination", "tetanus_shot", "injection", "wound_dressing"] + some(["stitches", "nebulization", "iv_drip"], 0.5)
    svcs += some(["ecg", "blood_test"], 0.35) + some(["anti_rabies"], 0.2)
    add(pid(), f"Dr. {SURNAME[i % len(SURNAME)]}'s Clinic", "gp_clinic", specs, svcs, area, fee_inr=rng.choice([200, 250, 300, 400, 500]),
        open_from=rng.choice(["08:00", "09:00", "10:00"]), open_until=rng.choice(["13:00", "20:00", "21:00", "22:00"]),
        home_visit=rng.random() < 0.2, teleconsult=rng.random() < 0.3, reviews=rng.randint(10, 400))

# 5. specialist clinics: one specialty each, two clinics per specialty and a third for the common ones
spec_cycle = HOSPITAL_SPECIALTIES * 2 + ["dermatology", "dentistry", "gynecology", "orthopedics", "pediatrics", "ophthalmology"]
for i, sp in enumerate(spec_cycle):
    area = areas[(i * 7 + 3) % len(areas)]
    extra = SPECIALTY_SERVICES[sp]
    keep = extra if rng.random() < OPTIONAL_SPECIALTY_SERVICES.get(sp, 1.0) else extra[:1]
    title = {"dentistry": "Dental Care", "ophthalmology": "Eye Centre", "orthopedics": "Bone & Joint Clinic", "pediatrics": "Child Clinic",
             "gynecology": "Women's Clinic", "psychiatry": "Mind Clinic", "cardiology": "Heart Clinic", "dermatology": "Skin Clinic",
             "ent": "ENT Clinic", "urology": "Urology Centre", "neurology": "Neuro Clinic", "pulmonology": "Chest Clinic",
             "gastroenterology": "Gastro Clinic", "endocrinology": "Diabetes & Thyroid Clinic"}[sp]
    add(pid(), f"{SURNAME[(i + 4) % len(SURNAME)]} {title}", "specialist_clinic", [sp], ["consultation"] + keep, area,
        fee_inr=rng.choice([500, 700, 800, 1000, 1200]), female_doctor=(sp == "gynecology") or rng.random() < 0.4,
        teleconsult=sp in ("psychiatry", "dermatology", "endocrinology") or rng.random() < 0.3)

# 6. diagnostic centres: imaging-led
for i in range(14):
    area = areas[(i * 4 + 5) % len(areas)]
    svcs = ["xray", "ultrasound", "ecg", "blood_test", "urine_test"] + some(["ct_scan"], 0.5) + some(["mri"], 0.4)
    svcs += some(["echo", "tmt", "mammography", "dexa", "holter", "urine_culture", "sample_collection_home"], 0.4) + some(["pet_ct", "eeg", "pft"], 0.15)
    add(pid(), f"{PREFIX[(i + 3) % len(PREFIX)]} Diagnostics & Imaging", "diagnostic_centre", [], svcs, area, fee_inr=rng.choice([300, 400, 500]),
        open_from="07:00", open_until="22:00", closed_on="", female_doctor=rng.random() < 0.5)

# 7. pathology labs
for i in range(12):
    area = areas[(i * 6 + 4) % len(areas)]
    svcs = ["blood_test", "urine_test"] + some(["urine_culture", "covid_test", "allergy_test", "pap_smear"], 0.5) + some(["sample_collection_home"], 0.75) + some(["biopsy"], 0.2)
    add(pid(), f"{PREFIX[(i + 17) % len(PREFIX)]} Pathlab", "lab", [], svcs, area, fee_inr=rng.choice([150, 200, 250]), open_from="07:00",
        open_until="20:00", closed_on="", home_visit="sample_collection_home" in svcs)

# 8. physiotherapy centres
for i in range(6):
    area = areas[(i * 9 + 6) % len(areas)]
    add(pid(), f"{PREFIX[(i + 22) % len(PREFIX)]} Physio & Rehab", "physio_centre", ["orthopedics"], ["physiotherapy", "consultation"], area,
        fee_inr=rng.choice([500, 600, 800]), home_visit=rng.random() < 0.5)

# a few inactive facilities, as in the organiser roster
for r in rng.sample([r for r in rows if r["provider_id"].startswith("HP_")], 6):
    r["active"] = False

cols = list(rows[0])
out = ROOT / "data/providers.csv"
with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    w.writerows(rows)
print(f"wrote {out} with {len(rows)} providers")
