"""POST /triage, in layers. Each layer can only make the system safer or ask; none can be talked out of an emergency.

  1. Red-flag rules on the raw words (instant, deterministic).
  2. Intake analyst (Qwen): English facts + is this a usable health request + what the user wants + which alarming
     words were not meant literally. A rule hit stands unless the analyst says the words were figurative or a joke.
  3. Red-flag rules again on the English symptoms.
  4. Gate: off-topic / nonsense / abuse -> a fixed redirect to the health problem; mis-heard voice -> "did you say ...?";
     mixed-up or contradictory -> one clarifying question. Nothing here is judged by a medical model.
  5. Classifier (dataset: organiser taxonomy; smart: seriousness + specialty) with the questions already asked, in
     parallel with MedGemma's emergency screener. The screener's "yes" first asks its own confirming question with a
     pre-written call-112 line; if the danger is confirmed or persists after that, it is an emergency.
  6. Enough-facts guard: a symptom complaint without who/age or since-when gets a question, not a guess.
  7. Matching + explanation. Every shown line is scrubbed of disease and medicine names.
"""
import contextvars
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

from app import explain, intake, lexicon, matching, protocols, redflags, screen, smart, smart_match, triage
from app import emergency_text
from app.llm import CFG
from app.schemas import CaseFacts, Emergency, TriageRequest, TriageResponse

log = logging.getLogger("carecompass.pipeline")
URGENCY_FOR_CARE = CFG["labels"]["urgency_for_care"]
MAX_FOLLOW_UPS = CFG["intake"]["max_follow_ups"]
SMART_URGENCY = {"emergency": "emergency", "urgent": "same_day", "soon": "scheduled", "routine": "scheduled"}
SMART_CARE_LABEL = {"hospital_visit": "a hospital visit", "clinic_visit": "a doctor's visit", "specialist_visit": "a specialist visit",
                    "diagnostic_test": "a test or scan", "lab_test_home": "a home sample collection", "home_nursing": "nursing at home",
                    "paramedic_home_visit": "a home check-up", "teleconsultation": "a video consultation"}
GATE_STOP = {"off_topic", "nonsense", "abusive"}
HOME_NURSING_TAGS = {"injection", "iv_drip", "wound_dressing", "nebulization", "catheter_care"}
FORCED = "Decided with the information given (further questions were not answered); the care team will check the rest."
SUPPRESSIBLE = {"chest_pain", "breathlessness"}  # red flags that are also everyday figures of speech
MAX_REDIRECTS = 3  # junk / abuse / off-topic messages steered back this many times; the next one ends the conversation
EXTRA_ASKS_AFTER_BASICS = 2  # once who/age and since-when are known, the classifier's own questions stop at this many asked
SAFETY_MARK = "112"  # every safety-check question ends with the pre-written call-112 line (explain.SAFETY_LINE)
_POOL = ThreadPoolExecutor(max_workers=8)
_ASKS_MEDS = contextvars.ContextVar("asks_meds", default=False)  # set per request by the intake analyst


FLAG_WORDS = [  # a model-made emergency whose words matched no rule still names a red flag (never an empty list)
    ("suicidal_ideation", r"suicid|self-harm|end (his|her|my) life|want to die|not want to live"),
    ("seizure", r"seiz|convuls|\bfits?\b|jerking"),
    ("severe_bleeding", r"bleed|blood loss|haemorrh|hemorrh"),
    ("stroke_symptoms", r"stroke|droop|one-sided|slurred|numbness"),
    ("unconsciousness", r"unrespons|unconscious|collaps|faint|drows|sleepy|hard to wake|confus|recogni|letharg|altered"),
    ("breathlessness", r"breath|dyspn|blue lips|gasp"),
    ("chest_pain", r"chest"),
    ("severe_dehydration", r"dehydrat|no urine|not passing urine|not drinking|keep water down"),
]


def _infer_flags(*texts: str) -> list[str]:
    blob = " ".join(texts).lower()
    return [flag for flag, pat in FLAG_WORDS if re.search(pat, blob)][:3]


def _emergency(flags: list[str], extra: list[str], lang: str, mode: str, why: str | None = None, texts=()) -> TriageResponse:
    flags = flags or _infer_flags(*texts, why or "")
    e = emergency_text.card(flags + extra, lang)
    named = ", ".join(flags + extra) or "red-flag signs"
    return TriageResponse(
        care_type="emergency_112", urgency="emergency", red_flags=flags, mode=mode,
        reasoning=why or f"Red-flag symptoms detected ({named}); routed to emergency services, not home care.",
        language=lang, explanation=e["explanation"], seriousness="emergency" if mode == "smart" else None,
        emergency=Emergency(title=e["title"], call=e["call"], steps=e["steps"]),
    )


def _ask(q: str, lang: str, mode: str, kind: str, reasoning: str, safety_note: str | None = None, **extra) -> TriageResponse:
    return TriageResponse(follow_up_question=q, language=lang, mode=mode, follow_up_kind=kind, reasoning=reasoning,
                          safety_note=safety_note, **extra)


def run(req: TriageRequest) -> TriageResponse:
    _ASKS_MEDS.set(False)
    r = _run(req)
    if r.care_type == "emergency_112":
        return r  # pre-written text; red-flag names are not diagnoses
    if _ASKS_MEDS.get():  # say plainly why the medicine or diagnosis question is not answered
        line = explain.NO_MEDS[r.language]
        r = r.model_copy(update={"follow_up_question": r.follow_up_question and f"{line} {r.follow_up_question}",
                                 "explanation": r.explanation and f"{line} {r.explanation}"})
    plain = "Routed from the symptoms you described."
    return r.model_copy(update={
        "reasoning": explain.scrub(r.reasoning, plain),
        "explanation": explain.scrub(r.explanation, r.explanation and plain),
        "match_summary": explain.scrub(r.match_summary),
        "follow_up_question": explain.scrub(r.follow_up_question, r.follow_up_question and explain.FALLBACK_ASK[r.language]),
    })


def _context(facts: CaseFacts) -> str:
    """What the reply writer may know: the extracted facts, never the user's raw words."""
    bits = [facts.user_wants, facts.patient, ", ".join(facts.symptoms_present), facts.duration, ", ".join(facts.services_requested)]
    return "; ".join(b for b in bits if b) or "nothing specific yet"


def _medical(facts: CaseFacts) -> bool:
    return bool(facts.symptoms_present or facts.services_requested or facts.vitals_reported or facts.existing_conditions
                or facts.setting_requested not in (None, "unspecified"))


BASICS_ASKS = 2  # questions the enough-facts guard may spend; after that a user who won't say still gets care
ASK_SEVERITY = CFG["intake"].get("ask_severity", True)  # also ask how bad / how high (off for an organiser scoring run)
ADULT_RELATION = re.compile(r"father|mother|papa|mummy|\bdad\b|\bmom\b|husband|wife|pati|patni|grand|dada|dadi|nana|nani|uncle|"
                            r"aunt|in-law|पापा|मम्मी|पिता|माँ|माता|पति|पत्नी|दादा|दादी|नाना|नानी|ससुर|सास", re.I)


def _age_stated(facts: CaseFacts) -> bool:
    """A fever's age counts as known only when said: a number, an age group the user gave, or an adult relation.
    'self' alone does not count (the mentor's demo: 'i am having fever' was routed with no age)."""
    p = facts.patient or ""
    return bool(facts.special_population or re.search(r"\d", p) or ADULT_RELATION.search(p))


def _basic_gaps(facts: CaseFacts, severity: bool = True) -> list[str]:
    """What a symptom complaint lacks to be routed safely: who (for fever, the age group) and since when.
    Explicit requests (a named service or kind of visit) need neither: the user already knows what they want."""
    if not facts.symptoms_present or facts.services_requested or facts.setting_requested not in (None, "unspecified"):
        return []
    missing = []
    fever = any("fever" in s.lower() or "temperature" in s.lower() for s in facts.symptoms_present + facts.vitals_reported)
    if not (facts.patient or facts.age_group):
        missing.append("who the patient is and how old")
    elif fever and not _age_stated(facts):  # "i am having fever": the typist could be a teen or a parent typing for a baby
        missing.append("how old the patient is")
    if not facts.duration:
        missing.append("since when")
    if severity and ASK_SEVERITY and not (facts.severity or facts.vitals_reported):
        missing.append(_severity_ask(facts, fever))
    return missing[:2]  # at most two facts per question; the third comes in the next one


def _severity_ask(facts: CaseFacts, fever: bool) -> str:
    """How bad: the fact that most often moves the care type once who and how long are known. Complaint-specific from the
    protocol cards (bleeding: does it stop with pressure; accident: what happened, can they move it, head hit)."""
    if key := protocols.key_ask(facts):
        return key
    if fever:
        return "how high the temperature is"
    if any(s.lower().strip() in ("pain", "ache", "dard", "body pain", "hurting", "swelling") for s in facts.symptoms_present):
        return "where exactly it hurts and how bad it is (mild, moderate or severe)"  # only "pain" said: where is unknown
    return "how bad it is (mild, moderate or severe)"


def _missing_basics(facts: CaseFacts, asked: int) -> list[str]:
    """The gaps worth asking about now: none once the user wants no more questions or the guard's budget is spent,
    except a fever's unknown age, which is asked until the overall cap (an infant's fever is an emergency)."""
    if facts.no_more_questions:
        return []
    gaps = _basic_gaps(facts)
    if asked < BASICS_ASKS:
        return gaps
    if asked >= MAX_FOLLOW_UPS:
        return []
    # asking the age again: say why, so it doesn't read as the same question ignored twice
    return ["how old the patient is (asked once already: add in a few words that fever in a small baby needs very different "
            "care than in an adult)"] if any("old" in g for g in gaps) else []


def _kind(t) -> str:
    """What an assistant turn was: the client echoes follow_up_kind back; for older clients it is inferred from our texts."""
    return t.kind or explain.kind_of(t.content)


def _count(history, kinds: set) -> int:
    return sum(t.role == "assistant" and _kind(t) in kinds for t in history)


def _stem(w: str) -> str:
    return w[:5]


def _grounded(signs: list[str], facts: CaseFacts) -> list[str]:
    """Danger signs that match something the user actually reported (the screener sometimes reasons about what MIGHT
    be happening; only reported findings count)."""
    reported = " ".join(facts.symptoms_present + facts.vitals_reported + facts.existing_conditions + facts.special_population
                        + [facts.patient or "", facts.onset or "", facts.severity or ""]).lower()
    words = {_stem(w) for w in re.findall(r"[a-z]{4,}", reported)} - _STOP
    return [s for s in signs if {_stem(w) for w in re.findall(r"[a-z]{4,}", s.lower())} & words]


_STOP = {_stem(w) for w in ("with", "that", "this", "patient", "very", "since", "years", "from", "have", "been", "some", "more")}


def _may_ask(facts: CaseFacts, asked: int) -> bool:
    """The classifier's own questions: never past the cap or after "no more questions", and once the basics are known,
    not past EXTRA_ASKS_AFTER_BASICS asked in total (protocol checklists otherwise turn into an interrogation)."""
    if facts.no_more_questions or asked >= MAX_FOLLOW_UPS:
        return False
    return bool(_basic_gaps(facts)) or asked < EXTRA_ASKS_AFTER_BASICS


def _norm(q: str) -> str:
    return re.sub(r"[^\w]+", " ", q.lower()).strip()


def _fresh(q: str, asked_qs: list[str]) -> str | None:
    """The question, unless it repeats one already asked (then the user isn't going to answer it: decide instead)."""
    return None if _norm(q) in {_norm(a) for a in asked_qs} else q


def _screened(scr, facts: CaseFacts, history, asked: int, lang: str, mode: str):
    """MedGemma's emergency screen -> an emergency, a safety-check question, or None (routing unchanged).
    It never escalates on its own first word: it asks about the reported danger sign once, and escalates only if the
    danger is still reported after that question."""
    s = scr.result() if scr else None
    if s is None or not s.emergency:
        return None
    signs = _grounded(s.danger_signs, facts)
    if not signs:
        log.info("screen ignored: no reported finding behind %s", s.danger_signs)
        return None
    checked = any(t.role == "assistant" and SAFETY_MARK in t.content for t in history)
    if checked:
        mapped, more = redflags.detect(*signs, *facts.symptoms_present)
        log.info("screen emergency confirmed after the safety check: %s", signs)
        return _emergency(mapped, more, lang, mode, why=f"MedGemma emergency screen: {', '.join(signs)}. {s.reasoning}", texts=signs)
    q, line = explain.safety_check(s.check_question, signs, lang)
    log.info("screen safety check: %s", signs)
    return _ask(q, lang, mode, "safety_check", f"Possible danger signs ({', '.join(signs)}); checking before deciding.", line)


def _run(req: TriageRequest) -> TriageResponse:
    t0 = time.perf_counter()
    history = req.conversation_history
    user_turns = [t.content for t in history if t.role == "user"] + [req.user_input]
    asked_qs = intake.asked_questions(history)  # every question, so none is repeated
    asked = _count(history, {"medical", "safety_check"})  # only these spend the medical question budget
    stalls = _count(history, {"redirect", "clarify", "misheard", "fallback"})  # turns that made no progress
    lang = req.language if req.language != "auto" else intake.guess_language(req.user_input)
    voice = req.input_mode == "voice"

    # 1. rules on the raw words
    raw_flags, raw_extra = redflags.detect(*user_turns)

    # 2. the intake analyst
    try:
        facts = intake.structure(history, req.user_input, voice)
    except Exception:
        log.exception("intake failed")
        if raw_flags or raw_extra:  # no model to second-guess the rules: safety first
            return _emergency(raw_flags, raw_extra, lang, req.mode)
        return _ask(explain.INTAKE_FAIL[lang], lang, req.mode, "fallback", "The message could not be processed; asking to rephrase.",
                    explain.SAFETY_LINE[lang])
    _ASKS_MEDS.set(facts.asks_medicine_or_diagnosis)
    if req.language == "auto" and lang != "hi":  # mostly Devanagari is unambiguous; for Latin script trust the model
        lang = "en" if facts.detected_language == "en" else "hinglish"

    # 3. emergencies: rules on the English symptoms, then raw-word hits the analyst did not explain away
    flags, extra = redflags.detect(*facts.symptoms_present)
    if flags or extra:
        return _emergency(flags, extra, lang, req.mode)
    safety_note = None
    if raw_flags or raw_extra:
        hit = raw_flags + raw_extra
        denied = sum(redflags.detect(*facts.symptoms_denied), [])
        retracted = [f for f in hit if f in denied]  # "chest pain" ... "no wait, no chest pain": confirm once, don't just drop it
        checked = any(t.role == "assistant" and SAFETY_MARK in t.content for t in history)
        if retracted and not checked:
            signs = [f.replace("_", " ") for f in retracted]
            q, line = explain.safety_check(f"Just to be safe: is there any {' or '.join(signs)} right now?", signs, lang)
            return _ask(q, lang, req.mode, "safety_check", f"Red flag mentioned then denied ({', '.join(signs)}); confirming.", line)
        # only common figures of speech ("heart attack aa jayega", "can't breathe from laughing") may be explained away, and
        # only when the analyst names them; self-harm, collapse, fits, stroke, bleeding, poisoning never are
        figurative = facts.not_literal and set(hit) <= SUPPRESSIBLE
        if retracted or figurative:
            safety_note = explain.SAFETY_LINE[lang]  # not sounding the alarm for a figure of speech, but saying how to get help
            log.info("raw rule hit %s judged not literal or denied: %s", hit, facts.not_literal or retracted)
        else:
            return _emergency(raw_flags, raw_extra, lang, req.mode)

    # 4. the gate: nothing here reaches a medical model
    log.info("intake check=%s notes=%s wants=%s", facts.message_check, facts.check_notes, facts.user_wants)
    medical = _medical(facts)
    stalled = stalls >= MAX_REDIRECTS
    if facts.not_literal and safety_note is None:
        safety_note = explain.SAFETY_LINE[lang]  # "main toh mar jaunga lol": no alarm, but the way to get help is shown
    if not medical and (stalled or (facts.message_check in GATE_STOP and _count(history, {"redirect"}) >= MAX_REDIRECTS)):
        # still nothing medical after repeated steering: close politely, keep the 112 path
        log.info("conversation ended after %d non-progress turns", stalls)
        return TriageResponse(language=lang, mode=req.mode, conversation_ended=True, explanation=explain.END[lang],
                              reasoning=f"No health concern after {stalls} turns of steering; conversation closed.")
    if facts.message_check in GATE_STOP and not medical:
        return _ask(explain.redirect(lang, facts.message_check == "abusive", _count(history, {"redirect"})), lang, req.mode, "redirect",
                    "No health concern described; steering back to the health problem.", safety_note)
    if facts.message_check == "unclear" and (voice or not medical) and not stalled:
        q = explain.misheard(req.user_input, lang) if voice else explain.clarify(facts.check_notes, _context(facts), lang)
        if _fresh(q, asked_qs):
            return _ask(q, lang, req.mode, "misheard" if voice else "clarify", "The message was unclear; confirming before going further.")
    # one clarifying question per conversation: if the confusion persists after it, route with the best reading of the facts
    if facts.message_check in ("mixed_up", "contradictory") and not _count(history, {"clarify"}) and not stalled:
        q = explain.clarify(facts.check_notes, _context(facts), lang)
        if _fresh(q, asked_qs):
            return _ask(q, lang, req.mode, "clarify", f"Clarifying first: {'; '.join(facts.check_notes) or facts.message_check}.", safety_note)
    if not medical:
        q = (explain.follow_up(["what the main problem is"], _context(facts), lang) if facts.patient
             else explain.FALLBACK_ASK[lang] if explain.FALLBACK_ASK[lang] not in asked_qs else explain.REDIRECT_AGAIN[lang])
        return _ask(q, lang, req.mode, "fallback", "No health concern described yet; asking what it is.", safety_note)

    # 5. classifier + MedGemma's emergency screen, in parallel. The screen waits until who/age and since-when are known
    #    (with them missing it tends to assume an infant), unless the user won't say or the question budget is spent:
    #    then it runs anyway, told the age is not stated, so the least forthcoming users still get a safety screen.
    basics_known = not _basic_gaps(facts, severity=False) or facts.no_more_questions or asked >= BASICS_ASKS
    scr = _POOL.submit(screen.screen, facts) if basics_known and screen.wanted(facts) else None
    if req.mode == "smart":
        return _run_smart(req, facts, user_turns, asked_qs, asked, lang, t0, scr, safety_note)
    return _run_dataset(req, facts, user_turns, asked_qs, asked, lang, t0, scr, safety_note)


def _run_dataset(req, facts, user_turns, asked_qs, asked, lang, t0, scr, safety_note) -> TriageResponse:
    decision, votes = triage.classify(facts, asked_qs)
    log.info("votes medgemma=%s qwen=%s", votes["medgemma"] and votes["medgemma"].care_type, votes["qwen"] and votes["qwen"].care_type)
    if decision is not None and decision.care_type == "emergency_112":
        mapped, more = redflags.detect(*decision.red_flags, *facts.symptoms_present)
        return _emergency(mapped, more, lang, "dataset", why=decision.reasoning, texts=decision.red_flags + facts.symptoms_present)
    ctx = _context(facts)
    if missing := _missing_basics(facts, asked):
        return _ask(explain.follow_up(missing, ctx, lang, protocols.guide(facts)), lang, "dataset", "medical",
                    f"Need {' and '.join(missing)} before routing safely.", safety_note)
    if (out := _screened(scr, facts, req.conversation_history, asked, lang, "dataset")) is not None:
        return out
    if decision is None:  # both classifiers down: someone should see the patient
        care, reasoning = "paramedic_home_visit", "Classifier unavailable; defaulting to an in-person Medic assessment."
    elif (decision.status == "need_more_info" and _may_ask(facts, asked)
          and (q := _fresh(explain.follow_up(decision.missing_info or ["main concern"], ctx, lang, protocols.guide(facts)), asked_qs))):
        return _ask(q, lang, "dataset", "medical", decision.reasoning, safety_note)
    elif decision.status == "need_more_info":  # nothing new to ask: decide with what we have
        care = decision.care_type
        reasoning = FORCED
    else:
        care, reasoning = decision.care_type, decision.reasoning
    if _basic_gaps(facts) and safety_note is None:
        safety_note = explain.SAFETY_LINE[lang]  # decided with basics missing: say how to get help if it gets worse

    text = " ".join(user_turns)
    resp = TriageResponse(
        care_type=care, urgency=URGENCY_FOR_CARE[care], red_flags=[], reasoning=reasoning, language=lang, safety_note=safety_note,
        matched_caregivers=matching.match(care, facts, text, req.location, lang),
        explanation=explain.explanation(care, reasoning, ctx, lang),
    )
    log.info("dataset decision %s/%s in %.2fs", resp.care_type, resp.urgency, time.perf_counter() - t0)
    return resp


def _hints(facts: CaseFacts) -> lexicon.Hints:
    """Service and specialty tags from the analyst's clean English facts, not the raw multilingual words; preferences come
    only from the analyst's reading ("aaj hi bukhar aaya" is when it started, not a wish to be seen today)."""
    english = " ; ".join(facts.services_requested + facts.symptoms_present + facts.existing_conditions + facts.vitals_reported)
    hints = lexicon.analyse(english)
    hints.preferences = list(dict.fromkeys(facts.preferences))
    return hints


def _run_smart(req, facts, user_turns, asked_qs, asked, lang, t0, scr, safety_note) -> TriageResponse:
    hints = _hints(facts)
    decision, votes = smart.classify(facts, hints, asked_qs)
    log.info("smart votes medgemma=%s qwen=%s hints=%s", votes["medgemma"] and votes["medgemma"].care_type,
             votes["qwen"] and votes["qwen"].care_type, hints.as_dict())
    decision = decision or smart.fallback(hints)
    if decision.care_type == "emergency_112":
        mapped, more = redflags.detect(*decision.red_flags, *facts.symptoms_present)
        return _emergency(mapped, more, lang, "smart", why=decision.reasoning, texts=decision.red_flags + facts.symptoms_present)
    ctx = _context(facts)
    if missing := _missing_basics(facts, asked):
        return _ask(explain.follow_up(missing, ctx, lang, protocols.guide(facts)), lang, "smart", "medical",
                    f"Need {' and '.join(missing)} before routing safely.", safety_note, seriousness=decision.seriousness)
    if (out := _screened(scr, facts, req.conversation_history, asked, lang, "smart")) is not None:
        return out
    graded = scr.result() if scr else None
    if graded and graded.level == "see_doctor_today" and decision.seriousness == "routine":
        decision = decision.model_copy(update={"seriousness": "soon"})  # MedGemma says a doctor soon: not "plan it whenever"
    service_only = bool(facts.services_requested) and not facts.symptoms_present  # logistics are the booking screen's job
    if (decision.status == "need_more_info" and not service_only and _may_ask(facts, asked)
            and (q := _fresh(explain.follow_up(decision.missing_info or ["main concern"], ctx, lang, protocols.guide(facts)), asked_qs))):
        return _ask(q, lang, "smart", "medical", decision.reasoning, safety_note, seriousness=decision.seriousness)
    if decision.status == "need_more_info":
        decision = decision.model_copy(update={"status": "final", "reasoning": decision.reasoning if service_only else FORCED})
    nursing = {t for t in lexicon.map_free_text(facts.services_requested) if t in HOME_NURSING_TAGS}
    if service_only and nursing and decision.care_type in ("paramedic_home_visit", "clinic_visit"):
        home = facts.setting_requested == "home_visit" or "home" in facts.preferences or decision.care_type == "paramedic_home_visit"
        if home:  # an injection / dressing / drip at home is a nursing job, not a doctor's check-up
            decision = decision.model_copy(update={"care_type": "home_nursing", "specialty": "none"})
    if _basic_gaps(facts) and safety_note is None:
        safety_note = explain.SAFETY_LINE[lang]  # decided with basics missing: say how to get help if it gets worse

    providers, summary, need = smart_match.match(decision, hints, facts, req.location, lang)
    if decision.care_type == "teleconsultation" and need.setting != "online":  # the user never asked for video
        decision = decision.model_copy(update={"care_type": "specialist_visit" if need.specialty not in (None, "general_medicine") else "clinic_visit"})
    elif need.setting == "online" and decision.care_type in ("clinic_visit", "specialist_visit"):  # they asked for video
        decision = decision.model_copy(update={"care_type": "teleconsultation"})
    tests = need.soft_tests + need.info_tests + [r for r in need.required if r in smart.TEST_TAGS]
    if service_only and tests and not [r for r in need.required if r not in smart.TEST_TAGS] and decision.care_type in ("clinic_visit", "specialist_visit"):
        home = facts.setting_requested in ("lab_test", "home_visit") or "home" in facts.preferences
        decision = decision.model_copy(update={"care_type": "lab_test_home" if home else "diagnostic_test"})  # a test package
    urgency = SMART_URGENCY[decision.seriousness]
    if "soon" in facts.preferences and urgency == "scheduled":
        urgency = "same_day"  # "aaj shaam tak": the user's own timing
    show_spec = need.specialty and decision.care_type not in ("home_nursing", "lab_test_home") and decision.specialty != "none"
    top = providers[0] if providers else None
    resp = TriageResponse(
        care_type=decision.care_type, urgency=urgency, red_flags=[], reasoning=decision.reasoning,
        language=lang, mode="smart", seriousness=decision.seriousness, safety_note=safety_note,
        specialty=lexicon.label(need.specialty) if show_spec else None,
        recommended_tests=[lexicon.label(t) for t in dict.fromkeys(tests)],
        services_needed=[lexicon.label(s) for s in need.required if s not in smart.TEST_TAGS],
        match_summary=summary, matched_caregivers=providers,
        possible_causes=decision.possible_causes if CFG["smart"].get("show_possible_causes") else [],
        explanation=explain.smart_explanation(SMART_CARE_LABEL.get(decision.care_type, decision.care_type), decision.seriousness,
                                              lexicon.label(need.specialty) if show_spec else None,
                                              [lexicon.label(t) for t in tests], top, need.setting == "online", ctx, lang,
                                              soon="soon" in facts.preferences),
    )
    log.info("smart decision %s/%s (%s) in %.2fs", resp.care_type, resp.seriousness, need.profile, time.perf_counter() - t0)
    return resp
