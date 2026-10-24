"""Conversation voice (Qwen): the classifier's missing_info -> one follow-up question, and the decision -> a plain
explanation, both in the user's language. Templates take over if every model call fails."""
import re

from app import llm

# The brief forbids naming a disease or a medicine. Prompts ask for that; this makes sure, on every line the user sees.
DISEASE = re.compile(r"\b(dengue|malaria|typhoid|covid|corona|influenza|flu|pneumonia|tuberculosis|tb|diabetes|diabetic|arthritis|"
                     r"migraine|infections?|viral|bacterial|ulcers?|cancer|asthma|uti|chikungunya|jaundice|hepatitis|"
                     r"hypertension|appendicitis|gastritis|sinusitis|bronchitis|anemia|anaemia|allerg(y|ic))\b", re.I)
MEDICINE = re.compile(r"\b(paracetamol|crocin|dolo|calpol|ibuprofen|combiflam|aspirin|antibiotics?|antivirals?|amoxicillin|"
                      r"azithromycin|cetirizine|antacids?|tablets?|capsules?|\d+\s?mg|syrups?)\b", re.I)


def scrub(text: str | None, fallback: str | None = None) -> str | None:
    """Drop every sentence that names a disease or medicine; nothing left -> fallback."""
    if not text:
        return text
    keep = [x for x in re.split(r"(?<=[.!?।])\s+", text) if not (DISEASE.search(x) or MEDICINE.search(x))]
    return " ".join(keep) or fallback

REPLY_LANG = {"en": "English", "hi": "Hindi in Devanagari script", "hinglish": "Hinglish: romanised Hindi in Latin script"}

ASK_SYS = """You are the voice of a home-healthcare app talking to a patient or family member.
Write ONE short, kind follow-up question (max 20 words) that asks for the missing facts in plain everyday words.
Write it in the reply language you are given, exactly. No diagnosis, no advice. Output only the question text."""

EXPLAIN_SYS = """You are the voice of a home-healthcare app. In 1-2 short, warm sentences tell the user what care was arranged
and why, in plain words a non-medical person understands. Write in the reply language you are given, exactly.
Never name a disease, never suggest medicine. Output only the sentences."""

CARE_PLAIN = {
    "paramedic_home_visit": "a Medic visits at home to check the patient (a doctor can join by video)",
    "home_nursing": "a trained Medic comes home to do the nursing procedure",
    "lab_test_home": "a sample collector comes home for the test",
    "teleconsultation": "a doctor speaks with you on a video call",
}
FALLBACK_ASK = {
    "en": "Happy to help. Who is this for, and what is the main concern or service you need?",
    "hi": "हम मदद के लिए हैं। यह किसके लिए है, और मुख्य परेशानी या ज़रूरत क्या है?",
    "hinglish": "Hum madad ke liye hain. Yeh kiske liye hai, aur main pareshani ya zaroorat kya hai?",
}
FALLBACK_EXPLAIN = {
    "paramedic_home_visit": {"en": "A Medic will visit today to check the patient at home.", "hi": "आज एक मेडिक घर आकर मरीज़ की जाँच करेंगे।", "hinglish": "Aaj ek Medic ghar aakar patient ki jaanch karenge."},
    "home_nursing": {"en": "A trained Medic will come home today for this procedure.", "hi": "आज एक प्रशिक्षित मेडिक घर आकर यह काम करेंगे।", "hinglish": "Aaj ek trained Medic ghar aakar yeh procedure karenge."},
    "lab_test_home": {"en": "A sample collector will come home at a scheduled time for the test.", "hi": "तय समय पर सैंपल लेने वाले घर आएँगे।", "hinglish": "Tay samay par sample collector ghar aayenge."},
    "teleconsultation": {"en": "A doctor can help you today on a video call.", "hi": "आज डॉक्टर वीडियो कॉल पर आपकी मदद करेंगे।", "hinglish": "Aaj doctor video call par aapki madad karenge."},
}


def follow_up(missing: list[str], user_text: str, lang: str) -> str:
    try:
        q = llm.chat(ASK_SYS, f"Reply language: {REPLY_LANG[lang]}\nUser's message: {user_text}\nMissing facts: {missing}")
        return q.strip().strip('"')
    except Exception:  # noqa: BLE001
        return FALLBACK_ASK[lang]


def explanation(care_type: str, reasoning: str, user_text: str, lang: str) -> str:
    try:
        return llm.chat(EXPLAIN_SYS, f"Reply language: {REPLY_LANG[lang]}\nUser said: {user_text}\n"
                                     f"Care arranged: {CARE_PLAIN[care_type]}\nWhy: {reasoning}").strip().strip('"')
    except Exception:  # noqa: BLE001
        return FALLBACK_EXPLAIN[care_type][lang]


SMART_SYS = """You are the voice of a care-navigation app. In at most 2 short, warm sentences tell the user what kind of care to get,
how soon, and why the first suggested place fits them. If tests are listed, say a doctor may ask for them (do not insist).
Plain words, in the reply language you are given, exactly. Never name a disease, never suggest medicine. Output only the sentences."""
SOON_WORDS = {"urgent": "today", "soon": "in the next 2-3 days", "routine": "at a convenient time"}
SMART_FALLBACK = {
    "en": "Please visit {place} {when}.",
    "hi": "कृपया {when_hi} {place} जाएँ।",
    "hinglish": "Kripya {when_hl} {place} jaayein.",
}


def smart_explanation(care: str, seriousness: str, specialty: str | None, tests: list[str], top_name: str | None, top_reason: str | None,
                      user_text: str, lang: str) -> str:
    when = SOON_WORDS.get(seriousness, "soon")
    try:
        doctor = f"; doctor: {specialty}" if specialty else ""
        prompt = (f"Reply language: {REPLY_LANG[lang]}\nUser said: {user_text}\nCare: {care}, {when}{doctor}\n"
                  f"Tests a doctor may ask for: {', '.join(tests) or 'none'}\n"
                  f"First suggested place: {top_name or 'none found'} ({top_reason or ''})")
        return llm.chat(SMART_SYS, prompt).strip().strip('"')
    except Exception:  # noqa: BLE001
        return SMART_FALLBACK[lang].format(place=top_name or "a nearby clinic", when=when,
                                           when_hi={"today": "आज ही", "in the next 2-3 days": "अगले 2-3 दिनों में"}.get(when, "जल्द"),
                                           when_hl={"today": "aaj hi", "in the next 2-3 days": "agle 2-3 dinon mein"}.get(when, "jaldi"))
