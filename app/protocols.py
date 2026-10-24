"""Symptom protocol cards: what to ask and which danger signs to look for, per complaint (retrieved knowledge).

Written for this app from widely published triage guidance (WHO child danger signs, standard adult red flags). They are
matched on the analyst's English symptom phrases (not the user's raw words) and handed to the classifier and the question
writer, so a fever gets asked about age, how high and how long, an injury about how it happened and whether they hit their
head, and so on, instead of a generic "tell me more". They never decide anything on their own.
"""
from app.schemas import CaseFacts

CARDS = {
    "fever": (("fever", "temperature", "chills", "pyrexia"),
              ["patient's age", "how high the temperature is", "since when", "drinking fluids and passing urine"],
              ["age under 3 months", "very sleepy or hard to wake", "not drinking or no urine", "rash that does not fade",
               "fits", "breathing difficulty", "stiff neck"]),
    "breathing / cough": (("cough", "breath", "wheez", "chest congestion"),
                          ["since when", "breathless at rest or only on effort", "fever with it"],
                          ["breathless at rest", "lips or face turning blue", "cannot speak full sentences", "chest pain"]),
    "abdominal pain": (("abdominal", "stomach", "belly", "abdomen"),
                       ["where exactly", "how bad, mild or severe", "since when", "vomiting or fever with it"],
                       ["severe pain that does not ease", "vomiting blood or black stools", "pregnant", "hard swollen belly"]),
    "headache": (("headache", "head pain", "migraine"),
                 ["since when", "how bad", "any vision change, weakness or vomiting"],
                 ["sudden worst-ever headache", "after a head injury", "with weakness, confusion or speech trouble",
                  "with high fever and stiff neck"]),
    "injury / fall": (("fall", "fell", "injur", "fracture", "sprain", "twist", "swollen wrist", "swollen ankle", "accident", "hit by",
                       "crash", "collision"),
                      ["how it happened", "can they move it or put weight on it", "did they hit their head"],
                      ["hit the head and vomiting or drowsy", "bone visible or limb badly bent", "heavy bleeding"]),
    "cut / bleeding": (("cut", "bleed", "wound", "laceration"),
                       ["where the bleeding is from", "does it stop when pressed", "how it happened"],
                       ["bleeding that does not stop with pressure", "deep or gaping wound", "bleeding in pregnancy"]),
    "vomiting / loose motions": (("vomit", "loose motion", "diarrh", "loose stool"),
                                 ["how many times", "keeping water down", "passing urine normally"],
                                 ["cannot keep water down", "no urine for 6 hours or more", "blood in vomit or stool",
                                  "very sleepy child"]),
    "weakness / not eating": (("weak", "not eating", "tired", "fatigue", "letharg", "sleepy", "drowsy", "appetite"),
                              ["since when", "eating and drinking", "any confusion, drowsiness or trouble getting up"],
                              ["new confusion or hard to wake", "cannot get up", "not drinking"]),
    "dizziness / fainting": (("dizz", "faint", "light-head", "lightheaded", "vertigo"),
                             ["did they faint", "since when", "only on standing or all the time"],
                             ["fainted", "with chest pain or palpitations", "with weakness or speech trouble"]),
    "rash / allergy": (("rash", "itch", "hives", "allerg"),
                       ["where on the body", "since when", "any face or lip swelling or breathing trouble"],
                       ["face, lip or throat swelling", "breathing difficulty", "rash with high fever that does not fade"]),
    "pregnancy": (("pregnan",),
                  ["how many months pregnant", "what is happening now"],
                  ["any bleeding", "severe headache or blurred vision", "severe abdominal pain", "baby moving less"]),
    "urinary": (("urin",),
                ["since when", "fever or back pain with it"],
                ["no urine", "high fever with back pain"]),
    "low mood / anxiety": (("anxi", "depress", "panic", "stress", "sad", "low mood"),
                           ["since when", "sleeping and eating", "any thoughts of harming themselves"],
                           ["thoughts of self-harm or suicide"]),
    "blood sugar": (("sugar", "glucose"),
                    ["the reading", "drowsy or confused", "since when"],
                    ["reading above 400 or below 70 with symptoms", "drowsy or confused"]),
}


# the one question that tells how serious each complaint is (asked when the user hasn't said how bad it is)
KEY_ASK = {
    "fever": "how high the temperature is",
    "breathing / cough": "whether they are breathless even at rest, or only when walking",
    "abdominal pain": "where exactly the pain is and how bad it is (mild, moderate or severe)",
    "headache": "how bad the headache is, and whether there is vomiting, blurred vision or weakness",
    "injury / fall": "what exactly happened, and whether they can move it or hit their head",
    "cut / bleeding": "how heavy the bleeding is, and whether it stops when you press a clean cloth on it",
    "vomiting / loose motions": "how many times, and whether they can keep water down",
    "weakness / not eating": "whether they are eating and drinking, and any confusion or trouble getting up",
    "dizziness / fainting": "whether they fainted or nearly fainted",
    "rash / allergy": "where the rash is, and whether there is any face or lip swelling",
    "pregnancy": "how many months pregnant, and what exactly is happening",
    "urinary": "whether there is fever or back pain with it",
    "low mood / anxiety": "whether they have had any thoughts of harming themselves",
    "blood sugar": "the reading, and whether they are drowsy or confused",
}


def key_ask(facts: CaseFacts) -> str | None:
    """The severity question for the most relevant complaint in the facts; None if no card matches."""
    hit = cards(facts, limit=1)
    return KEY_ASK.get(hit[0][0]) if hit else None


def cards(facts: CaseFacts, limit: int = 2) -> list[tuple[str, list[str], list[str]]]:
    """-> (name, questions, danger signs) for the complaints in the facts, most relevant first."""
    text = " ".join(facts.symptoms_present + facts.vitals_reported + facts.special_population).lower()
    hits = [(name, ask, danger) for name, (keys, ask, danger) in CARDS.items() if any(k in text for k in keys)]
    hits.sort(key=lambda h: FIRST.index(h[0]) if h[0] in FIRST else len(FIRST))  # the most time-critical complaint first
    return hits[:limit]


FIRST = ["cut / bleeding", "injury / fall", "pregnancy", "low mood / anxiety", "blood sugar", "fever", "breathing / cough"]


def guide(facts: CaseFacts) -> str:
    """One line per matching card for a model prompt; '' if none match."""
    return "\n".join(f"Protocol for {name}: ask about {', '.join(ask)}; danger signs: {', '.join(danger)}."
                     for name, ask, danger in cards(facts))
