"""When to ask another question, as one equation (every mode, every turn; emergencies never get here).

    ask the candidate with the largest  V = w * 0.6**a   if  V > C = 0.25*A + 0.3*D   (C = inf if the user says "just give me a doctor")

w  how much the answer can change WHERE the patient goes (fever -> age 1.0: infant = emergency, adult = home visit)
a  times this question was already asked (each repeat is worth 40% less)
A  questions asked so far, D  those dodged (asked, still unknown). Off-topic turns cost nothing.
Candidates: the slots below for the symptoms in CaseFacts (asked from templates: instant, never off-script), plus whatever the
classifier says is missing (w 0.45, phrased by Qwen). Hard cap: MAX_TURNS replies from us.
"""
import re

AGE_KNOWN = re.compile(r"\d|infant|baby|newborn|child|kid|toddler|teen|adult|elderly|old", re.I)
IMPATIENT = re.compile(r"just (give|show|book|find|tell)|give me (a |the )?doctor|no more questions|stop asking|enough questions|"
                       r"\bbas\b.{0,25}(doctor|batao|bata do)|seedha (doctor|batao)|doctor (do|dikhao|bhejo|bata)", re.I)
MAX_TURNS = 8
CLASSIFIER_W = 0.45


def _has(facts, pat: str) -> bool:
    return any(re.search(pat, s, re.I) for s in facts.symptoms_present)


# name: (w, applies(facts), known(facts), "was it asked" pattern, (first ask, re-ask that says why) per language)
SLOTS = {
    "age": (1.0, lambda f: _has(f, r"fever|vomit|diarrh|loose (motion|stool)|dehydrat"),
            lambda f: bool(f.special_population or AGE_KNOWN.search(f.patient or "")),
            r"\bage\b|how old|umar|उम्र|kitne saal|कितने साल",
            ({"en": "How old is the patient?", "hi": "मरीज़ की उम्र कितनी है?", "hinglish": "Patient ki umar kitni hai?"},
             {"en": "I ask because fever in a small baby needs very different care than in an adult. Roughly how old is the patient?",
              "hi": "मैं इसलिए पूछ रहा हूँ क्योंकि छोटे बच्चे के बुखार में बिल्कुल अलग देखभाल चाहिए। मरीज़ की लगभग उम्र क्या है?",
              "hinglish": "Main isliye pooch raha hoon kyunki chhote bachche ke bukhar mein bilkul alag care chahiye. Patient ki lagbhag umar kya hai?"})),
    "where": (0.8, lambda f: any(re.fullmatch(r"(body )?(pain|ache|swelling|bleeding)", s.strip(), re.I) for s in f.symptoms_present),
              lambda f: False,  # applies only while the symptom has no body part, so applying = unknown
              r"\bwhere\b|which part|kahan|kahaan|कहाँ|कहां|किस हिस्से|kis hisse",
              ({"en": "Where exactly is it?", "hi": "यह ठीक कहाँ हो रहा है?", "hinglish": "Yeh theek kahan ho raha hai?"},
               {"en": "Which part of the body is it in? That decides which doctor fits.", "hi": "शरीर के किस हिस्से में है? उसी से सही डॉक्टर तय होगा।",
                "hinglish": "Body ke kis hisse mein hai? Usi se sahi doctor tay hoga."})),
    "duration": (0.6, lambda f: bool(f.symptoms_present), lambda f: bool(f.duration),
                 r"how long|since when|kab se|कब से|how many days|kitne din|कितने दिन|कितने समय|kitne time",
                 ({"en": "Since when has this been going on?", "hi": "यह कब से हो रहा है?", "hinglish": "Yeh kab se ho raha hai?"},
                  {"en": "Roughly how long has it been: hours, days or weeks?", "hi": "लगभग कितने समय से है: कुछ घंटे, दिन या हफ़्ते?",
                   "hinglish": "Lagbhag kitne time se hai: kuch ghante, din ya hafte?"})),
    "temperature": (0.3, lambda f: _has(f, r"fever"),
                    lambda f: bool(f.severity or any(re.search(r"\d|temp|degree|°|high|mild", v, re.I) for v in f.vitals_reported)),
                    r"temperature|how high|thermometer|kitna (tez|bukhar)|bukhar kitna|बुखार कितना|तापमान|कितना तेज़|थर्मामीटर",
                    ({"en": "How high is the fever, if you measured it?", "hi": "बुखार कितना है, अगर नापा हो?",
                      "hinglish": "Bukhar kitna hai, agar naapa ho?"},
                     {"en": "Does it feel very hot, or did a thermometer show a number?", "hi": "क्या बहुत तेज़ लग रहा है, या थर्मामीटर में कितना आया?",
                      "hinglish": "Kya bahut tez lag raha hai, ya thermometer mein kitna aaya?"})),
}


def choose(facts, missing: list[str], asked: list[str], user_input: str) -> tuple[str, str] | None:
    """-> (kind "slot"|"classifier", name or item, times already asked) | None (decide now). asked = our earlier replies."""
    if IMPATIENT.search(user_input) or len(asked) >= MAX_TURNS:
        return None
    times = {n: sum(bool(re.search(s[3], q, re.I)) for q in asked) for n, s in SLOTS.items()}
    unknown = [n for n, s in SLOTS.items() if s[1](facts) and not s[2](facts)]
    A = sum(times.values())
    D = sum(times[n] for n in unknown)
    cost = 0.25 * A + 0.3 * D
    cands = [(SLOTS[n][0] * 0.6 ** times[n], ("slot", n)) for n in unknown]
    other = sum(not any(re.search(s[3], q, re.I) for s in SLOTS.values()) for q in asked)  # replies that weren't slot questions
    cands += [(CLASSIFIER_W * 0.6 ** other, ("classifier", m)) for m in missing
              if not any(re.search(s[3], m, re.I) for s in SLOTS.values())]  # the table already owns age/duration/...
    best = max(cands, default=None, key=lambda c: c[0])
    return (best[1][0], best[1][1], times.get(best[1][1], 0)) if best and best[0] > cost else None


def template(slot: str, lang: str, times: int) -> str:
    """Never the same words twice in a row: the re-ask is reworded and says why it matters."""
    return SLOTS[slot][4][min(times, 1)][lang]


if __name__ == "__main__":  # the walkthroughs the weights were set from
    from app.schemas import CaseFacts
    _choose = choose
    choose = lambda *a: (r := _choose(*a)) and r[:2]  # noqa: E731
    F = lambda **k: CaseFacts(**{"symptoms_present": ["fever"], "symptoms_denied": [], "detected_language": "en", **k})  # noqa: E731
    junk = ["I'm here for health questions.", "Didn't catch that."]
    assert choose(F(), [], junk, "i am having fever") == ("slot", "age")                       # junk doesn't use up the age
    assert choose(F(duration="today"), [], junk + ["How old is the patient?"], "since today") == ("slot", "age")  # dodged once: ask again
    two = ["How old is the patient?", "How old is the patient?"]
    assert choose(F(), [], two, "not telling") is None                                        # dodged twice: decide
    assert choose(F(patient="self, 28 years"), [], ["How old?"], "28") == ("slot", "duration")
    assert choose(F(patient="self, 28", duration="2 days"), ["joint swelling?"], ["How old?", "Since when?"], "2 days") is None
    assert choose(F(), [], [], "just give me a doctor") is None
    pain = CaseFacts(symptoms_present=["pain"], symptoms_denied=[], detected_language="en")
    assert choose(pain, [], [], "i have pain") == ("slot", "where")
    assert choose(CaseFacts(symptoms_present=[], symptoms_denied=[], services_requested=["injection"], detected_language="en"),
                  ["what is the injection for?"], [], "injection lagwana hai") == ("classifier", "what is the injection for?")
    for n, slot in SLOTS.items():  # both wordings must count as "asked", or a re-ask would look like a first ask
        for v in slot[4]:
            assert all(re.search(slot[3], q, re.I) for q in v.values()), (n, v)
    print("asking: all walkthroughs hold")
