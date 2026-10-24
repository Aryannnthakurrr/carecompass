"""POST /triage. Shared front: red-flag rules -> Qwen structurer -> rules again. Then by mode:
  dataset: MedGemma + Qwen organiser-taxonomy classifier -> reference matching on the organiser roster.
  smart:   MedGemma + Qwen seriousness analyser (specialty, first tests, procedures) -> dynamic matching on providers.csv.
Either path asks a follow-up when it needs one and can exit to the emergency path at every step."""
import logging
import time

from app import explain, intake, lexicon, matching, redflags, smart, smart_match, triage
from app.emergency_text import EMERGENCY
from app.llm import CFG
from app.schemas import Emergency, TriageRequest, TriageResponse

log = logging.getLogger("carecompass.pipeline")
URGENCY_FOR_CARE = CFG["labels"]["urgency_for_care"]
MAX_FOLLOW_UPS = CFG["intake"]["max_follow_ups"]
SMART_URGENCY = {"emergency": "emergency", "urgent": "same_day", "soon": "scheduled", "routine": "scheduled"}
SMART_CARE_LABEL = {"hospital_visit": "a hospital visit", "clinic_visit": "a doctor's visit", "specialist_visit": "a specialist visit",
                    "diagnostic_test": "a test or scan", "lab_test_home": "a home sample collection", "home_nursing": "nursing at home",
                    "paramedic_home_visit": "a home check-up", "teleconsultation": "a video consultation"}


def _emergency(flags: list[str], extra: list[str], lang: str, mode: str, why: str | None = None) -> TriageResponse:
    e = EMERGENCY[lang]
    named = ", ".join(flags + extra) or "red-flag signs"
    return TriageResponse(
        care_type="emergency_112", urgency="emergency", red_flags=flags, mode=mode,
        reasoning=why or f"Red-flag symptoms detected ({named}); routed to emergency services, not home care.",
        language=lang, explanation=e["explanation"], seriousness="emergency" if mode == "smart" else None,
        emergency=Emergency(title=e["title"], call=e["call"], steps=e["steps"]),
    )


def run(req: TriageRequest) -> TriageResponse:
    t0 = time.perf_counter()
    user_turns = [t.content for t in req.conversation_history if t.role == "user"] + [req.user_input]
    asked = sum(t.role == "assistant" for t in req.conversation_history)
    lang = req.language if req.language != "auto" else intake.guess_language(req.user_input)

    # 1. rules on the raw words: no model in the path of an emergency
    flags, extra = redflags.detect(*user_turns)
    if flags or extra:
        log.info("rules emergency %s %s (%.2fs)", flags, extra, time.perf_counter() - t0)
        return _emergency(flags, extra, lang, req.mode)

    # 2. facts in English
    try:
        facts = intake.structure(req.conversation_history, req.user_input)
    except Exception:
        log.exception("structurer failed")
        return TriageResponse(follow_up_question=explain.FALLBACK_ASK[lang], language=lang, mode=req.mode,
                              reasoning="Could not understand the request yet; asking a follow-up.")
    if req.language == "auto" and lang != "hi":  # Devanagari is unambiguous; for Latin script trust the model on en vs Hinglish
        lang = "en" if facts.detected_language == "en" else "hinglish"

    # 3. rules again on the English symptoms (catches paraphrases the raw-word rules missed)
    flags, extra = redflags.detect(*facts.symptoms_present)
    if flags or extra:
        return _emergency(flags, extra, lang, req.mode)

    if req.mode == "smart":
        return _run_smart(req, facts, user_turns, asked, lang, t0)
    return _run_dataset(req, facts, user_turns, asked, lang, t0)


def _run_dataset(req, facts, user_turns, asked, lang, t0) -> TriageResponse:
    decision, votes = triage.classify(facts)
    log.info("votes medgemma=%s qwen=%s", votes["medgemma"] and votes["medgemma"].care_type, votes["qwen"] and votes["qwen"].care_type)
    if decision is None:  # both classifiers down: someone should see the patient
        care, reasoning = "paramedic_home_visit", "Classifier unavailable; defaulting to an in-person Medic assessment."
    elif decision.care_type == "emergency_112":
        mapped, more = redflags.detect(*decision.red_flags, *facts.symptoms_present)
        return _emergency(mapped, more, lang, "dataset", why=decision.reasoning)
    elif decision.status == "need_more_info" and asked < MAX_FOLLOW_UPS:
        q = explain.follow_up(decision.missing_info or ["main concern"], req.user_input, lang)
        return TriageResponse(follow_up_question=q, language=lang, reasoning=decision.reasoning)
    else:
        care, reasoning = decision.care_type, decision.reasoning

    text = " ".join(user_turns)
    resp = TriageResponse(
        care_type=care, urgency=URGENCY_FOR_CARE[care], red_flags=[], reasoning=reasoning, language=lang,
        matched_caregivers=matching.match(care, facts, text, req.location, lang),
        explanation=explain.explanation(care, reasoning, text, lang),
    )
    log.info("dataset decision %s/%s in %.2fs", resp.care_type, resp.urgency, time.perf_counter() - t0)
    return resp


def _run_smart(req, facts, user_turns, asked, lang, t0) -> TriageResponse:
    text = " ".join(user_turns)
    hints = lexicon.analyse(text)
    decision, votes = smart.classify(facts, hints)
    log.info("smart votes medgemma=%s qwen=%s hints=%s", votes["medgemma"] and votes["medgemma"].care_type,
             votes["qwen"] and votes["qwen"].care_type, hints.as_dict())
    decision = decision or smart.fallback(hints)
    if decision.care_type == "emergency_112":
        mapped, more = redflags.detect(*decision.red_flags, *facts.symptoms_present)
        return _emergency(mapped, more, lang, "smart", why=decision.reasoning)
    if decision.status == "need_more_info" and asked < MAX_FOLLOW_UPS:
        q = explain.follow_up(decision.missing_info or ["main concern"], req.user_input, lang)
        return TriageResponse(follow_up_question=q, language=lang, mode="smart", reasoning=decision.reasoning,
                              seriousness=decision.seriousness)

    providers, summary, need = smart_match.match(decision, hints, facts, req.location, lang)
    if decision.care_type == "teleconsultation" and need.setting != "online":  # the user never asked for video
        decision = decision.model_copy(update={"care_type": "specialist_visit" if need.specialty not in (None, "general_medicine") else "clinic_visit"})
    tests = need.soft_tests + need.info_tests + [r for r in need.required if r in smart.TEST_TAGS]
    top = providers[0] if providers else None
    resp = TriageResponse(
        care_type=decision.care_type, urgency=SMART_URGENCY[decision.seriousness], red_flags=[], reasoning=decision.reasoning,
        language=lang, mode="smart", seriousness=decision.seriousness,
        specialty=lexicon.label(need.specialty) if need.specialty else None,
        recommended_tests=[lexicon.label(t) for t in dict.fromkeys(tests)],
        services_needed=[lexicon.label(s) for s in need.required if s not in smart.TEST_TAGS],
        match_summary=summary, matched_caregivers=providers,
        possible_causes=decision.possible_causes if CFG["smart"].get("show_possible_causes") else [],
        explanation=explain.smart_explanation(SMART_CARE_LABEL.get(decision.care_type, decision.care_type), decision.seriousness,
                                              lexicon.label(need.specialty) if need.specialty else None,
                                              [lexicon.label(t) for t in tests], top and top.name, top and top.reason, text, lang),
    )
    log.info("smart decision %s/%s (%s) in %.2fs", resp.care_type, resp.seriousness, need.profile, time.perf_counter() - t0)
    return resp
