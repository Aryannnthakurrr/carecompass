"""Conversation voice (Qwen): the classifier's missing_info -> one follow-up question, and the decision -> a plain
explanation, both in the user's language. Templates take over if every model call fails."""
from app import llm

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
