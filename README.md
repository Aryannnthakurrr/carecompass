# CareCompass

A multilingual (English / Hindi / Hinglish), voice-first care-navigation **app**. A patient or family member describes
the problem in their own words, by voice or text; CareCompass asks only the questions that matter, decides the type and
urgency of care, escalates emergencies with clear first-aid steps, and ranks the best caregivers nearby.

Built for HACK IIITD — CAI, IIIT-Delhi ("AI for Healthcare Access").

> Routing support only. CareCompass never diagnoses or prescribes. In an emergency, call **112** (national emergency) or
> **108** (ambulance).

```
iPhone app (SwiftUI)  ──HTTP──►  CareCompass server on a laptop  ──►  Sarvam (speech), Qwen 3.8 27B (API)
web demo (browser)    ──HTTP──►  FastAPI pipeline + MedGemma 4B via Ollama (local GPU)
```

Every turn passes through layers: red-flag rules, an intake analyst (Qwen), a gate, protocol-card questions, the
**MedGemma clinical screen** and the care decision, then explainable matching. Each layer can escalate; none can talk the
system out of an emergency. On 133 held-out cases MedGemma lifts emergencies caught from 66 to **74 of 77**
(2/56 false alarms). Full story: [docs/architecture.md](docs/architecture.md).

## Documentation

Everything lives in [docs/](docs/README.md):

| | |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Layers, request flow, model roles, gate, emergency path, matching, privacy and cost |
| [docs/evaluation.md](docs/evaluation.md) | Every measured claim, the data behind it, and the script that reproduces it |
| [docs/ios-app.md](docs/ios-app.md) | Building the iPhone app and pointing it at the laptop server |
| [docs/medgemma-finetune.md](docs/medgemma-finetune.md) | Dataset pipeline and the planned MedGemma LoRA fine-tune |

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

**iPhone:** build the app from `ios/` ([docs/ios-app.md](docs/ios-app.md)), then open Appointments → Server and enter
`http://<laptop-ip>:8000` (`ipconfig` shows the laptop's address). If Ollama is not running, the server keeps working with
Qwen alone; the MedGemma screen simply adds no vote.

## API

`POST /triage` follows the organiser contract. Send the whole conversation each turn; the response is either a
`follow_up_question` (decision fields `null`) or a decision:

```json
{ "user_input": "papa ko do din se bukhar hai aur khansi bhi", "language": "auto",
  "location": { "lat": 28.54, "lng": 77.27 }, "conversation_history": [] }
```

`care_type` is `paramedic_home_visit`, `home_nursing`, `lab_test_home`, `teleconsultation` or `emergency_112`;
`urgency` is `emergency`, `same_day` or `scheduled`. Optional extras (`mode`, `input_mode`, `follow_up_kind`,
`safety_note`, `conversation_ended`, `emergency`, smart-mode factors) are listed in
[docs/architecture.md](docs/architecture.md#api). Other endpoints: `/stt`, `/tts`, `/caregivers`, `/emergency/dispatch`
and tracking, `/auth/*`, `/me/*`.

## Quick checks

```powershell
python eval/test_redflags.py        # offline: 85 rule checks + coverage on the labelled dataset
python eval/curated_queries.py      # offline: smart-mode matching on curated queries
python eval/eval_screener.py        # local GPU: MedGemma screen on 133 held-out cases
```

All commands and results: [docs/evaluation.md](docs/evaluation.md).

## Repository

```
app/        FastAPI server (pipeline, rules, intake, MedGemma screen, matching, replies)
ios/        native iPhone client (SwiftUI)
frontend/   browser demo
docs/       all documentation
training/   labelled dataset builder and the MedGemma LoRA notebook (planned)
eval/       evaluation scripts and results
data/       provider directory builder
```

Data: Sanocare synthetic care-triage dataset v1 (2026), provided for the IIIT-Delhi hackathon. The provider directory and
all training conversations are synthetic; no real patient data is used.
