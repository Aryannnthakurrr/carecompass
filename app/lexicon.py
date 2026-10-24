"""Shared healthcare vocabulary for smart mode: services/tests, specialties, symptom hints and user preferences,
each with English, romanised Hinglish and Devanagari patterns. The provider data, the classifier and the matcher
all speak these tags, so a word the user types maps straight to a capability a provider has (or lacks)."""
import re
from dataclasses import dataclass

# ---------------------------------------------------------------- services and tests
# tag: (label, kind, communication, patterns)
#   kind: imaging | cardiac | lab | scope | neuro | procedure | vaccine | therapy | dental | eye | ent | home | consult
#   communication: how much talking the visit needs. "low" (a shot, a scan) lets distance beat a language mismatch;
#   "high" (a consultation) makes a shared language close to mandatory.
SERVICES = {
    "xray": ("X-ray", "imaging", "low", [r"x-?\s?rays?", r"एक्स-?\s?रे", r"chest film"]),
    "ultrasound": ("Ultrasound", "imaging", "low", [r"ultra\s?sound", r"\busg\b", r"sonograph", r"\bsono\b", r"अल्ट्रासाउंड", r"सोनोग्राफी"]),
    "ct_scan": ("CT scan", "imaging", "low", [r"\bct\b(\s?scan)?", r"cat scan", r"सी\.?\s?टी\.?\s?स्कैन", r"सीटी"]),
    "mri": ("MRI", "imaging", "low", [r"\bmri\b", r"एम\.?\s?आर\.?\s?आई", r"magnetic resonance"]),
    "mammography": ("Mammography", "imaging", "low", [r"mammogra"]),
    "dexa": ("DEXA bone-density scan", "imaging", "low", [r"\bdexa\b", r"bone density"]),
    "pet_ct": ("PET-CT", "imaging", "low", [r"pet[-\s]?ct", r"pet scan"]),
    "ecg": ("ECG", "cardiac", "low", [r"\becg\b", r"\bekg\b", r"electrocardiogra", r"ईसीजी"]),
    "echo": ("2D Echo", "cardiac", "low", [r"\becho\b", r"echocardiogra", r"2d echo"]),
    "tmt": ("Treadmill (stress) test", "cardiac", "low", [r"\btmt\b", r"treadmill", r"stress test"]),
    "holter": ("Holter monitoring", "cardiac", "low", [r"holter"]),
    "blood_test": ("Blood tests", "lab", "low", [r"blood (test|work|report)", r"\bcbc\b", r"complete blood count", r"lipid", r"\b[kl]ft\b",
                                               r"(kidney|liver|thyroid) (function|test|profile)", r"\btsh\b", r"hba1c", r"(sugar|glucose) test",
                                               r"vitamin\s?(d|b12)", r"(dengue|malaria|typhoid) test", r"widal", r"khoo?n ki jaa?nch",
                                               r"ब्लड टेस्ट", r"खून की जा(ँ|ं)च", r"full body check"]),
    "urine_test": ("Urine test", "lab", "low", [r"urine (test|routine|exam)", r"peshab (ki )?jaa?nch", r"पेशाब की जा(ँ|ं)च", r"urine r/?m"]),
    "urine_culture": ("Urine culture", "lab", "low", [r"urine culture", r"culture (test|report)"]),
    "covid_test": ("COVID test", "lab", "low", [r"covid", r"rt-?pcr", r"antigen test"]),
    "allergy_test": ("Allergy test", "lab", "low", [r"allergy test"]),
    "biopsy": ("Biopsy", "lab", "high", [r"biopsy"]),
    "pap_smear": ("Pap smear", "lab", "low", [r"pap smear"]),
    "sample_collection_home": ("Home sample collection", "home", "low", [r"home (sample )?collection", r"ghar (se|pe|par) sample", r"घर से सैंपल", r"sample (le|collect)"]),
    "endoscopy": ("Endoscopy", "scope", "low", [r"endoscop"]),
    "colonoscopy": ("Colonoscopy", "scope", "low", [r"colonoscop"]),
    "eeg": ("EEG", "neuro", "low", [r"\beeg\b"]),
    "pft": ("Lung function test (PFT)", "neuro", "low", [r"\bpft\b", r"spirometr", r"lung function"]),
    "sleep_study": ("Sleep study", "neuro", "low", [r"sleep study", r"polysomnograph"]),
    "audiometry": ("Hearing test", "ent", "low", [r"audiometr", r"hearing test"]),
    "eye_test": ("Eye test", "eye", "low", [r"eye (test|check)", r"vision test", r"aa?nkh(on)? (ki )?jaa?nch", r"chashm", r"आ(ँ|ं)खों की जा(ँ|ं)च", r"spectacle"]),
    "tetanus_shot": ("Tetanus shot", "vaccine", "low", [r"tetanus", r"\btt\b ?(shot|injection|jab)?", r"टिटनेस"]),
    "vaccination": ("Vaccination", "vaccine", "low", [r"vaccin", r"\bteeka\b", r"\btika\b", r"टीका", r"immuni[sz]", r"flu shot", r"\bhpv\b"]),
    "anti_rabies": ("Anti-rabies vaccine", "vaccine", "low", [r"rabies", r"(dog|monkey|cat) bite", r"(kutt?e|bandar|billi) ne kaa?t", r"कुत्ते ने काट", r"बंदर ने काट"]),
    "injection": ("Injection", "procedure", "low", [r"injection", r"\binject", r"इंजेक्शन", r"insulin shot"]),
    "iv_drip": ("IV drip", "procedure", "low", [r"\biv\b", r"\bdrip\b", r"infusion", r"glucose chadh", r"ड्रिप"]),
    "wound_dressing": ("Wound dressing", "procedure", "low", [r"dressing", r"wound care", r"पट्टी", r"\bghaa?v\b", r"घाव"]),
    "stitches": ("Stitches", "procedure", "low", [r"stitch", r"sutur", r"\btaa?n?ke\b", r"टा(ं|ँ)के"]),
    "nebulization": ("Nebulisation", "procedure", "low", [r"nebuli"]),
    "catheter_care": ("Catheter care", "procedure", "low", [r"catheter", r"कैथेटर"]),
    "plaster_cast": ("Plaster / cast", "procedure", "low", [r"plaster", r"\bcast\b", r"प्लास्टर"]),
    "ear_wax_removal": ("Ear-wax removal", "ent", "low", [r"ear ?wax", r"kaa?n (ka )?mail", r"kaa?n saaf"]),
    "minor_surgery": ("Minor surgery", "procedure", "high", [r"minor surgery", r"abscess", r"boil (removal|drain)", r"cyst removal"]),
    "physiotherapy": ("Physiotherapy", "therapy", "high", [r"physio", r"फिजियो"]),
    "dialysis": ("Dialysis", "therapy", "low", [r"dialysis", r"डायलिसिस"]),
    "chemotherapy": ("Chemotherapy", "therapy", "high", [r"\bchemo"]),
    "counselling": ("Counselling", "therapy", "high", [r"counsel", r"\btherap(ist|y)\b", r"psychologist"]),
    "dental_cleaning": ("Teeth cleaning", "dental", "low", [r"teeth cleaning", r"scaling", r"daa?nt saaf"]),
    "root_canal": ("Root canal", "dental", "low", [r"root canal", r"\brct\b"]),
    "tooth_extraction": ("Tooth extraction", "dental", "low", [r"tooth (removal|extraction)", r"daa?nt nikal", r"दा(ँ|ं)त निकल"]),
    "cataract_surgery": ("Cataract surgery", "eye", "high", [r"cataract", r"motiyabind", r"मोतियाबिंद"]),
    "home_care": ("Nursing at home", "home", "low", [r"home nurs", r"nurse (at home|ghar)", r"ghar (pe|par) nurse", r"attendant", r"elderly care", r"bedridden"]),
    "consultation": ("Doctor consultation", "consult", "high", [r"consult", r"check-?\s?up", r"dikha(na|ni|ana|o)", r"doctor se mil", r"appointment", r"दिखाना", r"डॉक्टर से मिल"]),
}
SERVICE_TAGS = list(SERVICES)
TEST_KINDS = {"imaging", "cardiac", "lab", "scope", "neuro"}

# ---------------------------------------------------------------- specialties
SPECIALTIES = {
    "general_medicine": ("General physician", [r"general physician", r"\bgp\b", r"family doctor", r"\bphysician\b", r"\bmbbs\b"]),
    "pediatrics": ("Paediatrician", [r"p(a)?ediatric", r"child specialist", r"bach?ch?o(n)? ke doctor", r"बच्चों के डॉक्टर", r"baby doctor"]),
    "gynecology": ("Gynaecologist", [r"gyn(a)?ec", r"gynae", r"obstetric", r"pregnan", r"\bperiods?\b", r"menstru", r"स्त्री रोग", r"महिला रोग"]),
    "orthopedics": ("Orthopaedic", [r"ortho(?!dont)", r"bone doctor", r"haddi", r"हड्डी", r"fracture", r"joint pain", r"knee pain", r"back pain", r"ghutn", r"घुटन", r"sprain", r"moch"]),
    "dermatology": ("Dermatologist", [r"derma", r"skin (doctor|specialist|problem|rash|allergy)", r"twacha", r"त्वचा", r"\bacne\b", r"pimple", r"hair ?fall", r"itching", r"khujli", r"खुजली"]),
    "ent": ("ENT specialist", [r"\bent\b", r"ear,? nose", r"kaa?n,? naa?k", r"\bkaa?n (me|mein) dard", r"कान", r"sinus", r"tonsil"]),
    "ophthalmology": ("Eye specialist", [r"eye (doctor|specialist)", r"ophthalm", r"aa?nkh", r"आ(ँ|ं)ख"]),
    "cardiology": ("Cardiologist", [r"cardio", r"heart (doctor|specialist|problem)", r"dil ka doctor", r"दिल के डॉक्टर", r"palpitation", r"dhadkan", r"\bbp\b (high|problem)", r"blood pressure"]),
    "urology": ("Urologist", [r"urolog", r"urine (problem|infection)", r"burning urin", r"peshab", r"पेशाब", r"kidney stone", r"pathri", r"पथरी", r"prostate"]),
    "neurology": ("Neurologist", [r"neurolog", r"migraine", r"numbness", r"\bnerve"]),
    "psychiatry": ("Psychiatrist", [r"psychiatr", r"anxiety", r"depress", r"mental health", r"panic", r"insomnia", r"neend nahi", r"ghabrahat", r"घबराहट"]),
    "dentistry": ("Dentist", [r"dentist", r"dental", r"\bdaa?nt\b", r"दा(ँ|ं)त", r"tooth", r"teeth"]),
    "pulmonology": ("Chest specialist", [r"pulmonolog", r"chest specialist", r"asthma", r"(chronic|persistent) cough", r"\blungs?\b"]),
    "gastroenterology": ("Gastroenterologist", [r"gastro", r"acidity", r"stomach (pain|problem|ache)", r"pet (me|mein) dard", r"पेट", r"\bliver\b", r"constipation", r"\bpiles\b", r"indigestion"]),
    "endocrinology": ("Endocrinologist", [r"endocrin", r"diabet", r"sugar (high|problem|patient|badh)", r"thyroid", r"hormone"]),
}
SPECIALTY_TAGS = list(SPECIALTIES)

# ---------------------------------------------------------------- symptom hints
# Keyword shortcuts for what a doctor usually starts with. They are hints for the classifier and the fallback when no
# model answers; MedGemma's own judgement on the structured facts comes first.
SYMPTOM_HINTS = [
    (r"(burning|jalan|जलन).{0,25}(urin|peshab|पेशाब)|(urin|peshab|पेशाब).{0,25}(burning|jalan|जलन)|frequent urination|baar baar peshab",
     "urology", ["urine_test", "urine_culture"], []),
    (r"kidney stone|pathri|पथरी|flank pain", "urology", ["ultrasound", "urine_test"], []),
    (r"(fever|bukh?aa?r|बुखार).{0,40}(\b[3-9]\b|three|four|five|teen|chaar|paanch|week|hafte)\s*(din|days?|दिन|week|hafte)?", "general_medicine", ["blood_test"], []),
    (r"(cough|khaa?nsi|खांसी).{0,40}(2|two|do|3|three|teen) (week|hafte|हफ्ते)|(chronic|persistent) cough", "pulmonology", ["xray", "blood_test"], []),
    (r"(knee|joint|back|shoulder|ankle|wrist|ghutn|kamar|कमर|घुटन).{0,20}(pain|dard|दर्द)|sprain|moch|fell|gir (gaya|gayi|gaye)|twisted", "orthopedics", ["xray"], []),
    (r"(tooth|daa?nt|दा(ँ|ं)त).{0,20}(pain|dard|दर्द|ache)|toothache", "dentistry", [], []),
    (r"rash|itch|khujli|खुजली|pimple|acne|hair ?fall", "dermatology", [], []),
    (r"(stomach|abdomen|abdominal|pet|पेट).{0,20}(pain|dard|दर्द|ache)", "gastroenterology", ["ultrasound", "blood_test"], []),
    (r"palpitation|dhadkan tez|heart racing|(mild|halka) chest discomfort", "cardiology", ["ecg", "echo"], []),
    (r"migraine|(frequent|daily|bar bar|roz) headache|sar ?dard roz", "neurology", [], []),
    (r"diabet|sugar (high|badh|zyada)|thyroid", "endocrinology", ["blood_test"], []),
    (r"pregnan|missed period|period (late|miss)", "gynecology", ["ultrasound", "blood_test"], []),
    (r"anxiety|depress|panic|can'?t sleep|insomnia|neend nahi|ghabrahat|घबराहट|stress", "psychiatry", [], ["counselling"]),
    (r"(ear|kaa?n|कान).{0,15}(pain|dard|दर्द|discharge|block)|hearing (loss|problem)|kam sunai", "ent", ["audiometry"], []),
    (r"(eye|aa?nkh|आ(ँ|ं)ख).{0,15}(red|lal|pain|dard|itch|watering)|blurry vision|dhundhla", "ophthalmology", ["eye_test"], []),
    (r"\bcut\b|wound|chot|चोट|scrape|graze|ghaa?v|घाव", "general_medicine", [], ["wound_dressing", "tetanus_shot"]),
    (r"(dog|monkey|cat) bite|(kutt?e|bandar|billi) ne kaa?t|कुत्ते ने काट", "general_medicine", [], ["anti_rabies", "wound_dressing"]),
    (r"\bburn(ed|t)?\b|jal (gaya|gayi|gaye)|जल गया", "general_medicine", [], ["wound_dressing"]),
    (r"wheez|asthma|saa?ns phool", "pulmonology", ["pft"], ["nebulization"]),
]

# ---------------------------------------------------------------- preferences (shift the matcher's weights)
PREFERENCES = {
    "quality": [r"\bbest\b", r"\btop\b", r"top[- ]rated", r"highly rated", r"experienced", r"\bsenior\b", r"good doctor", r"ac?h?ch?a doctor",
                r"badhiya", r"बढ़िया", r"अच्छा डॉक्टर", r"bad experience", r"(wasn'?t|was not|not) (good|helpful)", r"useless", r"previous doctor",
                r"pichh?le doctor", r"kharab experience", r"reliable", r"trusted", r"awesome", r"excellent", r"specialist hi chahiye"],
    "nearby": [r"near(by| me| my)", r"close (by|to)", r"nearest", r"\bpaa?s (me|mein|wala|ka|ki)\b", r"nazdeek", r"नज़दीक", r"\bपास\b",
               r"walking distance", r"can'?t travel", r"cannot travel", r"door nahi", r"not far"],
    "budget": [r"\bcheap", r"affordable", r"\bbudget\b", r"low cost", r"inexpensive", r"\bsasta\b", r"kam (paise|kharch|fee)", r"सस्ता", r"free clinic", r"government"],
    "soon": [r"\btoday\b", r"tonight", r"right now", r"\basap\b", r"urgent", r"immediately", r"\baaj\b", r"\babhi\b", r"jaldi", r"turant", r"\bआज\b", r"\bअभी\b", r"जल्दी"],
    "female": [r"lady doctor", r"female (doctor|gyn)", r"woman doctor", r"mahila doctor", r"महिला डॉक्टर"],
    "home": [r"at home", r"home visit", r"\bghar (pe|par|aa)", r"घर (पर|पे|आ)"],
    "online": [r"\bonline\b", r"video", r"teleconsult", r"phone (pe|par|call|consult)", r"ऑनलाइन", r"वीडियो"],
    "consult_only": [r"just (a |need a |want a )?(consult|doctor|checkup)", r"only (a )?(consult|doctor)", r"sirf (doctor|consult|dikhana)",
                     r"bas doctor (ko )?dikhana", r"no tests?", r"test nahi", r"without (any )?tests?", r"बस डॉक्टर"],
    "hospital": [r"hospital", r"अस्पताल", r"aspataa?l"],
}

_C = lambda pats: [re.compile(p, re.I) for p in pats]  # noqa: E731
_SERV = {t: _C(v[3]) for t, v in SERVICES.items()}
_SPEC = {t: _C(v[1]) for t, v in SPECIALTIES.items()}
_HINT = [(re.compile(p, re.I), s, tests, svcs) for p, s, tests, svcs in SYMPTOM_HINTS]
_PREF = {k: _C(v) for k, v in PREFERENCES.items()}


@dataclass
class Hints:
    services: list[str]          # explicitly asked for by name (hard requirements)
    specialties: list[str]       # specialties the words point to
    suggested_tests: list[str]   # keyword-level guesses at first tests
    suggested_services: list[str]
    preferences: list[str]

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v}


def analyse(text: str) -> Hints:
    t = text.lower()
    services = [tag for tag, pats in _SERV.items() if any(p.search(t) for p in pats)]
    if "anti_rabies" in services and "vaccination" in services:
        services.remove("vaccination")
    if "injection" in services and any(SERVICES[s][1] == "vaccine" for s in services):
        services.remove("injection")  # "tetanus ka injection" is a tetanus shot, not a generic injection
    specs = [tag for tag, pats in _SPEC.items() if any(p.search(t) for p in pats)]
    tests, svcs = [], []
    for pat, spec, ts, ss in _HINT:
        if pat.search(t):
            specs.append(spec)
            tests += ts
            svcs += ss
    dedup = lambda xs: list(dict.fromkeys(xs))  # noqa: E731
    return Hints(services=dedup(services), specialties=dedup(specs), suggested_tests=dedup(tests),
                 suggested_services=dedup(s for s in svcs if s not in services), preferences=[k for k, pats in _PREF.items() if any(p.search(t) for p in pats)])


def label(tag: str) -> str:
    if tag in SERVICES:
        return SERVICES[tag][0]
    if tag in SPECIALTIES:
        return SPECIALTIES[tag][0]
    return tag.replace("_", " ")


def map_free_text(items: list[str]) -> list[str]:
    """Map model/structurer phrases ("urine culture test", "MRI of knee") onto service tags."""
    out = []
    for it in items:
        low = it.lower().strip()
        if low in SERVICES:
            out.append(low)
            continue
        out += [tag for tag, pats in _SERV.items() if any(p.search(low) for p in pats)]
    return list(dict.fromkeys(out))
