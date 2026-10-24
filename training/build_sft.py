"""Build the MedGemma LoRA dataset: the same task MedGemma does in production, CaseFacts JSON -> Decision JSON.

Distilled from a panel of models, with a stronger adjudicator for disagreements, so MedGemma is not capped at Qwen.

Stages (each resumes where it stopped; outputs in training/sft/):
  generate  a generator (Qwen, or gpt-oss-120b for a second voice) writes requests for scenario x language x style,
            including gray-zone scenarios with no intended answer (the panel decides)   -> requests.jsonl
            (+ the organiser's ~66 distinct messages with their gold labels)
  facts     the REAL structurer (app/intake.py, Qwen, as in production) turns every request into CaseFacts -> facts.jsonl
            so training inputs look exactly like what MedGemma sees live. Teachers see these facts only: a label
            that needs details the facts lost would teach MedGemma to guess.
  label     panel gpt-oss-120b + gpt-5.4-mini + Qwen votes in parallel. Unanimous -> kept. Split -> gpt-5.4 adjudicates
            with every vote in view, and may never overrule an emergency vote on its own (those go to review).
            OpenAI spend is metered from real token usage and capped (--budget); past the cap mini leaves the panel
            and gpt-oss-120b at high reasoning adjudicates instead.             -> labelled.jsonl, review.jsonl, spend.json
  export    chat-format JSONL (system = triage.RULES, no examples), split by generation batch -> train/val/test.jsonl
  hard      YOUR hand-written cases (sft/hard_raw.jsonl: {"turns": [...], "target": ...}) -> hard.jsonl, the unbiased test set

Usage:
  python training/build_sft.py all --per-call 2 --styles 1 --limit 6     # smoke test, a few dozen API calls
  python training/build_sft.py generate --generator gpt-oss-120b        # a second generator, for diversity
  python training/build_sft.py all                                       # full build
  python training/build_sft.py export                                    # re-split without calling any model
  python training/build_sft.py hard                                      # structure the hand-labelled hard set
"""
import argparse
import hashlib
import json
import os
import random
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import intake, llm, redflags, triage  # noqa: E402
from app.schemas import CaseFacts, Decision, Turn  # noqa: E402
from openai import OpenAI  # noqa: E402

OUT = ROOT / "training" / "sft"
NEED = "need_more_info"  # pseudo-label for "ask a follow-up"

# care target -> scenarios. Each scenario is one generation prompt per language x style.
SCENARIOS = {
    "home_nursing": [
        "a doctor-prescribed injection to be given at home", "an IV drip or saline at home",
        "dressing of a post-surgery wound", "dressing of a fresh small wound or scrape", "catheter care or catheter change",
        "bedsore care for a bedridden elderly person", "nebulization at home for someone with known asthma who is breathing normally",
        "insulin injection help for a diabetic", "stitch removal at home", "feeding tube (Ryles tube) care",
    ],
    "lab_test_home": [
        "a blood test with home sample collection", "a sugar / HbA1c test at home", "a thyroid test",
        "a full body checkup", "a CBC or vitamin D / B12 level test", "a urine test sample pickup", "a lipid profile before a doctor visit",
    ],
    "teleconsultation": [
        "mild acidity or gas", "wanting a doctor to look at existing reports", "a second opinion on a prescription",
        "a prescription renewal or medicine query", "a mild cold or sore throat for an adult", "a mild skin rash or itching",
        "diet advice for a known condition", "a mild headache from screen time", "a question about periods or mild cramps",
    ],
    "paramedic_home_visit": [
        "fever with cough for a couple of days, no breathing trouble", "general weakness or tiredness in an elderly parent",
        "a routine BP and sugar check at home", "an elderly person not eating well", "mild loose motions in an adult who is drinking fluids",
        "body ache and mild fever", "a check-up after hospital discharge", "vomited once, otherwise fine, wants someone to check",
    ],
    "emergency_112": [
        "chest pain or pressure", "severe difficulty breathing", "someone unconscious or collapsed and not responding",
        "a seizure or fits", "stroke signs: face drooping, one-sided weakness, slurred speech", "heavy bleeding that will not stop",
        "high fever in a baby under 3 months", "poisoning or swallowing something toxic", "swelling of face or throat after a sting or food",
        "talking about ending their life", "severe dehydration: not passing urine, very drowsy", "head injury followed by vomiting or confusion",
        "bleeding during pregnancy",
    ],
    "panel": [  # gray zones: no intended answer, the teacher panel decides. This is where a small model gains most.
        "an elderly person who is suddenly much more sleepy than usual and not eating", "fever of 103F in a 2-year-old",
        "a fall with a swollen, painful wrist", "dizziness on standing in an elderly person on BP medicines",
        "mild chest discomfort after a heavy spicy meal", "getting breathless climbing stairs, slowly worse over months",
        "a child vomiting many times since morning", "a diabetic whose sugar reading is above 400", "a pregnant woman with a headache",
        "a burn on the hand from hot oil", "an old wound that is now red, warm and oozing", "a known asthma patient wheezing more than usual",
        "a bedridden patient whose urine has become very dark and reduced", "a person who fainted briefly and is now awake and talking",
        "high fever for 5 days that is not coming down", "a dog bite on the leg", "severe stomach pain since a few hours",
        "an elderly person confused since this morning",
    ],
    NEED: [
        "a vague request with no symptom and no service (e.g. just wanting a doctor or help)",
        "asking to book something for a family member without saying what is wrong",
        "a greeting or a question about the service with no health detail",
    ],
}
LANGS = {
    "en": "English",
    "hi": "Hindi in Devanagari script",
    "hinglish": "Hinglish: romanised Hindi mixed with English, the way people type on WhatsApp",
}
STYLES = [
    "a short WhatsApp message, casual, maybe a typo",
    "a voice-note transcript: rambling, filler words, no punctuation",
    "a complete, polite message that mentions who the patient is and their age",
    "a message that also says which worrying symptoms are NOT present",
    "two messages: first a vague opener, then (after the app asks what is wrong) the actual details",
]
ASK = {"en": "Who is this for and what is the main concern?", "hi": "यह किसके लिए है और मुख्य परेशानी क्या है?",
       "hinglish": "Yeh kiske liye hai aur main problem kya hai?"}
CARE_DEF = {k: v for k, v in (line.split(":", 1) for line in triage.RULES.splitlines() if line.startswith("- "))}


# ---------------------------------------------------------------- io helpers

def read(name):
    p = OUT / name
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def append(name, rows):
    with (OUT / name).open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# Out-of-money / out-of-quota errors stop the whole build at once. Retrying them burned the Cerebras account
# and kept paying OpenAI for rows that could no longer be kept (24 Oct 2026).
FATAL = ("402", "payment_required", "insufficient_quota", "RESOURCE_EXHAUSTED", "PerDay", "billing")
HALT = threading.Event()


class Halt(RuntimeError):
    pass


def halt(why):
    if not HALT.is_set():
        print(f"\n  HALT: {why}\n  Nothing more will be called. Rows finished so far are saved; rerun the stage to resume.", flush=True)
    HALT.set()
    raise Halt(why)


def retry(fn, tries=4):
    for i in range(tries):
        if HALT.is_set():
            raise Halt("halted")
        try:
            return fn()
        except Halt:
            raise
        except Exception as e:  # noqa: BLE001 - rate limits and provider hiccups
            if any(k in str(e) for k in FATAL):
                halt(str(e)[:200])
            if i == tries - 1:
                raise
            print(f"  retry {i + 1}: {str(e)[:120]}", flush=True)
            time.sleep(2 * (i + 1))


def pmap(fn, items, workers):
    out = []
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(fn, it) for it in items]
        for i, f in enumerate(as_completed(futs), 1):
            try:
                out.append(f.result())
            except Halt:
                for g in futs:
                    g.cancel()
            except Exception as e:  # noqa: BLE001
                print(f"  dropped: {str(e)[:160]}", flush=True)
            if i % 25 == 0:
                print(f"  {i}/{len(items)}", flush=True)
    return [o for o in out if o]


CALLS, CALL_CAPS = {}, {}  # requests per provider this run, and the --max-calls ceiling


def count_call(endpoint):
    with _lock:
        CALLS[endpoint] = CALLS.get(endpoint, 0) + 1
        n = CALLS[endpoint]
    if n > CALL_CAPS.get(endpoint, float("inf")):
        halt(f"{endpoint} request cap reached ({CALL_CAPS[endpoint]}); raise --max-calls-{endpoint} to continue")


def chunks(items, n=100):
    return [items[i: i + n] for i in range(0, len(items), n)]


# ---------------------------------------------------------------- stage 1: requests

# ---------------------------------------------------------------- model clients (paid APIs, keys from .env)

ENDPOINTS = {"openai": (None, "OPENAI_API_KEY"), "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY")}
MODELS = {  # name -> (endpoint, model id, request options, USD per 1M tokens: input, cached input, output)
    "gpt-oss-120b": ("cerebras", "gpt-oss-120b", {"temperature": 0, "reasoning_effort": "medium"}, None),
    "gpt-oss-120b-high": ("cerebras", "gpt-oss-120b", {"temperature": 0, "reasoning_effort": "high"}, None),
    "qwen": ("cerebras", llm.MODELS["conversation"]["model"], {"temperature": 0, "reasoning_effort": "none"}, None),
    # OpenAI list prices from developers.openai.com model pages (Oct 2026; VERIFY before a big run)
    "gpt-5.4-mini": ("openai", "gpt-5.4-mini", {"reasoning_effort": "low"}, (0.75, 0.075, 4.50)),
    "gpt-5.4": ("openai", "gpt-5.4", {"reasoning_effort": "medium"}, (2.50, 0.25, 15.00)),
}
CAPS = {"gpt-5.4-mini": 2.60, "gpt-5.4": 1.15}  # USD; scaled by --budget. Past a cap the model is simply not called again.
SPEND = {}
_lock = threading.Lock()
_clients = {}


def client(endpoint):
    if endpoint not in _clients:
        url, key = ENDPOINTS[endpoint]
        _clients[endpoint] = OpenAI(base_url=url, api_key=os.environ[key], timeout=180, max_retries=2)
    return _clients[endpoint]


def affordable(name):
    cap = CAPS.get(name)
    return cap is None or SPEND.get(name, 0.0) < cap


def ask(name, system, user, **override):
    endpoint, model, kw, price = MODELS[name]
    if not affordable(name):
        raise RuntimeError(f"{name} budget cap reached")
    count_call(endpoint)
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
    r = client(endpoint).chat.completions.create(model=model, messages=msgs, response_format={"type": "json_object"}, **{**kw, **override})
    if price:  # meter real usage; reasoning tokens are billed as output and are included in completion_tokens
        u = r.usage
        cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        cost = ((u.prompt_tokens - cached) * price[0] + cached * price[1] + u.completion_tokens * price[2]) / 1e6
        with _lock:
            SPEND[name] = SPEND.get(name, 0.0) + cost
    return llm._parse(r.choices[0].message.content)


def load_spend():
    """Spend is cumulative across runs, so a restart can never take the total past --budget."""
    if (OUT / "spend.json").exists():
        SPEND.update(json.loads((OUT / "spend.json").read_text()))


def save_spend():
    (OUT / "spend.json").write_text(json.dumps({k: round(v, 4) for k, v in SPEND.items()}, indent=1))


def gen_call(generator, target, scenario, lang, style, n):
    if target == "panel":
        routing = "This is a gray zone: write it naturally and do not hint at how serious it is or where it should go.\n"
    else:
        definition = "too vague to choose any care type" if target == NEED else CARE_DEF.get(f"- {target}", target)
        routing = f"The right routing for every message is {target} ({definition.strip()}); make sure it clearly fits.\n"
    two = style.startswith("two messages")
    prompt = (
        f"Write {n} different messages that real users in Delhi NCR might send to a home-healthcare app.\n"
        f"Language: {LANGS[lang]}.\nSituation: {scenario}.\nStyle: {style}.\n" + routing +
        "Vary the patient (self, parent, grandparent, child, spouse), ages, durations and wording. Users never name a diagnosis "
        "the app should not know; they describe what they see or what they need.\n"
        + ('Return JSON {"items": [{"turns": ["first message", "second message"]}, ...]}.' if two else
           'Return JSON {"items": [{"turns": ["message"]}, ...]}.')
    )
    # temperature > 0: diversity matters here, unlike the production calls; high temperature garbles Devanagari
    override = {"temperature": 0.7 if lang == "hi" else 0.9} | ({"reasoning_effort": "low"} if generator.startswith("gpt-oss") else {})
    items = ask(generator, None, prompt, **override).get("items", [])
    return [i["turns"] for i in items if isinstance(i.get("turns"), list) and all(isinstance(t, str) and t.strip() for t in i["turns"])][:n]


def stage_generate(a):
    done = {r["batch"] for r in read("requests.jsonl")}
    rng = random.Random(a.seed)
    cells = []
    for target, scenarios in SCENARIOS.items():
        for si, scenario in enumerate(scenarios):
            for lang in LANGS:
                for style in rng.sample(STYLES, a.styles):
                    key = f"{target}|{scenario}|{lang}|{style}" + ("" if a.generator == "qwen" else f"|{a.generator}")
                    batch = hashlib.md5(key.encode()).hexdigest()[:10]
                    if batch not in done:
                        cells.append((batch, target, si, scenario, lang, style))
    if a.limit:  # smoke test: a random spread of targets, not the first scenario only
        cells = rng.sample(cells, min(a.limit, len(cells)))
    print(f"generate: {len(cells)} prompts x {a.per_call} messages")

    def one(cell):
        batch, target, si, scenario, lang, style = cell
        turns_list = retry(lambda: gen_call(a.generator, target, scenario, lang, style, a.per_call))
        return [{"id": f"{batch}-{k}", "batch": batch, "source": "synthetic", "generator": a.generator, "target": target,
                 "scenario": f"{target}/{si}", "lang": lang, "style": STYLES.index(style), "turns": t} for k, t in enumerate(turns_list)]

    rows = [r for rs in pmap(one, cells, a.workers) for r in rs]
    if not any(r["source"] == "organiser" for r in read("requests.jsonl")):
        rows += organiser_rows()
    append("requests.jsonl", rows)
    print(f"  +{len(rows)} requests")


def organiser_rows():
    seen, rows = set(), []
    lang = {"English": "en", "Hindi": "hi", "Hinglish": "hinglish"}
    for line in (ROOT / "training/triage_dataset.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        text = r["request"]["user_input"]
        if text in seen:
            continue
        seen.add(text)
        h = hashlib.md5(text.encode()).hexdigest()[:10]
        rows.append({"id": f"org-{h}", "batch": f"org-{h}", "source": "organiser", "target": r["response"]["care_type"] or NEED,
                     "scenario": "organiser", "lang": lang[r["detected_language"]], "style": None, "turns": [text],
                     "organiser_reasoning": r["response"]["reasoning"]})
    return rows


# ---------------------------------------------------------------- stage 2: facts (the real structurer)

def stage_facts(a):
    done = {r["id"] for r in read("facts.jsonl")}
    todo = [r for r in read("requests.jsonl") if r["id"] not in done]
    print(f"facts: {len(todo)} requests")

    def one(r):
        *opener, last = r["turns"]
        history = [t for o in opener for t in (Turn(role="user", content=o), Turn(role="assistant", content=ASK[r["lang"]]))]
        count_call("cerebras")  # the production structurer: Qwen on Cerebras
        facts = retry(lambda: intake.structure(history, last))
        raw_flags = redflags.detect(*r["turns"])
        lang = intake.guess_language(last) if r["source"] == "synthetic" else r["lang"]  # the generator sometimes switches script
        return {**r, "lang": lang, "facts": facts.model_dump(), "facts_json": triage._facts_json(facts),
                "rules_raw": raw_flags[0] + raw_flags[1], "rules_facts": sum(redflags.detect(*facts.symptoms_present), [])}

    n = 0
    for chunk in chunks(todo):  # write as we go so an interrupted run resumes instead of starting over
        rows = pmap(one, chunk, a.workers)
        append("facts.jsonl", rows)
        n += len(rows)
        if HALT.is_set():
            break
    print(f"  +{n} facts")


# ---------------------------------------------------------------- stage 3: labels (teacher panel)

TEACH = triage.SYSTEM + "\nDecide from these facts only. Keep reasoning under 20 words." + llm._schema_hint(Decision)


def lab(d):
    return None if d is None else NEED if d.status == NEED else d.care_type


def vote(name, user):
    if not affordable(name):
        return None
    try:
        return Decision.model_validate(retry(lambda: ask(name, TEACH, user), tries=3))
    except Exception as e:  # noqa: BLE001 - a missing vote is recorded, not fatal
        print(f"  {name} failed: {str(e)[:100]}", flush=True)
        return None


def adjudicate(user, panel):
    shown = "\n".join(f"- reviewer {i + 1}: {d.model_dump_json()}" for i, d in enumerate(v for v in panel.values() if v))
    prompt = (f"{user}\n\nIndependent triage reviewers disagreed:\n{shown}\n\nWeigh their arguments against the rules and the "
              "facts, then give the single correct decision. If a red flag is plausibly present, choose emergency_112.")
    for name in ("gpt-5.4", "gpt-oss-120b-high"):  # the fallback takes over once the OpenAI cap is reached
        if affordable(name):
            try:
                return Decision.model_validate(retry(lambda: ask(name, TEACH, prompt), tries=3)), name
            except Exception as e:  # noqa: BLE001
                print(f"  adjudicator {name} failed: {str(e)[:100]}", flush=True)
    return None, None


def stage_label(a):
    done = {r["id"] for r in read("labelled.jsonl")} | {r["id"] for r in read("review.jsonl")}
    todo = [r for r in read("facts.jsonl") if r["id"] not in done]
    if a.limit:  # pilot: measure cost per row and the split-vote rate before the full run
        todo = random.Random(a.seed).sample(todo, min(a.limit, len(todo)))
    for k in CAPS:
        CAPS[k] *= a.budget / 3.75
    load_spend()
    print(f"label: {len(todo)} rows, panel {a.teachers}, OpenAI caps {CAPS}, already spent {SPEND}")

    def one(r):
        user = f"Patient facts: {r['facts_json']}"
        with ThreadPoolExecutor(len(a.teachers)) as ex:  # every teacher at once
            panel = dict(zip(a.teachers, ex.map(lambda n: vote(n, user), a.teachers)))
        valid = [d for d in panel.values() if d]
        out = {**r, "votes": {n: lab(d) for n, d in panel.items()}, "written_for": r["target"]}
        if HALT.is_set():
            return None  # not labelled; a resumed run picks it up
        if len(valid) < 2:
            return {**out, "keep": False, "why": "fewer than two teachers answered"}
        if len({lab(d) for d in valid}) == 1:
            final, how = valid[0], "unanimous"
        else:
            final, judge = adjudicate(user, panel)
            if final is None:
                return {**out, "keep": False, "why": "split vote and no adjudicator available"}
            out["votes"]["adjudicator"], how = lab(final), f"adjudicated by {judge}"
            if lab(final) != "emergency_112" and any(lab(d) == "emergency_112" for d in valid):
                return {**out, "keep": False, "why": "adjudicator overruled an emergency vote: human check"}
        if r["source"] == "organiser" and lab(final) != r["target"]:  # the organiser label is what the hidden test scores
            gold = r["target"]
            care = "teleconsultation" if gold == NEED else gold
            out["panel_said"] = lab(final)
            final = Decision(status=NEED if gold == NEED else "final", care_type=care,
                             red_flags=final.red_flags if care == "emergency_112" else [],
                             missing_info=(final.missing_info[:1] or [ASK["en"]]) if gold == NEED else [],
                             reasoning=r["organiser_reasoning"].split("; urgency assessed")[0].rstrip(".") + ".")
            how = "organiser gold over panel"
        if r["rules_facts"] and lab(final) != "emergency_112":
            return {**out, "keep": False, "why": f"red-flag rules fire on the facts ({r['rules_facts']}) but the panel said {lab(final)}"}
        if r["rules_raw"] and lab(final) != "emergency_112":
            return {**out, "keep": False, "why": f"raw-text rules fire on a non-emergency ({r['rules_raw']}): check app/redflags.py"}
        return {**out, "target": lab(final), "decision": final.model_dump(), "how": how, "keep": True, "why": how}

    kept = review = 0
    for chunk in chunks(todo):
        rows = pmap(one, chunk, a.workers)
        append("labelled.jsonl", [r for r in rows if r["keep"]])
        append("review.jsonl", [r for r in rows if not r["keep"]])
        kept, review = kept + sum(r["keep"] for r in rows), review + sum(not r["keep"] for r in rows)
        save_spend()
        if HALT.is_set():
            break
        print(f"  kept {kept}, review {review}, OpenAI spend (all runs) {({k: round(v, 3) for k, v in SPEND.items()})}", flush=True)


# ---------------------------------------------------------------- stage 4: export

def to_chat(r):
    d = Decision.model_validate(r["decision"])  # key order = schema order = what Ollama's grammar will emit
    return {"id": r["id"], "target": r["target"], "source": r["source"], "lang": r["lang"],
            "messages": [{"role": "system", "content": triage.RULES},
                         {"role": "user", "content": f"Patient facts: {r['facts_json']}"},
                         {"role": "assistant", "content": json.dumps(d.model_dump(), ensure_ascii=False, separators=(",", ":"))}]}


def stage_export(a):
    # manual_review.jsonl (manual_review.py) overrides panel labels recall-first; manual.jsonl adds hand-written follow-ups
    review = {r["id"]: r for r in read("manual_review.jsonl")}
    rows = [review.pop(r["id"], r) for r in read("labelled.jsonl")] + list(review.values()) + read("manual.jsonl")
    # deduplicate identical inputs (the structurer often maps paraphrases to the same facts)
    uniq = {}
    for r in rows:
        uniq.setdefault(r["facts_json"], r)
    rows = list(uniq.values())
    # split by generation batch so paraphrase siblings never straddle train and test. The split is a hash of the batch id,
    # not a shuffle, so relabelling a row never moves it (or anything else) to another split: 15% test, 10% val.
    def split_of(batch):
        h = int(hashlib.md5(f"split|{batch}".encode()).hexdigest(), 16) % 100
        return "test" if h < 15 else "val" if h < 25 else "train"
    rng = random.Random(a.seed)
    out = {"train": [], "val": [], "test": []}
    for r in rows:
        out[split_of(r["batch"])].append(to_chat(r))
    for name, rs in out.items():
        rng.shuffle(rs)
        (OUT / f"{name}.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rs), encoding="utf-8")
    review = read("review.jsonl")
    stats = {name: {"rows": len(rs), "by_target": Counter(x["target"] for x in rs), "by_lang": Counter(x["lang"] for x in rs),
                    "organiser": sum(x["source"] == "organiser" for x in rs)} for name, rs in out.items()}
    stats["review"] = {"rows": len(review), "why": Counter(r["why"].split(" (")[0].split(":")[0] for r in review)}
    stats["how"] = Counter(r.get("how", "?") for r in rows)
    stats["changed_from_written_for"] = sum(r["target"] != r.get("written_for", r["target"]) for r in rows if r["source"] == "synthetic")
    models = sorted({m for r in rows for m in r.get("votes", {})})
    stats["agreement_with_final"] = {m: f"{sum(r['votes'].get(m) == r['target'] for r in rows if r.get('votes', {}).get(m))}"
                                        f"/{sum(bool(r.get('votes', {}).get(m)) for r in rows)}" for m in models}
    stats["by_generator"] = Counter(r.get("generator", r["source"]) for r in rows)
    stats["emergency_rows_rules_missed"] = sum(r["target"] == "emergency_112" and not r["rules_raw"] for r in rows)
    (OUT / "stats.json").write_text(json.dumps(stats, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(stats, indent=1, ensure_ascii=False))


def stage_hard(a):
    """Human-written, human-labelled cases. Qwen wrote and labelled the synthetic data, so this is the only test it can't game."""
    raw = read("hard_raw.jsonl")
    print(f"hard: {len(raw)} cases")

    def one(item):
        k, r = item
        *opener, last = r["turns"]
        lang = r.get("lang") or intake.guess_language(last)
        history = [t for o in opener for t in (Turn(role="user", content=o), Turn(role="assistant", content=ASK[lang]))]
        facts = retry(lambda: intake.structure(history, last))
        return {"id": f"hard-{k}", "target": r["target"], "source": "human", "lang": lang, "turns": r["turns"],
                "rules_raw": sum(redflags.detect(*r["turns"]), []),
                "messages": [{"role": "system", "content": triage.RULES},
                             {"role": "user", "content": f"Patient facts: {triage._facts_json(facts)}"}]}

    rows = sorted(pmap(one, list(enumerate(raw)), a.workers), key=lambda r: int(r["id"][5:]))
    (OUT / "hard.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")
    print(f"  wrote {len(rows)} -> sft/hard.jsonl; raw-text rules fired on {sum(bool(r['rules_raw']) for r in rows)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["generate", "facts", "label", "export", "hard", "all"])
    ap.add_argument("--per-call", type=int, default=5, help="messages per generation prompt")
    ap.add_argument("--styles", type=int, default=2, help="styles sampled per scenario x language")
    ap.add_argument("--limit", type=int, default=0, help="cap the number of generation prompts (smoke tests)")
    ap.add_argument("--workers", type=int, default=8, help="rows in flight; each row calls every teacher at once")
    ap.add_argument("--generator", default="qwen", choices=["qwen", "gpt-oss-120b"])
    ap.add_argument("--teachers", default="gpt-oss-120b,gpt-5.4-mini,qwen", help="the voting panel; all must agree or gpt-5.4 adjudicates")
    ap.add_argument("--max-calls-cerebras", type=int, default=1500, help="hard ceiling on Cerebras requests per run")
    ap.add_argument("--max-calls-openai", type=int, default=1500, help="hard ceiling on OpenAI requests per run")
    ap.add_argument("--budget", type=float, default=3.75, help="max OpenAI USD across all runs (split mini 69%% / adjudicator 31%%); spend is kept in sft/spend.json")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    args.teachers = [t for t in args.teachers.split(",") if t]
    CALL_CAPS.update(cerebras=args.max_calls_cerebras, openai=args.max_calls_openai)
    if os.environ.get("CEREBRAS_TRAINING_API_KEY"):  # keep the live app's key out of bulk jobs (this process only)
        os.environ["CEREBRAS_API_KEY"] = os.environ["CEREBRAS_TRAINING_API_KEY"]
    OUT.mkdir(parents=True, exist_ok=True)
    for st in (["generate", "facts", "label", "export"] if args.stage == "all" else [args.stage]):
        if HALT.is_set():
            sys.exit(2)
        globals()[f"stage_{st}"](args)
    print(f"requests this run: {CALLS}")
