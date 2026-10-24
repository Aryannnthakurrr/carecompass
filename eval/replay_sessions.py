"""Replay the user turns of saved test sessions (eval/results/sessions/*.json) against the current pipeline, into new
session files <name>__<tag>.json, and print a compact transcript. Replies may differ, so later turns may not fit perfectly.
    python eval/replay_sessions.py <tag> session_name [session_name ...]"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import pipeline  # noqa: E402
from app.schemas import TriageRequest, Turn  # noqa: E402
sys.path.insert(0, str(ROOT / "eval"))
from chat_session import summary  # noqa: E402

DIR = ROOT / "eval/results/sessions"
tag, names = sys.argv[1], sys.argv[2:]
for name in names:
    old = json.loads((DIR / f"{name}.json").read_text(encoding="utf-8"))
    hist, out = [], {"session": f"{name}__{tag}", "mode": old["mode"], "turns": []}
    print(f"\n=== {name} [{old['mode']}]")
    for t in old["turns"]:
        t0 = time.time()
        r = pipeline.run(TriageRequest(user_input=t["user"], conversation_history=hist, mode=old["mode"],
                                       input_mode="voice" if t.get("voice") else "text", location={"lat": 28.54, "lng": 77.27}))
        rep = summary(r) | {"seconds": round(time.time() - t0, 1)}
        out["turns"].append({"user": t["user"], "voice": t.get("voice"), "reply": rep})
        shown = (f"ASK[{r.follow_up_kind}] {r.follow_up_question}" if r.follow_up_question else
                 "ENDED " + (r.explanation or "") if r.conversation_ended else
                 f"{r.care_type}/{r.urgency} flags={r.red_flags} | {r.explanation or ''}")
        print(f"  U: {t['user'][:110]}\n   -> {shown[:330]}" + (f"  [note: {r.safety_note[:40]}]" if r.safety_note else "") + f" ({rep['seconds']}s)")
        hist += [Turn(role="user", content=t["user"]), Turn(role="assistant", content=r.follow_up_question or r.explanation or "", kind=r.follow_up_kind)]
        if r.care_type == "emergency_112" or r.conversation_ended or (r.care_type and not r.follow_up_question):
            break
    (DIR / f"{name}__{tag}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
