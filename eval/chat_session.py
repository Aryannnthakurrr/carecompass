"""One turn of a named conversation against the real pipeline (in-process, real models). State lives in
eval/results/sessions/<session>.json, so a tester can hold many separate conversations and answer whatever the app asks.

    python eval/chat_session.py <session> <dataset|smart> [--voice] "user message"
    python eval/chat_session.py <session> --show          # print the transcript so far

Prints the app's reply as JSON (question + kind, decision, red flags, safety note, explanation, matches, seconds).
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DIR = ROOT / "eval/results/sessions"


def summary(r) -> dict:
    return {k: v for k, v in {
        "follow_up_question": r.follow_up_question, "follow_up_kind": r.follow_up_kind, "safety_note": r.safety_note,
        "care_type": r.care_type, "urgency": r.urgency, "red_flags": r.red_flags, "seriousness": r.seriousness,
        "specialty": r.specialty, "reasoning": r.reasoning, "explanation": r.explanation, "match_summary": r.match_summary,
        "matched": [f"{m.name} ({m.role}, {m.distance_km} km)" for m in r.matched_caregivers],
        "conversation_ended": r.conversation_ended or None, "language": r.language,
    }.items() if v not in (None, [], "")}


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    name, mode = sys.argv[1], sys.argv[2]
    path = DIR / f"{name}.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"session": name, "mode": None, "turns": []}
    if mode == "--show":
        print(json.dumps(state, indent=1, ensure_ascii=False))
        return
    voice = "--voice" in sys.argv[3:]
    msg = [a for a in sys.argv[3:] if a != "--voice"][-1]
    from app import pipeline
    from app.schemas import TriageRequest, Turn
    history = []
    for t in state["turns"]:
        history += [Turn(role="user", content=t["user"]),
                    Turn(role="assistant", content=t["reply"].get("follow_up_question") or t["reply"].get("explanation") or "",
                         kind=t["reply"].get("follow_up_kind"))]
    t0 = time.time()
    r = pipeline.run(TriageRequest(user_input=msg, conversation_history=history, mode=mode, input_mode="voice" if voice else "text",
                                   location={"lat": 28.54, "lng": 77.27}))
    reply = summary(r) | {"seconds": round(time.time() - t0, 1)}
    state["mode"] = mode
    state["turns"].append({"user": msg, "voice": voice, "reply": reply})
    DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(reply, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
