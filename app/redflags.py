"""Deterministic red-flag rules (English / romanised Hinglish / Devanagari). Run on the raw words before any
model, and again on the structurer's English symptoms. Any hit routes to emergency_112."""
import re

# flag -> patterns. Flags use the organiser vocabulary; EXTRA are emergencies the vocabulary has no word for.
RULES = {
    "chest_pain": [
        r"chest (pain|tightness|pressure)", r"chest (feels |is |feeling )?tight", r"tight(ness)? (in (the|my|his|her) )?chest", r"pain in (the |my |his |her )?chest",
        r"heart attack", r"see+ne? (me|mein|main|mai)? ?(dard|dabav|jakdan)", r"chh?aa?ti (me|mein|main)? ?dard", r"dil ka daura",
        r"सीने (में )?(दर्द|दबाव|जकड़न)", r"छाती (में )?दर्द", r"दिल का दौरा",
    ],
    "breathlessness": [
        r"(can'?t|cannot|unable to|not able to) breathe", r"(difficulty|trouble|problem) (in )?breathing", r"short(ness)? of breath",
        r"breathless", r"gasping", r"blue lips", r"lips (are )?(turning )?blue",
        r"saa?n?s (nahi|nhi|nahin) (aa|le pa)", r"saa?n?s lene (me|mein|main) (bahut |kaafi )?(dikkat|takleef|taklif|pareshani|mushkil)",
        r"saa?n?s phool", r"ho+th neele",
        r"सा(ं|ँ)स (नहीं|नही) (आ|ले)", r"सा(ं|ँ)स लेने में (बहुत |काफ़ी |काफी )?(दिक्कत|तकलीफ़?|परेशानी|मुश्किल)", r"सा(ं|ँ)स फूल", r"होंठ नीले",
    ],
    "unconsciousness": [
        r"unconscious", r"unresponsive", r"not responding", r"collapsed", r"passed out", r"fainted", r"won'?t wake",
        r"behosh", r"response nahi de", r"hosh (nahi|nhi|mein nahi)", r"jawab nahi de",
        r"बेहोश", r"होश (नहीं|में नहीं)", r"जवाब नहीं दे", r"गिर (गए|गई|गया).{0,20}(बोल|जवाब|होश)",
    ],
    "severe_bleeding": [
        r"(heavy|severe|lot of|lots of|uncontrolled|profuse) bleeding", r"bleeding (that )?(won'?t|will not|doesn'?t|does not|is not|isn'?t) stop",
        r"bleeding (ruk|band) (nahi|nhi)", r"khoo?n (ruk|band) (nahi|nhi)", r"bahut khoo?n", r"vomiting blood", r"khoo?n ki ulti",
        r"pregnan\w*.{0,30}bleed", r"bleed\w*.{0,30}pregnan",
        r"खून (नहीं|नही) रुक", r"बहुत खून", r"खून की उल्टी", r"गर्भ.{0,30}खून",
    ],
    "stroke_symptoms": [
        r"face (is )?droop", r"facial droop", r"slurred speech", r"one side (of (the|his|her) body )?(is )?(weak|numb)",
        r"(can'?t|cannot|unable to) move (his |her |my )?(right |left )?(arm|leg|side)", r"\bstroke\b", r"paralys",
        r"mu+n?h tedha", r"chehra.{0,20}tedha", r"bolne (me|mein|main) (dikkat|takleef|taklif)", r"haath.?pair sunn", r"lakwa",
        r"मु(ं|ँ)ह टेढ़ा", r"चेहरा.{0,20}टेढ़ा", r"बोलने में (दिक्कत|तकलीफ़?)", r"हाथ.?पैर सुन्न", r"लकवा",
    ],
    "seizure": [
        r"seizure", r"convuls", r"\bfits?\b", r"jhatk?e", r"daura (pad|padh|aa)", r"mirgi",
        r"झटके", r"दौरा (पड़|आ)", r"मिर्गी",
    ],
    "suicidal_ideation": [
        r"suicid", r"kill (my|him|her)self", r"end (my|his|her) life", r"(don'?t|do not) want to live", r"no reason to live",
        r"jee+ne ka (mann?|man) nahi", r"jeena nahi chaht", r"marna chaht", r"khudkushi", r"aatmahatya",
        r"जीने का मन नहीं", r"जीना नहीं चाहत", r"मरना चाहत", r"खुदकुशी", r"आत्महत्या",
    ],
    "severe_dehydration": [
        r"severe(ly)? dehydrat", r"(no|not passed|hasn'?t passed) urine", r"sunken eyes",
        r"peshab (nahi|nhi) (aaya|hua|kiya)", r"पेशाब (नहीं|नही) (आया|हुआ|किया)",
    ],
}
EXTRA = {
    "poisoning": [r"poison", r"swallowed .{0,20}(pesticide|insecticide|bleach|acid|tablets)", r"(pesticide|insecticide|keede? maarne ki dawa).{0,20}(pi|pee|kha|drank|swallow)",
                  r"zeher", r"ज़?ज़हर|जहर", r"कीड़े मारने की दवा.{0,20}(पी|खा)"],
    "severe_head_injury": [r"head injury.{0,30}(bleed|unconscious|vomit)", r"sar (pe|par|mein) (gehri )?chot.{0,30}(khoon|behosh|ulti)", r"सिर (पर|में) (गहरी )?चोट"],
    "anaphylaxis": [r"(face|throat|tongue) (is )?swell", r"gala sooj", r"गला सूज"],
}

# an infant's AGE, not a duration such as "2 days": newborn, "6 weeks old", "2 month old", "do mahine ka", "दो महीने की".
# Devanagari number words and digits carry no \b: vowel signs are not word characters, so \b fails next to them.
HI_WEEKS = r"एक|दो|तीन|चार|पांच|पाँच|छह|छः|सात|आठ|[०-९]{1,2}"
HI_MONTHS = r"एक|दो|[०-२]"
INFANT = re.compile(r"new ?born|infant|नवजात"
                    r"|(\b(\d|1[0-2]|one|two|three|ek|do|teen)|" + HI_WEEKS + r")[\s-]*(weeks?|hafte|हफ़्ते|हफ्ते)[\s-]*(old|ka|ki|ke|का|की|के)"
                    r"|((\b|^)([0-2]|one|two|ek|do)|" + HI_MONTHS + r")[\s-]*(months?|mahine|maheene|महीने|महीना)[\s-]*(old|ka|ki|ke|का|की|के)", re.I)
FEVER = re.compile(r"fever|temperature|bukh?aa?r|बुखार|तापमान|\btemp\b", re.I)

NEG_BEFORE = re.compile(r"(\b(no|not|without|denies|never|na)\b|बिना)[\w\s]{0,12}$", re.I)
# "nahi" / "नहीं" right after the phrase is a denial ("seene mein dard nahi", "सीने में दर्द नहीं है"). The end check is a
# lookahead, not \b, because "नहीं" ends in a nasal sign the regex engine does not count as a word character.
NEG_AFTER = re.compile(r"^\W{0,3}(hai |है |ho raha |हो रहा )?\W{0,3}(nahi|nahin|nhi|नहीं|नही)(?![a-zऀ-ॿ])", re.I)

_COMPILED = {k: [re.compile(p, re.I) for p in v] for k, v in {**RULES, **EXTRA}.items()}


def _hit(text: str, pat: re.Pattern) -> bool:
    for m in pat.finditer(text):
        before, after = text[max(0, m.start() - 20): m.start()], text[m.end(): m.end() + 14]
        if NEG_BEFORE.search(before) or NEG_AFTER.search(after):
            continue  # "no chest pain", "seene mein dard nahi"
        return True
    return False


def detect(*texts: str) -> tuple[list[str], list[str]]:
    """-> (organiser-vocabulary flags, extra emergency reasons). Pass raw words and/or English symptom phrases."""
    text = " ".join(t for t in texts if t).lower()
    found = [k for k, pats in _COMPILED.items() if any(_hit(text, p) for p in pats)]
    if INFANT.search(text) and FEVER.search(text) and not NEG_AFTER.search(text[FEVER.search(text).end():][:14]):
        found.append("high_fever_infant")
    flags = [f for f in RULES if f in found] + (["high_fever_infant"] if "high_fever_infant" in found else [])
    extra = [f for f in EXTRA if f in found]
    return flags, extra
