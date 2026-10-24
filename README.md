# CareCompass

Multilingual (English / Hindi / Hinglish), voice-first care navigation: understands a patient's request, asks a few follow-up questions, decides care type and urgency, escalates emergencies, and ranks the best caregivers.

Built for HACK IIITD — CAI, IIIT-Delhi ("AI for Healthcare Access").

> Routing support only — CareCompass does not diagnose or prescribe. In an emergency, call **112** (national emergency) or **108** (ambulance).

## Setup (Windows / PowerShell)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # then fill in SARVAM_API_KEY, CEREBRAS_API_KEY and GEMINI_API_KEY
```

Local models (Ollama, https://ollama.com):

```powershell
ollama pull hf.co/unsloth/medgemma-4b-it-GGUF:Q4_K_M
```

## Models and APIs used

| Stage | Model | Provider |
|---|---|---|
| Speech → text | Sarvam Saaras v4 | Sarvam API |
| Structurer, follow-ups, explanation | Qwen 3.8 27B (fallback Gemini 3.5 Flash-Lite) | Cerebras API |
| Triage | Red-flag rules + MedGemma 4B (local) with Qwen 3.8 27B second opinion | Ollama + Cerebras |
| Text → speech | Sarvam Bulbul v3 | Sarvam API |


## Run

```powershell
uvicorn app.main:app --port 8000          # API: POST /triage, /stt, /tts
python -m http.server 5173 -d frontend    # demo UI at http://localhost:5173
```

MedGemma runs through a local Ollama. If Ollama is not running, Qwen classifies alone and the API keeps working.

## API

`POST /triage` follows the organiser contract. Send the whole conversation each turn; the response is either a
`follow_up_question` (decision fields `null`) or a decision:

```json
{ "user_input": "papa ko do din se bukhar hai aur khansi bhi", "language": "auto",
  "location": { "lat": 28.54, "lng": 77.27 }, "conversation_history": [] }
```

Labels follow the organiser taxonomy: `care_type` is `paramedic_home_visit`, `home_nursing`, `lab_test_home`,
`teleconsultation` or `emergency_112`; `urgency` is `emergency`, `same_day` or `scheduled`. Optional extras:
`language`, `explanation` (in the user's language), `emergency` (pre-written 112/108 steps) and caregiver
`name`, `role`, `distance_km`, `languages`, `rating`, `next_slot`.

Pipeline: red-flag rules on the raw words → Qwen 3.8 27B structures the conversation into English facts → MedGemma 4B
and Qwen classify the facts in parallel (more urgent answer wins) → caregiver matching on `training/caregivers.csv`
→ explanation in the user's language.

## Two matching modes

`POST /triage` takes an optional `"mode"`. Without it the API stays in **dataset** mode, so the organiser contract and
auto-scoring are untouched. The UI has a Dataset / Smart toggle.

| | Dataset mode | Smart mode |
|---|---|---|
| Providers | `training/caregivers.csv` (organiser roster, 60) | `data/providers.csv` (186: the roster plus hospitals, clinics, specialists, diagnostic centres, labs) |
| Classifier output | organiser care type + urgency | seriousness, care setting, specialty, first tests a doctor would order, procedures needed |
| Matching | organiser reference blend, tuned to their labels | dynamic: hard filters on what was asked for, weights that shift with the kind of need and the user's own words |
| Explanation | template reason | factor chips per provider, "why this over the next one", and how the shortlist was chosen |

Smart matching in short (`app/smart_match.py`, vocabulary in `app/lexicon.py`):
- Named tests and procedures are hard requirements (an MRI request only matches places with MRI).
- A rare capability (offered by under 12% of providers) leaves few candidates, so rating and reach decide among them; a common
  test lets rating and distance decide; a quick procedure (tetanus shot, dressing) goes to the nearest open place.
- A consultation weighs a shared language heavily; for a quick procedure a language mismatch costs little, and the
  trade-off is spelled out ("fine for a quick tetanus shot; the nearest Hindi-speaking clinic is 2.4 km away").
- Symptom-led cases favour one-stop places that can also run the tests a doctor may ask for; "just a consultation"
  turns those tests into information instead.
- Words like "best", "nearby", "cheap", "today" and "lady doctor" raise the matching weight.
- Unclear severity (for example "I am bleeding") gets a follow-up about source, amount and cause; uncontrolled bleeding
  and other red flags still go straight to 112 at any step.

Regenerate the provider directory with `python data/build_providers.py`. Check smart-mode behaviour with
`python eval/eval_smart.py` (add `--e2e` to include full conversations through the models).

## Evaluation

```powershell
python eval/eval_dataset.py      # scores /triage on training/triage_dataset.jsonl, writes eval/results/predictions.csv
```

On the organiser's 340 training examples: care-type and urgency macro-F1 1.0, emergencies 56/56, no false
emergencies, follow-ups asked on exactly the 39 vague requests, caregiver top-3 overlap with the labelled matches 204/209.
The dataset is templated (about 66 distinct messages), so these numbers confirm the wiring rather than generalisation.

## Data

Sanocare synthetic care-triage dataset v1 (2026), provided for the IIIT-Delhi hackathon (`training/`).
