# CareCompass

A multilingual (English / Hindi / Hinglish), voice-first care-navigation **app**. A patient or family member describes
the problem in their own words, by voice or text; CareCompass asks only the questions that matter, decides the type and
urgency of care, escalates emergencies with clear first-aid steps, and ranks the best caregivers nearby.

Built for HACK IIITD — CAI, IIIT-Delhi ("AI for Healthcare Access").

> Routing support only. CareCompass never diagnoses or prescribes. In an emergency, call **112** (national emergency) or
> **108** (ambulance).

## How it runs

```
iPhone app (SwiftUI)  ──HTTP──►  CareCompass server on a laptop  ──►  Sarvam (speech), Cerebras (Qwen)
web demo (browser)    ──HTTP──►  FastAPI + MedGemma via Ollama (local GPU)
```

- **Client:** the native iOS app in `ios/` (chat by voice or text, follow-up chips, care plan, booking, one-tap emergency
  with a live ambulance map). A browser demo with the same features lives in `frontend/`.
- **Server:** a laptop runs the FastAPI backend and MedGemma 4B locally through Ollama. Clinical judgement (MedGemma) never
  leaves the machine; the language model is behind an API today and can be self-hosted (see "Privacy and cost").

## The pipeline

Every turn goes through layers. Each layer can only make the system safer or ask a question; none can talk the system out
of an emergency.

| # | Layer | What it does | Runs on |
|---|---|---|---|
| 1 | **Red-flag rules** | Deterministic patterns in English, Hinglish and Devanagari for chest pain, breathing trouble, collapse, stroke signs, seizures, heavy bleeding, infant fever, suicidal thoughts, poisoning, choking, drowning, snake bite and more. They understand denials ("seene mein dard nahi") and figures of speech | CPU, instant |
| 2 | **Intake analyst** (Qwen 3.8 27B) | Turns the conversation into English facts and reads it: is this a real health request, off-topic, abusive, mis-heard, mixed up or contradictory; what does the user want; is "heart attack" a figure of speech; do they refuse more questions | Cerebras API |
| 3 | **Gate** | Off-topic, nonsense and abuse get a fixed, polite redirect to the health problem (never entertained, and the conversation closes after repeated junk). Mis-heard voice gets "did you say …?". A confusing message gets one clarifying question | CPU |
| 4 | **Information gathering** | A symptom complaint is asked who / how old, since when, and how bad, using per-complaint protocol cards (fever: how high; bleeding: does it stop with pressure; accident: what happened, head hit; vomiting: keeping water down). At most two short questions, never a repeat, and "just send a doctor" is respected | CPU + Qwen phrasing |
| 5 | **MedGemma clinical screen** (the core) | MedGemma 4B grades the case from a short clinical note: emergency now / see a doctor today / routine, guided by the most similar graded cases from our labelled dataset (retrieval). An "emergency now" is confirmed with one targeted question and a call-112 line, then escalated | **Local GPU** |
| 6 | **Care decision** | Organiser taxonomy (dataset mode) or seriousness + specialty + first tests (smart mode) | Qwen |
| 7 | **Matching + explanation** | Weighted, explainable caregiver ranking; the reply in the user's language, with the place sentence built from provider data, never invented | CPU + Qwen |

Emergencies are answered with **pre-written** cards per red flag (bleeding: press the wound; seizure: nothing in the mouth;
infant fever: keep feeding; snake bite: don't cut or tie; suicidal thoughts: warm words and the Tele-MANAS 14416 line),
never generated text. Every line shown to the user is checked for disease and medicine names in English, Hindi and Hinglish.

## Why MedGemma is the hero

MedGemma makes the decision that matters most: **how dangerous is this, right now?** Asked as a focused clinical question on
a clinical note, with retrieved similar cases, it lifts emergency recall on held-out cases from 66/77 (rules + Qwen) to
**74/77**, with false alarms on 2/56 non-emergencies, and its "yes" is confirmed with the family before escalating. It runs
locally, so the clinical judgement on patient facts stays on our hardware. A recall-first LoRA fine-tune (Unsloth QLoRA on
Kaggle) is in `training/`.

## Models and APIs

| Stage | Model | Provider |
|---|---|---|
| Speech → text | Sarvam Saaras v4 (transcribe) | Sarvam API |
| Intake analyst, care decision, replies | Qwen 3.8 27B (open weights; fallback Gemini 3.5 Flash-Lite) | Cerebras API |
| Clinical screen | MedGemma 4B (Google Health AI Developer Foundations terms), 4-bit GGUF | Ollama, local GPU |
| Similar-case retrieval | TF-IDF over the labelled training split | Local CPU (scikit-learn) |
| Text → speech | Sarvam Bulbul v3 | Sarvam API |

All model names, endpoints, labels and weights live in `config.yaml`.

## Setup (laptop server, Windows / PowerShell)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env    # then fill in SARVAM_API_KEY, CEREBRAS_API_KEY and GEMINI_API_KEY
ollama pull hf.co/unsloth/medgemma-4b-it-GGUF:Q4_K_M
```

## Run

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000     # server, reachable from the phone on the same Wi-Fi
python -m http.server 5173 -d frontend               # optional browser demo at http://localhost:5173
```

**iPhone:** build the app from `ios/` (see `ios/README.md`), then in the app open Appointments → Server and enter
`http://<laptop-ip>:8000` (`ipconfig` shows the laptop's address). The iOS simulator reaches `http://localhost:8000` directly.

If Ollama is not running, the server keeps working with Qwen alone; the MedGemma screen simply adds no vote.

## API

`POST /triage` follows the organiser contract. Send the whole conversation each turn; the response is either a
`follow_up_question` (decision fields `null`) or a decision:

```json
{ "user_input": "papa ko do din se bukhar hai aur khansi bhi", "language": "auto",
  "location": { "lat": 28.54, "lng": 77.27 }, "conversation_history": [] }
```

Labels follow the organiser taxonomy: `care_type` is `paramedic_home_visit`, `home_nursing`, `lab_test_home`,
`teleconsultation` or `emergency_112`; `urgency` is `emergency`, `same_day` or `scheduled`.

Optional extras, all backwards compatible:

| Field | Meaning |
|---|---|
| request `mode` | `dataset` (default, organiser roster) or `smart` (186-provider directory, dynamic matching) |
| request `input_mode` | `voice` when `user_input` is a speech transcript (the analyst checks for mis-heard words) |
| history turn `kind` | echo back the reply's `follow_up_kind`, so only medical questions count toward the question budget |
| `follow_up_kind` | `medical`, `safety_check`, `clarify`, `redirect` or `misheard` |
| `safety_note` | the call-112 line shown with a safety check, or with a decision made while facts were missing |
| `conversation_ended` | repeated non-health messages: closed politely; `explanation` holds the closing line |
| `emergency` | the pre-written card: title, numbers to call, first-aid steps |
| `explanation`, `language`, caregiver `name`, `role`, `distance_km`, `languages`, `rating`, `next_slot` | display extras |

Other endpoints: `/stt`, `/tts`, `/caregivers` (directory), `/emergency/dispatch` and tracking, `/auth/*`, `/me/*`.

## Two matching modes

| | Dataset mode | Smart mode |
|---|---|---|
| Providers | `training/caregivers.csv` (organiser roster, 60) | `data/providers.csv` (186: hospitals, clinics, specialists, diagnostic centres, labs, home care) |
| Decision | organiser care type + urgency | seriousness, care setting, specialty, first tests, procedures |
| Matching | organiser reference blend | hard filters on what was asked for (a named scan, the right specialist, a lady doctor), weights that shift with the need and the user's wishes (cheap, nearby, today) |
| Explanation | template reason | factor chips, "why this over the next one", and how the shortlist was chosen |

## Privacy and cost

- **Today (pilot):** speech and the language model are API calls; MedGemma, rules, retrieval and matching run on our own
  machine. A 3-turn voice conversation costs about ₹2, a text conversation under ₹1.
- **At scale:** both models have open weights. Self-hosting Qwen and MedGemma on two rented L40S GPUs keeps every patient
  message on our infrastructure and brings the language-model cost to roughly ₹0.2 per conversation above about two lakh
  conversations a month.
- **Versus a frontier model end to end:** a frontier model scored slightly higher on care type in our benchmark (0.96 vs
  0.93 macro-F1) but was about 4× slower and roughly 10× the cost per conversation, and it cannot be self-hosted. The
  layered design gets its safety from rules and MedGemma rather than from model size.

## Evaluation

```powershell
python eval/test_redflags.py        # offline: 85 rule checks + coverage on the labelled dataset (0 false alarms)
python eval/eval_smart.py           # offline: smart-mode matching and wording checks
python eval/eval_screener.py        # local GPU: MedGemma screen on 133 held-out cases
python eval/smoke_budget.py         # small capped live check: risky conversations + organiser sample
python eval/eval_conversations.py   # full scripted suite: adversarial, multilingual, emergencies (47 conversations)
python eval/eval_dataset.py         # organiser dataset, writes eval/results/predictions.csv
```

## Repository

```
app/        FastAPI server: pipeline.py (layers), intake.py (analyst), redflags.py, screen.py + retrieval.py (MedGemma),
            protocols.py (complaint cards), triage.py / smart.py (care decision), matching.py / smart_match.py,
            explain.py (replies, scrub), emergency_text.py (pre-written cards)
ios/        native iPhone client (SwiftUI)
frontend/   browser demo
training/   labelled dataset, recall-first review, MedGemma QLoRA notebook (Kaggle), runbook
eval/       evaluation scripts and results
data/       provider directory builder
```

## Data

Sanocare synthetic care-triage dataset v1 (2026), provided for the IIIT-Delhi hackathon (`training/`). The provider
directory and all training conversations are synthetic; no real patient data is used.
