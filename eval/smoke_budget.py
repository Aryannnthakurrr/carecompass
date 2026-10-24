"""Budget-capped smoke check of the pipeline (real models): the riskiest conversations from eval_conversations plus a
stratified sample of organiser messages (2 per gold label), with every Cerebras call metered and a hard stop.

    python eval/smoke_budget.py [--max-calls 120] [--max-usd 0.90]

Cost is estimated from the real token usage of each response at conservative prices (USD per 1M tokens) set below.
"""
import argparse
import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
from app import llm, pipeline  # noqa: E402
from app.schemas import TriageRequest  # noqa: E402
import eval_conversations as convo  # noqa: E402

PRICE_IN, PRICE_OUT = 1.00, 2.00  # USD per 1M tokens: deliberately above Cerebras list prices for this model
CASES = ["mentor: junk, fever, dodges age", "i want to die", "figurative die (hamilton)", "snake bite", "adult fever in one go",
         "child fever full", "explicit service, no questions", "junk + mixed patients", "junk forever ends", "sleepy elderly asks first"]


class Meter:
    def __init__(self, max_calls, max_usd):
        self.calls = self.tin = self.tout = 0
        self.max_calls, self.max_usd = max_calls, max_usd
        self.lock = threading.Lock()

    @property
    def usd(self):
        return (self.tin * PRICE_IN + self.tout * PRICE_OUT) / 1e6

    def wrap(self, create):
        def metered(*a, **kw):
            with self.lock:
                if self.calls >= self.max_calls or self.usd >= self.max_usd:
                    raise SystemExit(f"budget stop: {self.calls} calls, ${self.usd:.3f}")
                self.calls += 1
            r = create(*a, **kw)
            u = getattr(r, "usage", None)
            with self.lock:
                self.tin += getattr(u, "prompt_tokens", 0) or 0
                self.tout += getattr(u, "completion_tokens", 0) or 0
            return r
        return metered


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=120)
    ap.add_argument("--max-usd", type=float, default=0.90)
    a = ap.parse_args()
    meter = Meter(a.max_calls, a.max_usd)
    client = llm._qwen()
    client.chat.completions.create = meter.wrap(client.chat.completions.create)

    results = []
    print("== conversations")
    for name in CASES:
        case = next(c for c in convo.CASES if c[0] == name)
        mode = case[1] or "dataset"
        ok, log, problems = convo.run_case(mode, case)
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} [{mode}] {name}" + ("" if ok else "  <- " + "; ".join(problems)))
        if not ok:
            print("\n".join(log))
        print(f"   (so far: {meter.calls} calls, ${meter.usd:.3f})")

    print("== organiser sample (2 distinct messages per gold label)")
    seen, per = {}, {}
    for line in (ROOT / "training/triage_dataset.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        gold = r["response"]["care_type"] or "ASK"
        text = r["request"]["user_input"]
        if text in seen or per.get(gold, 0) >= 2:
            continue
        seen[text] = gold
        per[gold] = per.get(gold, 0) + 1
    for text, gold in seen.items():
        res = pipeline.run(TriageRequest(user_input=text, location={"lat": 28.54, "lng": 77.27}))
        got = res.care_type or ("ASK" if res.follow_up_question else "none")
        ok = got == gold
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {gold:22} got {got:22} | {text[:70]}")
    print(f"\n{sum(results)}/{len(results)} passed; {meter.calls} Cerebras calls, ~{meter.tin + meter.tout} tokens, est. ${meter.usd:.3f}")


if __name__ == "__main__":
    main()
