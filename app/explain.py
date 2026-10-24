"""Conversation voice (Qwen): questions and explanations in the user's language. It is given extracted facts, never the
user's raw words, so instructions typed by a user cannot steer what it writes. Gate replies (redirect, misheard) and the
call-112 safety line are pre-written, not generated. Every line the user sees passes scrub(): the brief forbids naming a
disease or a medicine. Templates take over if every model call fails."""
import re

from app import llm

REPLY_LANG = {"en": "English", "hi": "Hindi in Devanagari script", "hinglish": "Hinglish: romanised Hindi in Latin script"}

ASK_SYS = """You are the voice of a home-healthcare app talking to a patient or family member.
Write ONE short, kind follow-up question (max 25 words) that asks for the missing facts in plain everyday words.
Write it in the reply language you are given, exactly. No diagnosis, no advice, no medicine. Output only the question text."""

CLARIFY_SYS = """You are the voice of a home-healthcare app. The user's message was confusing for the reason given.
Write ONE short, calm question (max 25 words) that clears up exactly that confusion and keeps the user on their health
problem (who is unwell, what is happening). Do not repeat nonsense back, do not argue, no diagnosis, no medicine.
Write in the reply language you are given, exactly. Output only the question text."""

SAFETY_SYS = """You are the voice of a home-healthcare app. A possible danger sign was noticed. Turn the check question into
ONE short, calm question (max 20 words) for the family, in the reply language you are given, exactly. Plain words, no
medical terms, no diagnosis. Output only the question text."""

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
# gate replies: fixed text, so nothing a user types can turn them into something else
REDIRECT = {
    "en": "I can only help with health needs. Who is unwell, and what is happening?",
    "hi": "मैं सिर्फ़ सेहत से जुड़ी मदद कर सकता हूँ। कौन बीमार है, और क्या हो रहा है?",
    "hinglish": "Main sirf health se judi madad kar sakta hoon. Kaun beemar hai, aur kya ho raha hai?",
}
CALM = {
    "en": "I'm here to help. Tell me who is unwell and what is happening, and I'll find the right care.",
    "hi": "मैं मदद के लिए हूँ। बताइए कौन बीमार है और क्या हो रहा है, मैं सही मदद ढूँढूँगा।",
    "hinglish": "Main madad ke liye hoon. Bataiye kaun beemar hai aur kya ho raha hai, main sahi madad dhoondhunga.",
}
MISHEARD = {
    "en": "Sorry, I may have misheard. Did you say: “{t}”? Please say it again or type it.",
    "hi": "माफ़ कीजिए, शायद मैं ठीक से सुन नहीं पाया। क्या आपने कहा: “{t}”? कृपया दोबारा बोलें या लिखें।",
    "hinglish": "Sorry, shayad main theek se sun nahi paaya. Kya aapne kaha: “{t}”? Please dobara boliye ya type kariye.",
}
# the safety line is pre-written like the emergency text; pipeline.SAFETY_MARK finds it in the history ("112")
SAFETY_LINE = {
    "en": "If this is happening right now or getting worse, call 112 (ambulance 108) immediately.",
    "hi": "अगर यह अभी हो रहा है या बिगड़ रहा है, तो तुरंत 112 (एम्बुलेंस 108) पर कॉल करें।",
    "hinglish": "Agar yeh abhi ho raha hai ya badh raha hai, to turant 112 (ambulance 108) par call karein.",
}
SAFETY_ASK = {
    "en": "Is any of this happening right now: {s}?",
    "hi": "क्या इनमें से कुछ अभी हो रहा है: {s}?",
    "hinglish": "Kya inmein se kuch abhi ho raha hai: {s}?",
}
FALLBACK_EXPLAIN = {
    "paramedic_home_visit": {"en": "A Medic will visit today to check the patient at home.", "hi": "आज एक मेडिक घर आकर मरीज़ की जाँच करेंगे।", "hinglish": "Aaj ek Medic ghar aakar patient ki jaanch karenge."},
    "home_nursing": {"en": "A trained Medic will come home today for this procedure.", "hi": "आज एक प्रशिक्षित मेडिक घर आकर यह काम करेंगे।", "hinglish": "Aaj ek trained Medic ghar aakar yeh procedure karenge."},
    "lab_test_home": {"en": "A sample collector will come home at a scheduled time for the test.", "hi": "तय समय पर सैंपल लेने वाले घर आएँगे।", "hinglish": "Tay samay par sample collector ghar aayenge."},
    "teleconsultation": {"en": "A doctor can help you today on a video call.", "hi": "आज डॉक्टर वीडियो कॉल पर आपकी मदद करेंगे।", "hinglish": "Aaj doctor video call par aapki madad karenge."},
}

# The brief forbids naming a disease or a medicine; prompts ask for that, this makes sure on every line shown.
DISEASE = re.compile(r"\b(dengue|malaria|typhoid|covid|corona|influenza|pneumonia|tuberculosis|diabetes|diabetic|arthritis|migraine|"
                     r"viral fever|bacterial|cancer|asthma|uti|chikungunya|jaundice|hepatitis|hypertension|appendicitis|gastritis|"
                     r"sinusitis|bronchitis|anemia|anaemia|heart attack|stroke)\b", re.I)
MEDICINE = re.compile(r"\b(paracetamol|crocin|dolo|calpol|ibuprofen|combiflam|aspirin|antibiotics?|antivirals?|amoxicillin|"
                      r"azithromycin|cetirizine|antacids?|\d+\s?mg)\b", re.I)


def scrub(text: str | None, fallback: str | None = None) -> str | None:
    """Drop every sentence that names a disease or a medicine; nothing left -> fallback."""
    if not text:
        return text
    keep = [x for x in re.split(r"(?<=[.!?।])\s+", text) if not (DISEASE.search(x) or MEDICINE.search(x))]
    return " ".join(keep) or fallback


def _say(system: str, prompt: str) -> str:
    return llm.chat(system, prompt).strip().strip('"')


def follow_up(missing: list[str], context: str, lang: str, guide: str = "") -> str:
    """context = a facts summary (never the raw user text); guide = protocol cards for the complaint."""
    proto = f"\nUseful questions for this complaint (use them to word the missing facts well): {guide}" if guide else ""
    try:
        return _say(ASK_SYS, f"Reply language: {REPLY_LANG[lang]}\nWhat we know: {context}\nMissing facts: {missing}{proto}")
    except Exception:  # noqa: BLE001
        return FALLBACK_ASK[lang]


def clarify(notes: list[str], context: str, lang: str) -> str:
    try:
        return _say(CLARIFY_SYS, f"Reply language: {REPLY_LANG[lang]}\nWhat we know: {context}\nWhat is confusing: {notes}")
    except Exception:  # noqa: BLE001
        return FALLBACK_ASK[lang]


def safety_check(check_question: str, signs: list[str], lang: str) -> tuple[str, str]:
    """-> (question ending with the pre-written call-112 line, the line on its own for the UI banner)."""
    try:
        q = _say(SAFETY_SYS, f"Reply language: {REPLY_LANG[lang]}\nCheck question: {check_question or 'Is this happening right now?'}\n"
                             f"Danger signs noticed: {signs}")
    except Exception:  # noqa: BLE001
        q = SAFETY_ASK[lang].format(s=", ".join(signs) or "this")
    return f"{q} {SAFETY_LINE[lang]}", SAFETY_LINE[lang]


REDIRECT_AGAIN = {
    "en": "Please tell me about the health problem, for example: “my father has had fever for 2 days”. Who is unwell?",
    "hi": "कृपया सेहत की परेशानी बताइए, जैसे: “पापा को दो दिन से बुखार है”। कौन बीमार है?",
    "hinglish": "Please health problem bataiye, jaise: “papa ko do din se bukhar hai”. Kaun beemar hai?",
}
REDIRECT_THIRD = {
    "en": "I'm a health assistant, so I can only help with a health problem. Type or say what is wrong and for whom.",
    "hi": "मैं सेहत से जुड़ी मदद के लिए हूँ। लिखिए या बोलिए कि किसे क्या परेशानी है।",
    "hinglish": "Main health assistant hoon, sirf sehat ki pareshani mein madad kar sakta hoon. Likhiye ya boliye kise kya problem hai.",
}
GATE_TEXTS = {v for d in (REDIRECT, CALM, REDIRECT_AGAIN, REDIRECT_THIRD) for v in d.values()}
END = {
    "en": "I'm not able to help further in this conversation. If someone is unwell, please start a new chat and tell me who it is "
          "and what is happening. In an emergency, call 112 (ambulance 108).",
    "hi": "मैं इस बातचीत में आगे मदद नहीं कर पाऊँगा। अगर कोई बीमार है, तो नई चैट शुरू करके बताइए कि कौन है और क्या हो रहा है। "
          "आपात स्थिति में 112 (एम्बुलेंस 108) पर कॉल करें।",
    "hinglish": "Main is baatcheet mein aage madad nahi kar paaunga. Agar koi beemar hai, to nayi chat shuru karke bataiye kaun hai "
                "aur kya ho raha hai. Emergency mein 112 (ambulance 108) par call karein.",
}


def redirect(lang: str, abusive: bool = False, before: int = 0) -> str:
    """Fixed text; from the second redirect on, an example of what to say instead of the same line again."""
    if before:
        return (REDIRECT_AGAIN if before % 2 else REDIRECT_THIRD)[lang]
    return (CALM if abusive else REDIRECT)[lang]


def misheard(transcript: str, lang: str) -> str:
    return MISHEARD[lang].format(t=transcript.strip()[:160])


def explanation(care_type: str, reasoning: str, context: str, lang: str) -> str:
    try:
        return _say(EXPLAIN_SYS, f"Reply language: {REPLY_LANG[lang]}\nWhat we know: {context}\n"
                                 f"Care arranged: {CARE_PLAIN[care_type]}\nWhy: {reasoning}")
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
                      context: str, lang: str) -> str:
    when = SOON_WORDS.get(seriousness, "soon")
    try:
        doctor = f"; doctor: {specialty}" if specialty else ""
        prompt = (f"Reply language: {REPLY_LANG[lang]}\nWhat we know: {context}\nCare: {care}, {when}{doctor}\n"
                  f"Tests a doctor may ask for: {', '.join(tests) or 'none'}\n"
                  f"First suggested place: {top_name or 'none found'} ({top_reason or ''})")
        return _say(SMART_SYS, prompt)
    except Exception:  # noqa: BLE001
        return SMART_FALLBACK[lang].format(place=top_name or "a nearby clinic", when=when,
                                           when_hi={"today": "आज ही", "in the next 2-3 days": "अगले 2-3 दिनों में"}.get(when, "जल्द"),
                                           when_hl={"today": "aaj hi", "in the next 2-3 days": "agle 2-3 dinon mein"}.get(when, "jaldi"))
