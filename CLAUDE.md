# CareCompass — Project Brief for Claude Code

Hackathon: HACK IIITD, CAI (IIIT-Delhi), "AI for Healthcare Access" track (24 hours).
Goal: a multilingual (English / Hindi / Hinglish), voice-first system that takes a patient's request, asks 2–4 follow-up questions, decides care type and urgency, flags emergencies, and ranks the best caregivers — exposed as a REST API plus a simple demo UI.

Problem statement PDF: `docs/CareCompass_CAI_Problem_Statement_Final.pdf`.

## How to work on this project

- Commit progress frequently (organisers ask for commits throughout the hackathon). Small, descriptive commits.
- Never write API keys or tokens into code or this file. They live in `.env` only, which the user fills in themselves.
- Anything marked **VERIFY** is a name, version or parameter that may have changed. Check the official docs or Hugging Face before relying on it.
- Ask before downloading anything larger than 5 GB.
- Windows machine: PowerShell for shell commands; venv at `.venv\Scripts\Activate.ps1`.

## Hardware

- NVIDIA RTX 4050 laptop GPU, **6 GB VRAM**.
- Only ONE ~4B model at 4-bit fits on the GPU at a time. Everything else runs on the CPU or behind an API.
- LoRA training does NOT happen on this laptop. It runs on free Kaggle or Colab GPUs (see "Training").

## Model decisions (final)

| Stage | Model | Where it runs | Why |
|---|---|---|---|
| Speech → text | Sarvam **Saaras v4**: `transcribe` (original words) + `translate` (English) | Sarvam API | ~0.5 s each; voice needs no LLM for translation. Sarvam text translation (Mayura) rejected: mistranslated typed "seene mein dard" |
| Text → structured English + next follow-up question | **Gemini 3.5 Flash-Lite** with enforced JSON schema (fallback: Gemma 4 26B-A4B) | Gemini API | Fastest with best schema adherence in `eval/bench_intake.py`; Gemma 4 ~3.4s, Sarvam-105b ~10s, MedGemma hallucinated symptoms |
| Triage (care type + urgency) | Red-flag rules → **Gemma 4 26B-A4B** (provisional; optional Flash-Lite second opinion, more urgent wins) | Gemini API | Untrained: 29/30 care, 11/11 emergencies. MedGemma 4B untrained: 5/11 emergencies. Final pick on organiser validation split (also try fine-tuned MuRIL/XLM-R, MedGemma LoRA) |
| Caregiver matching | Weighted scoring in Python | Local CPU | Deterministic, explainable |
| Plain-language explanation | Gemini 3.5 Flash-Lite (fallback Gemma 4 26B-A4B) | Google AI Studio | Replies in the user's language |
| Text → speech | Sarvam **Bulbul v3** | Sarvam API | Natural Indian voices, handles Hinglish |
| Fallback LLM | Gemma 4 26B-A4B (API), then MedGemma 4B (local, offline last resort) | Gemini API / Ollama | If Flash-Lite fails or times out |

Model rationale + benchmark numbers: `eval/results/*.json`, `python eval/bench_pipeline.py <part>`. Free-tier limits: gemini-3.8-flash 20 RPD; Flash-Lite hits per-minute 429s under parallel load.

Saaras is speech-to-TEXT. Bulbul is text-to-SPEECH. MedGemma is under Google's Health AI Developer Foundations terms.

All model names / endpoints / labels / weights live in `config.yaml`.

## Folder layout

```
CLAUDE.md, README.md, .env (git-ignored), .env.example, requirements.txt, config.yaml
app/            main.py (FastAPI, POST /triage), schemas.py, intake.py, redflags.py,
                triage.py, matching.py, explain.py, speech.py, emergency_text.py
data/           organiser data (data/raw/ git-ignored)
docs/           problem statement, write-up
models/         GGUF files (git-ignored) + Ollama Modelfiles
training/       Kaggle/Colab notebook for MedGemma LoRA
eval/           macro-F1, emergency recall, latency
smoke_tests/    one script per external service
frontend/       demo UI
```

## API contract (indicative — final schema comes with the dataset)

Request:
```json
{
  "user_input": "papa ko do din se bukhar hai aur khansi bhi",
  "language": "auto",
  "location": { "lat": 28.54, "lng": 77.27 },
  "conversation_history": []
}
```
Response:
```json
{
  "follow_up_question": null,
  "care_type": "paramedic_home_visit",
  "urgency": "same_day",
  "red_flags": [],
  "reasoning": "Fever with cough for 2 days, no breathing difficulty reported",
  "matched_caregivers": [
    { "caregiver_id": "CG_014", "score": 0.92, "reason": "Hindi-speaking, 3 km away, free at 4 pm" }
  ]
}
```
- If more info is needed, return `follow_up_question` and leave decision fields null.
- Use the organisers' exact label strings once released.

## Pipeline logic

1. **Intake (`intake.py`)**: ONE Flash-Lite call with a Pydantic `response_schema` (symptoms in English, negated symptoms separate, follow-up in user's language/script) returns JSON: `{symptoms[], duration, patient_age, severity, missing_slots[], follow_up_question_in_user_language | null, detected_language}`. Structured output. Hard cap of 4 follow-up questions.
2. **Red flags (`redflags.py`)**: run on BOTH raw transcript and structured symptoms. Any hit → emergency path immediately, skip matching. Starter list: chest pain / seene mein dard · difficulty breathing / saans lene mein taklif / saans nahi aa rahi · unconscious / behosh · seizure / daura / jhatke · stroke signs (muh tedha, haath-pair sunn, bolne mein dikkat) · heavy bleeding / bahut khoon · severe head injury · poisoning / zeher · face or throat swelling · blue lips · sudden confusion · fever in infant < 3 months · bleeding in pregnancy · suicidal thoughts.
3. **Triage (`triage.py`)**: MedGemma classifies care type + urgency. Safety bias: if output invalid/uncertain, or rules and model disagree, pick the MORE urgent option.
4. **Matching (`matching.py`)**: filter by skill and availability within urgency window; score = weighted sum of distance (haversine), language match, rating, soonest slot (0.35/0.25/0.2/0.2). Reasons from templates, not the LLM.
5. **Explain (`explain.py`)**: Flash-Lite → 1–2 plain sentences in user's language. For speech, Hinglish in Devanagari; romanised on screen.
6. **Emergency (`emergency_text.py`)**: NEVER generated. Pre-written EN/HI/Hinglish with "Call 112" and "Call 108" plus 2–3 simple steps.

## Clinical scope rules
- Routing only. Never diagnose (no disease names), never prescribe.
- Every emergency shows a clear call-for-help path.
- Only synthetic data and cited public datasets. No real patient data.
- Declare every model and API used in the write-up.

## Training (MedGemma 4B LoRA) — Kaggle/Colab
- Unsloth QLoRA on free T4/P100. Input: structured symptom JSON. Output: `{"care_type", "urgency"}`.
- 20% held out. Report macro-F1 (care type, urgency) + emergency recall.
- Export merged Q4_K_M GGUF → `models/` → Ollama Modelfile → update `config.yaml`.
- Notebook: `training/medgemma_lora.ipynb`.

## Evaluation (scoring: Routing 30, Safety 20, Matching 20, UX 20, Explainability 10)
- Macro-F1 on care type and urgency.
- Recall on emergency/red-flag cases; under-triage penalised heavily. Track on EVERY change.
- Tie-breaks: fewest follow-ups, response time, ease of API integration.

## Deliverables checklist
- [ ] REST API following organiser JSON schema (most important)
- [ ] Demo UI: big mic button, answer chips, result card, red emergency screen, mocked one-tap booking
- [ ] Predictions CSV on hidden test set
- [ ] GitHub repo with README + setup; Docker preferred
- [ ] Write-up, 2 pages max
- [ ] Demo video ≤ 3 min: routine English → Hinglish voice with follow-up → emergency

## Open items
- Dataset, caregiver profiles, care taxonomy and final schema not released yet. Build against indicative schema; labels in `config.yaml`.
- Verified 2026-10-24: sarvamai 0.1.37 (STT `saaras:v3|v4`, TTS `bulbul:v2|v3|v4-flash`), Gemini API Gemma ids `gemma-4-31b-it`, `gemma-4-26b-a4b-it`. **VERIFY**: Gemma free-tier rate limits; whether Gemma accepts JSON mode (smoke test falls back).
