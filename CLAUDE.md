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
| Speech → text | Sarvam **Saaras v4** `transcribe` only (original words) | Sarvam API | ~0.4 s, Rs0.49/min. `translate` dropped: Qwen reads Devanagari/Hinglish directly. Sarvam text translation (Mayura) rejected: mistranslated typed "seene mein dard" |
| Structurer + conversation voice (facts JSON, follow-up phrasing, explanation) | **Qwen 3.8 27B** (open weights; fallback Gemini 3.5 Flash-Lite) | Cerebras API | `eval/bench_v2.py`: all facts 30/30, 11/11 red flags, 0 denial leaks, 0.45 s; follow-ups right script 17/17 at 0.38 s with explicit target language |
| Triage classifier (facts only → status/care/urgency/missing_info) | Red-flag rules → **MedGemma 4B** (local, LoRA to come) **+ Qwen 3.8 27B second opinion, more urgent wins** | Ollama + Cerebras | Pair: 11/11 emergencies, 0 under-triaged, 0 false emergencies, macro-F1 0.93/0.88. MedGemma on raw text was 5/11 — it must never see raw language |
| Caregiver matching | Weighted scoring in Python | Local CPU | Deterministic, explainable |
| Plain-language explanation | Qwen 3.8 27B (same as conversation) | Cerebras API | Replies in the detected language, passed explicitly |
| Text → speech | Sarvam **Bulbul v3** | Sarvam API | Natural Indian voices, handles Hinglish |
| Fallbacks | Flash-Lite (conversation); Qwen alone (classifier, if MedGemma output invalid) | Gemini / Cerebras | Different provider |

Model rationale + benchmark numbers: `eval/results/*.json`, `python eval/bench_pipeline.py <part>`. Rationale doc (shareable): https://claude.ai/artifact/JY6yW4KHZ4K37TVbZKzBUZ

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

1. **Structurer (`intake.py`)**: Qwen 3.8 27B → `CaseFacts` JSON (symptoms_present/denied, onset, duration, age, special_population, existing_conditions, vitals_reported, services_requested, severity, detected_language). Records facts only: no decisions, no questions. Re-reads the whole `conversation_history` each turn (stateless API).
2. **Red flags (`redflags.py`)**: run on BOTH raw transcript and structured symptoms. Any hit → emergency path immediately, skip matching. Starter list: chest pain / seene mein dard · difficulty breathing / saans lene mein taklif / saans nahi aa rahi · unconscious / behosh · seizure / daura / jhatke · stroke signs (muh tedha, haath-pair sunn, bolne mein dikkat) · heavy bleeding / bahut khoon · severe head injury · poisoning / zeher · face or throat swelling · blue lips · sudden confusion · fever in infant < 3 months · bleeding in pregnancy · suicidal thoughts.
3. **Classifier (`triage.py`)**: MedGemma + Qwen in parallel on CaseFacts only → `{status: final|need_more_info, care_type, urgency, red_flags, missing_info (≤2 plain questions), reasoning}`. Either says emergency → emergency; else higher urgency wins; invalid output → the other decides. Emergencies never ask. Then Qwen phrases `missing_info` into one question in the detected language (pass it explicitly; Hinglish returns romanised for screen + Devanagari for Bulbul). Hard cap 4 follow-ups.
4. **Matching (`matching.py`)**: filter by skill and availability within urgency window; score = weighted sum of distance (haversine), language match, rating, soonest slot (0.35/0.25/0.2/0.2). Reasons from templates, not the LLM.
5. **Explain (`explain.py`)**: Qwen → 1–2 plain sentences in the detected language. Hinglish: Devanagari copy for Bulbul, romanised on screen. Emergency audio pre-generated at startup.
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

## Cost (per 3-turn conversation, Oct 2026 prices)

Voice ~Rs1.72 (Qwen Rs0.57, MedGemma local Rs0, Saaras transcribe Rs0.25, Bulbul Rs0.90); text-only ~Rs0.57. Details in the rationale doc.
