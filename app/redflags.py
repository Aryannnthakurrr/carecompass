"""Deterministic red-flag rules (English / romanised Hinglish / Devanagari). Run on the raw words before any
model, and again on the structurer's English symptoms. Any hit routes to emergency_112."""
import re

# flag -> patterns. Flags use the organiser vocabulary; EXTRA are emergencies the vocabulary has no word for.
RULES = {
    "chest_pain": [
        r"chest (pain|tightness|pressure)", r"chest (feels |is |feeling )?tight", r"tight(ness)? (in (the|my|his|her) )?chest", r"pain in (the |my |his |her )?chest",
        r"chest (discomfort|heaviness|ache)", r"(ache|discomfort|heaviness) in (the )?(cent(er|re) of )?(the |my |his |her )?chest",
        r"heart attack", r"see+ne? (me|mein|main|mai)? ?(dard|dabav|jakdan)", r"chh?aa?ti (me|mein|main)? ?dard", r"dil ka daura",
        r"सीने (में )?(दर्द|दबाव|जकड़न)", r"छाती (में )?दर्द", r"दिल का दौरा",
    ],
    "breathlessness": [
        r"(can'?t|cannot|unable to|not able to) breathe", r"(difficulty|trouble|problem) (in )?breathing", r"short(ness)? of breath",
        r"breathless", r"gasping", r"blue lips", r"lips (are )?(turning )?blue",
        r"(difficult|laboured|labored|tight|rapid and shallow) breathing", r"breathing (is )?(difficult|laboured|labored|tight)", r"dyspn",
        r"saa?n?s (nahi|nhi|nahin) (aa|le pa)", r"saa?n?s lene (me|mein|main) (bahut |kaafi )?(dikkat|takleef|taklif|pareshani|mushkil)",
        r"saa?n?s phool", r"ho+n?th.{0,12}nee?l", r"होंठ.{0,12}नील",
        r"सा(ं|ँ)स (नहीं|नही) (आ|ले)", r"सा(ं|ँ)स लेने में (बहुत |काफ़ी |काफी )?(दिक्कत|तकलीफ़?|परेशानी|मुश्किल)", r"सा(ं|ँ)स फूल", r"होंठ नीले",
    ],
    "unconsciousness": [
        r"unconscious", r"unresponsive", r"not responding", r"collapsed", r"passed out", r"fainted", r"won'?t wake",
        r"(poor|reduced|decreased|barely) responsive", r"(hard|difficult|unable) to (wake|rouse|arouse)",
        r"altered (mental status|consciousness|sensorium)", r"loss of consciousness", r"lost consciousness",
        r"not recogni[sz]ing", r"(very|extremely|unusually) drowsy", r"keeps? falling asleep",
        r"pa?he?cha+n (nahi|nhi) (rahe|raha|rahi|pa)", r"baa?r baa?r so (ja|rahe|raha|rahi)", r"jaag (nahi|nhi) (rahe|raha|rahi|pa)",
        r"पहचान (नहीं|नही) (रहे|रहा|रही|पा)", r"बार[- ]बार सो (जा|रहे|रहा|रही)", r"जाग (नहीं|नही) (रहे|रहा|रही|पा)",
        r"behosh", r"response nahi de", r"hosh (nahi|nhi|mein nahi)", r"jawab nahi de",
        r"बेहोश", r"होश (नहीं|में नहीं)", r"जवाब नहीं दे", r"गिर (गए|गई|गया).{0,20}(बोल|जवाब|होश)",
    ],
    "severe_bleeding": [
        r"(heavy|severe|lot of|lots of|uncontrolled|profuse) bleeding", r"bleeding (that )?(won'?t|will not|doesn'?t|does not|is not|isn'?t) stop",
        r"bleeding (is )?(not|never) stopp", r"bleeding (ruk|band) (nahi|nhi)", r"khoo?n (ruk|band) (nahi|nhi)",
        r"(khoo?n|bleeding).{0,25}(nahi|nhi|na) (ruk|band ho)", r"(khoo?n|bleeding).{0,25}(ruk|band) (nahi|nhi|na) (raha|rahi|ho)",
        r"(dabane|press).{0,30}(bhi|even).{0,20}(nahi|nhi|not|won'?t|doesn'?t) (ruk|stop)",
        r"(won'?t|will not|doesn'?t|does not|isn'?t|not) stop(ping)? bleeding",
        r"खून.{0,25}(नहीं|नही|ना) रुक", r"खून.{0,25}रुक (नहीं|नही) (रहा|रही)", r"bahut khoo?n", r"vomiting blood", r"khoo?n ki ulti",
        r"pregnan\w*.{0,30}bleed", r"bleed\w*.{0,30}pregnan",
        r"(heavy|severe|profuse|excessive) (vaginal|rectal|nose|menstrual) bleeding", r"(continuous|continuing|non-?stop) bleeding",
        r"(passing|passed) (large |big )?(blood )?clots?", r"bleeding.{0,20}clots",
        r"खून (नहीं|नही) रुक", r"बहुत खून", r"खून की उल्टी", r"गर्भ.{0,30}खून",
    ],
    "stroke_symptoms": [
        r"face (is )?droop", r"facial droop", r"slurred speech", r"one side (of (the|his|her) body )?(is )?(weak|numb)",
        r"(can'?t|cannot|unable to) move (his |her |my )?(right |left )?(arm|leg|side)", r"\bstroke\b", r"paralys",
        r"(droop|deviat|crooked|twist)\w* (corner of )?(the |his |her |my )?(mouth|face|lip)",
        r"(mouth|face|lip) (is )?(droop|deviat|crooked|twist)",
        r"(numbness|weakness|tingling) (in|of) (the |his |her |my )?(left|right) (hand|arm|leg|side|face)",
        r"(left|right)[- ]?(sided )?(hand|arm|leg|side|face) (numbness|weakness)", r"(left|right)[- ]sided (numbness|weakness)",
        r"(can'?t|cannot|unable to|inability to) close (one|his|her|the|my) eye", r"sudden(ly)? (difficulty|trouble) (speaking|talking)",
        r"speech (is )?(slurred|unclear)", r"sudden(ly)? (confus|disorient)", r"sudden (loss of vision|vision loss|blindness)",
        r"(ek|one) (taraf|side).{0,15}(sunn|kamzor|numb|weak)", r"एक तरफ.{0,15}(सुन्न|कमज़ोर|कमजोर)",
        r"mu+n?h tedha", r"chehra.{0,20}tedha", r"bolne (me|mein|main) (dikkat|takleef|taklif)", r"haath.?pair sunn", r"lakwa",
        r"मु(ं|ँ)ह टेढ़ा", r"चेहरा.{0,20}टेढ़ा", r"बोलने में (दिक्कत|तकलीफ़?)", r"हाथ.?पैर सुन्न", r"लकवा",
    ],
    "seizure": [
        r"seizure", r"convuls", r"(?<!coughing )(?<!cough )\bfits?\b", r"jhatk?e", r"daura (pad|padh|aa)", r"mirgi",
        r"(limb|body|arm|leg)s? jerking", r"jerking (of|movements)",
        r"झटके", r"दौरा (पड़|आ)", r"मिर्गी",
    ],
    "suicidal_ideation": [
        r"suicid", r"kill (my|him|her)self", r"end (my|his|her) life", r"(don'?t|do not) want to live", r"no reason to live",
        r"jee+ne ka (mann?|man) nahi", r"jeena nahi chaht", r"marna chaht", r"khudkushi", r"aatmahatya",
        r"\bwant(s|ed)? to die\b", r"wish (i|he|she) (was|were) dead", r"better off dead", r"\b(hurt|harm)(ing)? (my|him|her)self",
        r"mar jaa?n(a|e|aa) chaht", r"marne ka (mann?|man)", r"khud ko (khatam|nuksaan)",
        r"जीने का मन नहीं", r"जीना नहीं चाहत", r"मरना चाहत", r"मर जाना चाहत", r"मरने का मन", r"खुद को (खत्म|ख़त्म|नुकसान)", r"खुदकुशी", r"आत्महत्या",
    ],
    "severe_dehydration": [
        r"severe(ly)? dehydrat", r"(no|not passed|hasn'?t passed) urine", r"sunken eyes",
        r"peshab (nahi|nhi) (aaya|hua|kiya)", r"पेशाब (नहीं|नही) (आया|हुआ|किया)",
    ],
}
EXTRA = {
    "poisoning": [r"(?<!food )poison", r"swallowed .{0,20}(pesticide|insecticide|bleach|acid|tablets)", r"(pesticide|insecticide|keede? maarne ki dawa).{0,20}(pi|pee|kha|drank|swallow)",
                  r"zeher", r"ज़?ज़हर|जहर", r"कीड़े मारने की दवा.{0,20}(पी|खा)"],
    "severe_head_injury": [r"head injury.{0,80}(bleed|unconscious|vomit|drows|confus)", r"sar (pe|par|mein) (gehri )?chot.{0,30}(khoon|behosh|ulti)",
                           r"सिर (पर|में) (गहरी )?चोट.{0,60}(खून|बेहोश|उल्टी|उलटी|होश|उलझन)"],
    "anaphylaxis": [r"(face|throat|tongue) (is )?swell", r"gala sooj", r"गला सूज"],
    # events rather than symptoms (from a teammate's edge-case log): the injury is implied by what happened
    "mass_casualty": [r"(blast|explosion|explode|bomb|building (collapse|fell|on fire)|collapsed building|stampede|fire in (the|our|a) building)"
                      r".{0,80}(injur|hurt|trapped|bleed|dying|dead|unconscious|need (immediate )?(help|support))",
                      r"(injur|trapped|bleeding|dying)\w*.{0,60}(blast|explosion|bomb|collapse|stampede|fire)",
                      r"(dhamaka|visphot|imarat gir|building gir|bhagdad|aag lag).{0,60}(ghayal|zakhmi|fans|phas|khoon|mar gaye|behosh)",
                      r"(धमाका|विस्फोट|इमारत गिर|भगदड़|आग लग).{0,60}(घायल|ज़ख़्मी|ज़ख्मी|फँस|फंस|खून|मर गए|बेहोश)"],
    "drowning": [r"drown", r"doob (gaya|gayi|gaye|raha|rahi)", r"pani mein doob", r"डूब (गया|गई|गए|रहा|रही)"],
    "electric_shock": [r"electrocut", r"electric shock", r"(current|bijli|करंट|बिजली)( ka)?( jhatka| झटका)? (lag|लग)"],
    "snake_bite": [r"snake ?bite", r"bitten by a snake", r"(saa?n?p|snake) ne kaa?t", r"साँप ने काट", r"सांप ने काट"],
    "choking": [r"chok(ing|ed) on", r"(is|was) choking", r"can'?t (swallow|breathe).{0,20}(stuck|coin|food)",
                r"gale mein .{0,15}(atak|phas|fans)", r"गले में .{0,15}(अटक|फँस|फंस)"],
    "severe_burns": [r"(severe|bad|badly|major|large) burns?\b", r"burn(s|ed|t)? (all over|on (the |his |her )?(face|chest|whole body))",
                     r"(poora|pura|bahut zyada) jal (gaya|gayi|gaye)", r"(पूरा|बुरी तरह) जल (गया|गई|गए)"],
}

# an infant's AGE, not a duration such as "2 days": newborn, "6 weeks old", "2 month old", "do mahine ka", "दो महीने की".
# Devanagari number words and digits carry no \b: vowel signs are not word characters, so \b fails next to them.
HI_WEEKS = r"एक|दो|तीन|चार|पांच|पाँच|छह|छः|सात|आठ|[०-९]{1,2}"
HI_MONTHS = r"एक|दो|[०-२]"
INFANT = re.compile(r"new ?born|infant|नवजात"
                    r"|(\b(\d|1[0-2]|one|two|three|ek|do|teen)|" + HI_WEEKS + r")[\s-]*(weeks?|hafte|हफ़्ते|हफ्ते)[\s-]*(old|ka|ki|ke|का|की|के)"
                    r"|((\b|^)([0-2]|one|two|ek|do)|" + HI_MONTHS + r")[\s-]*(months?|mahine|maheene|महीने|महीना)[\s-]*(old|ka|ki|ke|का|की|के)", re.I)
FEVER = re.compile(r"fever|temperature|bukh?aa?r|बुखार|तापमान|\btemp\b", re.I)

# A denial scopes over its clause, so lists are covered: "not complaining of chest pain, shortness of breath, or
# palpitations", "न साँस लेने में दिक्कत है, न छाती में दर्द". A clause ends at sentence punctuation or a contrast word,
# so "no fever but chest pain" still fires. Erring towards "denied" here is safe: the same rules run again on the
# structurer's symptoms_present, which never holds a denied symptom, so a real emergency is still caught there.
CLAUSE_END = re.compile(r"[.!?।;\n]|\b(but|however|except|although|lekin|magar|bas)\b|लेकिन|मगर|बस", re.I)
# "n't" needs its apostrophe: a bare "nt" would match the end of "patient", "urgent", "accident", "pregnant"
NEG_TRIGGER = re.compile(r"\b(no|not|without|denies|denied|never|none|nor|neither|na|nearly|almost)\b|n['’]t\b"
                         r"|\b(dont|doesnt|didnt|isnt|wasnt|hasnt|havent|cant|wont)\b|बिना"
                         r"|(?<![ऀ-ॿ])(न|ना)(?![ऀ-ॿ])", re.I)
# "nahi" / "नहीं" after the phrase denies it ("seene mein dard nahi", "उल्टी, बेहोशी, या सीने में दर्द नहीं है") unless a verb
# affirms it first ("सीने में दर्द है और खांसी नहीं"). Lookaheads, not \b: "नहीं" ends in a sign \b does not see as a letter.
NEG_AFTER = re.compile(r"(nahi|nahin|nhi|नहीं|नही)(?![a-zऀ-ॿ])", re.I)
AFFIRM = re.compile(r"(?<![a-zऀ-ॿ])(hai|hain|ho rah|lag rah|aa rah|है|हैं|हो रह|लग रह|आ रह)", re.I)
NEG_AFTER_NEAR = re.compile(r"^\W{0,3}(hai |है |ho raha |हो रहा )?\W{0,3}(nahi|nahin|nhi|नहीं|नही)(?![a-zऀ-ॿ])", re.I)
# a past event, not today's emergency: "bedridden after his stroke", "since the stroke", "history of stroke"
HISTORY = re.compile(r"\b(after|since|due to|history of|post|old|previous|past|prior|recovering from)\W+(\w+\W+){0,2}$", re.I)
HISTORY_FLAGS = {"stroke_symptoms", "severe_head_injury"}

_COMPILED = {k: [re.compile(p, re.I) for p in v] for k, v in {**RULES, **EXTRA}.items()}


def _denied(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 60): start]
    clause = CLAUSE_END.split(before)[-1] if CLAUSE_END.search(before) else before
    if NEG_TRIGGER.search(clause):
        return True
    after = text[end: end + 60]
    if NEG_AFTER_NEAR.search(after):
        return True
    m = CLAUSE_END.search(after)
    after = after[: m.start()] if m else after
    neg = NEG_AFTER.search(after)
    return bool(neg) and not AFFIRM.search(after[: neg.start()])


def _hit(text: str, pat: re.Pattern, flag: str) -> bool:
    for m in pat.finditer(text):
        if _denied(text, m.start(), m.end()):
            continue  # "no chest pain", "seene mein dard nahi", "न छाती में दर्द"
        if flag in HISTORY_FLAGS and HISTORY.search(text[max(0, m.start() - 40): m.start()]):
            continue
        return True
    return False


def detect(*texts: str) -> tuple[list[str], list[str]]:
    """-> (organiser-vocabulary flags, extra emergency reasons). Pass raw words and/or English symptom phrases.
    Texts are joined with "; ", which ends a clause: a denial in one symptom phrase ("not eating") cannot reach the next,
    while two-part rules ("head injury ... vomiting") still see both."""
    text = "; ".join(t.lower() for t in texts if t)
    found = [k for k, pats in _COMPILED.items() if any(_hit(text, p, k) for p in pats)]
    if INFANT.search(text) and FEVER.search(text) and not NEG_AFTER_NEAR.search(text[FEVER.search(text).end():][:14]):
        found.append("high_fever_infant")
    flags = [f for f in RULES if f in found] + (["high_fever_infant"] if "high_fever_infant" in found else [])
    extra = [f for f in EXTRA if f in found]
    return flags, extra
