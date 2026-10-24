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
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from app import explain, intake, lexicon, matching, redflags, screen, smart, smart_match, triage
from app.emergency_text import EMERGENCY
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
SAFETY_MARK = "112"  # every safety-check question ends with the pre-written call-112 line (explain.SAFETY_LINE)
_POOL = ThreadPoolExecutor(max_workers=8)


def _emergency(flags: list[str], extra: list[str], lang: str, mode: str, why: str | None = None) -> TriageResponse:
    e = EMERGENCY[lang]
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
    r = _run(req)
    if r.care_type == "emergency_112":
        return r  # pre-written text; red-flag names are not diagnoses
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


def _missing_basics(facts: CaseFacts, asked: int) -> list[str]:
    """A symptom complaint needs who (and, for fever, the age group) and since when before it can be routed safely.
    Explicit requests (a named service or kind of visit) need neither: the user already knows what they want."""
    if facts.no_more_questions or asked >= MAX_FOLLOW_UPS or not facts.symptoms_present:
        return []
    if facts.services_requested or facts.setting_requested not in (None, "unspecified"):
        return []
    missing = []
    fever = any("fever" in s.lower() or "temperature" in s.lower() for s in facts.symptoms_present + facts.vitals_reported)
    if not (facts.patient or facts.age_group):
        missing.append("who the patient is and how old")
    elif fever and facts.age_group is None and (facts.patient or "").lower() not in ("self", "me", "myself"):
        missing.append("how old the patient is")
    if not facts.duration:
        missing.append("since when")
    return missing[:2]


def _screened(scr, facts: CaseFacts, history, asked: int, lang: str, mode: str):
    """MedGemma's emergency screen -> an emergency, a safety-check question, or None (routing unchanged)."""
    s = scr.result() if scr else None
    if s is None or not s.emergency:
        return None
    signs = s.danger_signs or facts.symptoms_present[:2]
    checked = any(t.role == "assistant" and SAFETY_MARK in t.content for t in history)
    if checked or asked >= MAX_FOLLOW_UPS:  # asked once already and the danger persists, or no room left to ask
        mapped, more = redflags.detect(*signs, *facts.symptoms_present)
        log.info("screen emergency confirmed: %s", signs)
        return _emergency(mapped, more, lang, mode, why=f"MedGemma emergency screen: {', '.join(signs)}. {s.reasoning}")
    q, line = explain.safety_check(s.check_question, signs, lang)
    log.info("screen safety check: %s", signs)
    return _ask(q, lang, mode, "safety_check", f"Possible danger signs ({', '.join(signs)}); checking before deciding.", line)


def _run(req: TriageRequest) -> TriageResponse:
    t0 = time.perf_counter()
    history = req.conversation_history
    user_turns = [t.content for t in history if t.role == "user"] + [req.user_input]
    asked_qs = intake.asked_questions(history)
    asked = len(asked_qs)
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
        return _ask(explain.FALLBACK_ASK[lang], lang, req.mode, "medical", "Could not understand the request yet; asking a follow-up.")
    if req.language == "auto" and lang != "hi":  # Devanagari is unambiguous; for Latin script trust the model on en vs Hinglish
        lang = "en" if facts.detected_language == "en" else "hinglish"

    # 3. emergencies: rules on the English symptoms, then raw-word hits the analyst did not explain away
    flags, extra = redflags.detect(*facts.symptoms_present)
    if flags or extra:
        return _emergency(flags, extra, lang, req.mode)
    safety_note = None
    if raw_flags or raw_extra:
        if facts.not_literal or facts.message_check in GATE_STOP:
            safety_note = explain.SAFETY_LINE[lang]  # not sounding the alarm for a figure of speech, but saying how to get help
            log.info("raw rule hit %s judged not literal: %s", raw_flags + raw_extra, facts.not_literal)
        else:
            return _emergency(raw_flags, raw_extra, lang, req.mode)

    # 4. the gate: nothing here reaches a medical model
    log.info("intake check=%s notes=%s wants=%s", facts.message_check, facts.check_notes, facts.user_wants)
    if facts.message_check in GATE_STOP and not _medical(facts):
        return _ask(explain.redirect(lang, facts.message_check == "abusive"), lang, req.mode, "redirect",
                    "No health concern described; steering back to the health problem.", safety_note)
    if facts.message_check == "unclear" and (voice or not _medical(facts)) and asked < MAX_FOLLOW_UPS:
        q = explain.misheard(req.user_input, lang) if voice else explain.clarify(facts.check_notes, _context(facts), lang)
        return _ask(q, lang, req.mode, "misheard" if voice else "clarify", "The message was unclear; confirming before going further.")
    if facts.message_check in ("mixed_up", "contradictory") and asked < MAX_FOLLOW_UPS:
        return _ask(explain.clarify(facts.check_notes, _context(facts), lang), lang, req.mode, "clarify",
                    f"Clarifying first: {'; '.join(facts.check_notes) or facts.message_check}.", safety_note)
    if not _medical(facts):
        return _ask(explain.FALLBACK_ASK[lang], lang, req.mode, "medical", "No health concern described yet; asking what it is.", safety_note)

    # 5. classifier + MedGemma's emergency screen, in parallel. The screen waits until who/age and since-when are known:
    #    with them missing it assumes the worst case (an infant) and raises false alarms; clear emergencies are already
    #    caught by the rules above and by the classifier.
    basics_known = not _missing_basics(facts, len(asked_qs))
    scr = _POOL.submit(screen.screen, facts) if basics_known and screen.wanted(facts) else None
    if req.mode == "smart":
        return _run_smart(req, facts, user_turns, asked_qs, lang, t0, scr, safety_note)
    return _run_dataset(req, facts, user_turns, asked_qs, lang, t0, scr, safety_note)


def _run_dataset(req, facts, user_turns, asked_qs, lang, t0, scr, safety_note) -> TriageResponse:
    asked = len(asked_qs)
    decision, votes = triage.classify(facts, asked_qs)
    log.info("votes medgemma=%s qwen=%s", votes["medgemma"] and votes["medgemma"].care_type, votes["qwen"] and votes["qwen"].care_type)
    if decision is not None and decision.care_type == "emergency_112":
        mapped, more = redflags.detect(*decision.red_flags, *facts.symptoms_present)
        return _emergency(mapped, more, lang, "dataset", why=decision.reasoning)
    ctx = _context(facts)
    if missing := _missing_basics(facts, asked):
        return _ask(explain.follow_up(missing, ctx, lang), lang, "dataset", "medical",
                    f"Need {' and '.join(missing)} before routing safely.", safety_note)
    if (out := _screened(scr, facts, req.conversation_history, asked, lang, "dataset")) is not None:
        return out
    if decision is None:  # both classifiers down: someone should see the patient
        care, reasoning = "paramedic_home_visit", "Classifier unavailable; defaulting to an in-person Medic assessment."
    elif decision.status == "need_more_info" and asked < MAX_FOLLOW_UPS and not facts.no_more_questions:
        return _ask(explain.follow_up(decision.missing_info or ["main concern"], ctx, lang), lang, "dataset", "medical",
                    decision.reasoning, safety_note)
    else:
        care, reasoning = decision.care_type, decision.reasoning

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


def _run_smart(req, facts, user_turns, asked_qs, lang, t0, scr, safety_note) -> TriageResponse:
    asked = len(asked_qs)
    hints = _hints(facts)
    decision, votes = smart.classify(facts, hints, asked_qs)
    log.info("smart votes medgemma=%s qwen=%s hints=%s", votes["medgemma"] and votes["medgemma"].care_type,
             votes["qwen"] and votes["qwen"].care_type, hints.as_dict())
    decision = decision or smart.fallback(hints)
    if decision.care_type == "emergency_112":
        mapped, more = redflags.detect(*decision.red_flags, *facts.symptoms_present)
        return _emergency(mapped, more, lang, "smart", why=decision.reasoning)
    ctx = _context(facts)
    if missing := _missing_basics(facts, asked):
        return _ask(explain.follow_up(missing, ctx, lang), lang, "smart", "medical",
                    f"Need {' and '.join(missing)} before routing safely.", safety_note, seriousness=decision.seriousness)
    if (out := _screened(scr, facts, req.conversation_history, asked, lang, "smart")) is not None:
        return out
    if decision.status == "need_more_info" and asked < MAX_FOLLOW_UPS and not facts.no_more_questions:
        return _ask(explain.follow_up(decision.missing_info or ["main concern"], ctx, lang), lang, "smart", "medical",
                    decision.reasoning, safety_note, seriousness=decision.seriousness)

    providers, summary, need = smart_match.match(decision, hints, facts, req.location, lang)
    if decision.care_type == "teleconsultation" and need.setting != "online":  # the user never asked for video
        decision = decision.model_copy(update={"care_type": "specialist_visit" if need.specialty not in (None, "general_medicine") else "clinic_visit"})
    tests = need.soft_tests + need.info_tests + [r for r in need.required if r in smart.TEST_TAGS]
    top = providers[0] if providers else None
    resp = TriageResponse(
        care_type=decision.care_type, urgency=SMART_URGENCY[decision.seriousness], red_flags=[], reasoning=decision.reasoning,
        language=lang, mode="smart", seriousness=decision.seriousness, safety_note=safety_note,
        specialty=lexicon.label(need.specialty) if need.specialty else None,
        recommended_tests=[lexicon.label(t) for t in dict.fromkeys(tests)],
        services_needed=[lexicon.label(s) for s in need.required if s not in smart.TEST_TAGS],
        match_summary=summary, matched_caregivers=providers,
        possible_causes=decision.possible_causes if CFG["smart"].get("show_possible_causes") else [],
        explanation=explain.smart_explanation(SMART_CARE_LABEL.get(decision.care_type, decision.care_type), decision.seriousness,
                                              lexicon.label(need.specialty) if need.specialty else None,
                                              [lexicon.label(t) for t in tests], top and top.name, top and top.reason, ctx, lang),
    )
    log.info("smart decision %s/%s (%s) in %.2fs", resp.care_type, resp.seriousness, need.profile, time.perf_counter() - t0)
    return resp
