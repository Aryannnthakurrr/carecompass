"""POST /triage: red-flag rules -> Qwen structurer -> rules again -> MedGemma + Qwen classifier -> follow-up or
decision -> caregiver matching -> explanation in the user's language."""
import logging
import time

from app import explain, intake, matching, redflags, triage
from app.emergency_text import EMERGENCY
from app.llm import CFG
from app.schemas import Emergency, TriageRequest, TriageResponse

log = logging.getLogger("carecompass.pipeline")
URGENCY_FOR_CARE = CFG["labels"]["urgency_for_care"]
MAX_FOLLOW_UPS = CFG["intake"]["max_follow_ups"]


def _emergency(flags: list[str], extra: list[str], lang: str, why: str | None = None) -> TriageResponse:
    e = EMERGENCY[lang]
    named = ", ".join(flags + extra) or "red-flag signs"
    return TriageResponse(
        care_type="emergency_112", urgency="emergency", red_flags=flags,
        reasoning=why or f"Red-flag symptoms detected ({named}); routed to emergency services, not home care.",
        language=lang, explanation=e["explanation"],
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
        return _emergency(flags, extra, lang)

    # 2. facts in English
    try:
        facts = intake.structure(req.conversation_history, req.user_input)
    except Exception:
        log.exception("structurer failed")
        return TriageResponse(follow_up_question=explain.FALLBACK_ASK[lang], language=lang,
                              reasoning="Could not understand the request yet; asking a follow-up.")
    if req.language == "auto" and lang != "hi":  # Devanagari is unambiguous; for Latin script trust the model on en vs Hinglish
        lang = "en" if facts.detected_language == "en" else "hinglish"

    # 3. rules again on the English symptoms (catches paraphrases the raw-word rules missed)
    flags, extra = redflags.detect(*facts.symptoms_present)
    if flags or extra:
        return _emergency(flags, extra, lang)

    # 4. classifier on facts only
    decision, votes = triage.classify(facts)
    log.info("votes medgemma=%s qwen=%s", votes["medgemma"] and votes["medgemma"].care_type, votes["qwen"] and votes["qwen"].care_type)
    if decision is None:  # both classifiers down: someone should see the patient
        care, reasoning = "paramedic_home_visit", "Classifier unavailable; defaulting to an in-person Medic assessment."
    elif decision.care_type == "emergency_112":
        mapped, more = redflags.detect(*decision.red_flags, *facts.symptoms_present)
        return _emergency(mapped, more, lang, why=decision.reasoning)
    elif decision.status == "need_more_info" and asked < MAX_FOLLOW_UPS:
        q = explain.follow_up(decision.missing_info or ["main concern"], req.user_input, lang)
        return TriageResponse(follow_up_question=q, language=lang, reasoning=decision.reasoning)
    else:
        care, reasoning = decision.care_type, decision.reasoning

    # 5. decision -> caregivers and a plain explanation
    text = " ".join(user_turns)
    resp = TriageResponse(
        care_type=care, urgency=URGENCY_FOR_CARE[care], red_flags=[], reasoning=reasoning, language=lang,
        matched_caregivers=matching.match(care, facts, text, req.location, lang),
        explanation=explain.explanation(care, reasoning, text, lang),
    )
    log.info("decision %s/%s in %.2fs", resp.care_type, resp.urgency, time.perf_counter() - t0)
    return resp
