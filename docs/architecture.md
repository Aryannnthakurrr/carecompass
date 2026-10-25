# How CareCompass works

CareCompass turns a message in English, Hindi or Hinglish into either one short question or a care decision with matched
providers. Every turn passes through layers: deterministic rules, a language model that reads the conversation, MedGemma
as the clinical screen, and scoring code that picks providers. Each layer can make the system safer or ask; none can talk
it out of an emergency. Numbers in square brackets refer to [evaluation.md](evaluation.md).

## The app and the server

The iPhone app (`ios/`, SwiftUI) is the client; a laptop runs the server. The phone sends each turn over HTTP on the same
Wi-Fi. The laptop runs the whole pipeline and MedGemma through Ollama, and calls out only for speech (Sarvam), the language
model (Qwen 3.8 27B, Cerebras API today, open weights) and the ambulance route (OSRM). The web demo (`frontend/`) uses the
same API. The client resends the whole conversation each turn, so the server keeps no session and any number of phones can
share it.

## Model roles

| Part | Does | Never |
|---|---|---|
| **Qwen 3.8 27B** | Reads three languages into English facts (`CaseFacts`), reads the message itself, picks the care type, words questions and replies | Writes emergency text or undoes an emergency |
| **MedGemma 4B** (local GPU) | Grades danger from a clinical note: emergency now, see a doctor today, or routine, naming the danger signs | Sees the user's words or talks to the user |
| **Sarvam** | Saaras v4 transcribes in the original words, so "seene mein dard" reaches the rules intact; Bulbul v3 speaks the reply | Translates (text translators turned chest pain into "pain in the side" [1]) |
| **Plain code** | Red-flag rules, gate, protocol cards, combining votes, matching, the scrub and every emergency card | Generates: same input, same answer |

## The nine layers (`app/pipeline.py`)

| # | Layer | What it does |
|---|---|---|
| 1 | Red-flag rules (`redflags.py`) | About 80 pattern groups in English, Hinglish and Devanagari on the raw words; denials scope over their clause, past history is excluded; event rules for drowning, electric shock, snake bite, choking, severe burns. Median 0.9 ms [4] |
| 2 | Intake analyst (`intake.py`) | Qwen turns the whole conversation into English facts plus a reading of the message: `message_check` (ok, unclear, off_topic, nonsense, abusive, mixed_up, contradictory), `not_literal`, `user_wants`, `no_more_questions`, `asks_medicine_or_diagnosis`, `age_group`. Taught by worked examples, not keyword lists |
| 3 | Rules again | On the English symptoms; a raw red flag stands unless the analyst marks it figurative, which only chest pain and breathlessness can be, and a lifted flag gets one safety question |
| 4 | Gate | Turns the analyst's reading into fixed replies (table below) |
| 5 | Information gathering (`protocols.py`) | A symptom complaint needs who and how old, since when, and how bad, from per-complaint protocol cards. Two facts per question, at most two questions, never a repeat |
| 6 | MedGemma screen (`screen.py`, `retrieval.py`) | Clinical note plus the four most similar graded training cases; danger signs must match reported facts; "emergency now" asks one check question beside the call-112 line, then escalates |
| 7 | Care decision (`triage.py`, `smart.py`) | Qwen with the facts, the questions already asked and the protocol card. Dataset mode: organiser care type and urgency. Smart mode: seriousness, setting, specialty, first tests |
| 8 | Matching and explanation | Weighted ranking; a 1–2 sentence reply in the user's language; reasons and the place sentence from templates |
| 9 | Scrub (`explain.scrub`) | Removes disease and medicine names in English, Hindi and Hinglish from every line shown |

## One request, step by step

1. The phone sends `POST /triage` with the whole history.
2. Red-flag rules on the raw words (0.9 ms median [4]). A hit returns the pre-written emergency card.
3. The intake analyst writes English facts (Qwen, 0.45 s median per call [1]).
4. The gate and information gathering may return one question, worded by Qwen in the user's language and script (0.38 s [1]).
5. When a decision is near, MedGemma's screen (6.5 s median on the laptop GPU [3]) and Qwen's care decision run in parallel.
6. Votes combine (any emergency wins), then matching, explanation and scrub produce the JSON.

An emergency returns in about 1 s end to end; a full decision turn took 9–13 s in our role-play runs, mostly the local
MedGemma call on a 6 GB laptop GPU [7].

## The gate and the care decision

| The analyst reads the message as | CareCompass replies |
|---|---|
| off-topic, nonsense, abuse | a fixed, polite redirect; the conversation closes after three such turns (`conversation_ended`) |
| a mis-heard voice word | "Did you say …?" |
| mixed up or contradictory | one clarifying question per conversation |
| asking for medicine or a diagnosis | a fixed "we don't advise medicines" line, then routing |
| "no more questions" | decides now, with the call-112 line |
| a real health request | on to information gathering |

How the votes combine: any emergency (rules, analyst, confirmed screen, care decision) wins; "see a doctor today" raises
urgency to same day; a danger sign nobody reported is ignored; a model that fails or returns invalid JSON leaves the others
to decide (every model down: keyword routing in smart mode, a Medic home visit in dataset mode).

## MedGemma: how we found its job

We kept the model and the facts fixed and changed only how we asked, on the same 133 held-out cases [2][3]:

| How MedGemma 4B was asked | Emergencies caught | False alarms |
|---|---|---|
| Facts as JSON, pick one of six care labels | 37 / 77 | 0 / 56 |
| Facts as JSON, reason first | 40 / 77 | 0 / 56 |
| Clinical note, reason first | 39 / 77 | 0 / 56 |
| Clinical note, one question: emergency, yes or no? | 73 / 77 | 14 / 56 |
| Chain: rules + Qwen, no MedGemma | 66 / 77 | 0 / 56 |
| Chain + graded screen | 72 / 77 | 1 / 56 |
| **Chain + graded screen + 4 retrieved cases (live)** | **74 / 77** | **2 / 56** |

Asked to choose among labels, it plays safe and under-calls; asked one focused clinical question, it sees the danger.
Example from the held-out split: "mother, 68: vomiting once; acting strange; asking for a BP check" fires no rule, and
MedGemma grades it emergency now, naming "acting strange in an elderly patient".

Why a 4B model: it runs 4-bit on a 6 GB laptop GPU, so patient facts stay on our machine; it never reads the user's words,
only a note our code writes, so jokes and prompt injection cannot reach it; and we own the weights, so a recall-first LoRA
fine-tune on our labels is the planned next step ([medgemma-finetune.md](medgemma-finetune.md)). A frontier model scored a
little higher on care type in our benchmark (macro-F1 0.961 vs 0.928, same 11/11 emergencies) but was 4× slower per call,
costs more and cannot be self-hosted or retrained by us [1][9].

## Emergencies

An emergency can be raised by the rules on the raw words or on the facts, by MedGemma's confirmed screen, by the care
decision, or by the SOS button. The response is pre-written, never generated (`app/emergency_text.py`): one card per red
flag in English, Hindi and Hinglish, with "Call 112" and "Call 108" and two or three steps (bleeding: press firmly on the
wound; seizure: nothing in the mouth; infant fever; stroke; chest pain; snake bite; choking; unconsciousness; suicidal
thoughts with the Tele-MANAS 14416 line). Matching is skipped. SOS opens a 10-second countdown, a simulated dispatch with a
crew brief and a live ambulance map; no login is ever needed.

## Matching

| | Smart mode (default, the apps) | Dataset mode (`"mode": "dataset"`, organiser grading) |
|---|---|---|
| Providers | 186 synthetic providers (`data/providers.csv`): hospitals, clinics, specialists, labs, home care | organiser roster of 60 |
| Scoring | hard filters for anything named, a weight profile by need (rare, test, procedure, consult, home, video), the user's words shift weights ("best", "nearby", "cheap", "today", "lady doctor"), language as a multiplier | role from care type, skill from the words, distance, language, rating; top-3 overlap with labelled matches 195/198 [5] |
| Explanation | factor chips, a "picked over" line, and a summary of how the shortlist was formed | template reason |

Real picks from `eval/curated_queries.py` [8]: "need a dermatologist" picks a 4.0-star hospital 0.3 km away; "need the
best dermatologist, last one was useless" picks a 4.6-star hospital 6.3 km away; "need a cheap ultrasound" moves from ₹600
to ₹300; "I need an MRI" filters to the 13 of 172 providers that have one.

## API

`POST /triage` follows the organiser schema; the response is a follow-up question (decision fields null), a decision, or an
emergency. Optional extras, all backwards compatible:

| Field | Meaning |
|---|---|
| request `mode` | `smart` (default, used by the apps) or `dataset` (organiser taxonomy and roster, for grading) |
| request `input_mode` | `voice` when `user_input` is a transcript (enables "did you say …?") |
| history turn `kind` | echo the reply's `follow_up_kind`, so only medical questions count toward the budget |
| `follow_up_kind` | `medical`, `safety_check`, `clarify`, `redirect` or `misheard` |
| `safety_note` | the call-112 line shown with a safety check, or with a decision made while facts were missing |
| `conversation_ended` | repeated non-health messages: closed politely |
| `emergency` | the pre-written card: title, numbers to call, steps |
| smart mode | `seriousness`, `specialty`, `recommended_tests`, `match_summary`, per-provider `factors`, `fee_inr` |

## Privacy and cost

| Setup | Where patient data goes | Per 3-turn conversation [9] |
|---|---|---|
| Pilot (today) | speech and Qwen by API; MedGemma, rules, retrieval and matching on our laptop | ₹2.02 voice, ₹0.87 text |
| Self-hosted at scale | Qwen and MedGemma on two L40S GPUs in India; only speech leaves | models ₹0.17–0.22 above about 2 lakh conversations a month [10] |
| Frontier model end to end | every message to a third party | ₹3.32 voice |

## How the architecture evolved

| Stage | Challenge | Solution |
|---|---|---|
| One model decides | MedGemma read Hinglish poorly (5/11 emergencies on raw text) [1] | A language model writes English facts; MedGemma judges facts only |
| Two tiers | Picking six care labels caught 37/77 held-out emergencies [3] | Ask the danger question on a clinical note |
| Screener | 73/77 but 14/56 false alarms [3] | Three grades, retrieval of similar graded cases, a grounding check: 74/77, 2/56 [2] |
| Real conversations | Jokes, junk, mixed patients, invented diseases, medicine requests, mis-heard voice | The intake analyst and the gate, found and tested by LLM role-play of patients and relatives |
| Thin facts | "I have fever" routed without asking how high or how old | Protocol cards ask the facts that change the decision |
| Unsafe replies | Generated text could name a disease or medicine | Pre-written emergency cards, fixed lines, a three-language scrub |

**Red-teaming.** We used an LLM to role-play patients and relatives (rude, joking, confused, multilingual, trying prompt
injection) and a second pass to review each transcript like a strict clinical reviewer. Each finding was fixed in the
layer it belonged to and became a regression test: 47 scripted conversations [6] and 85 rule checks [4].
