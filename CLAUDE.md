# CareCompass — Project Brief for Claude Code

Hackathon: HACK IIITD, CAI (IIIT-Delhi), "AI for Healthcare Access" track (24 hours).
Goal: a multilingual (English / Hindi / Hinglish), voice-first system that takes a patient's request, asks 2–4 follow-up questions, decides care type and urgency, flags emergencies, and ranks the best caregivers — exposed as a REST API plus a simple demo UI.

Problem statement PDF: `docs/CareCompass_CAI_Problem_Statement_Final.pdf`.

## How to work on this project

- Documentation lives in `docs/` (index: `docs/README.md`); the root README stays short. Keep both in sync with code changes.

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
| Clinical screen (how dangerous, now) | **MedGemma 4B** on a clinical note, graded emergency_now / see_doctor_today / routine, with retrieved similar cases | Ollama, local GPU | Held-out chain: rules + Qwen 66/77 emergencies → + MedGemma screen 74/77, 2/56 false alarms. As a 6-way classifier it was 37/77, so its job is the screen. It must never see raw language |
| Care decision (facts → care type / follow-up) | Red-flag rules + **Qwen 3.8 27B** (protocol cards, questions already asked) | Cerebras | Either emergency vote wins; recall-first LoRA of MedGemma in `training/` |
| Caregiver matching | Weighted scoring in Python | Local CPU | Deterministic, explainable |
| Plain-language explanation | Qwen 3.8 27B (same as conversation) | Cerebras API | Replies in the detected language, passed explicitly |
| Text → speech | Sarvam **Bulbul v3** | Sarvam API | Natural Indian voices, handles Hinglish |
| Fallbacks | Flash-Lite (conversation); Qwen alone (classifier, if MedGemma output invalid) | Gemini / Cerebras | Different provider |

Model rationale + benchmark numbers: `eval/results/*.json`, `python eval/bench_pipeline.py <part>`. Rationale doc (shareable): https://claude.ai/artifact/JY6yW4KHZ4K37TVbZKzBUZ · Architecture doc: https://claude.ai/artifact/RFsg9cCKFkiCwLHZejuSYd · MedGemma dataset & training doc: https://claude.ai/artifact/9YcKrftBNp5bK3UqyfAayN

Saaras is speech-to-TEXT. Bulbul is text-to-SPEECH. MedGemma is under Google's Health AI Developer Foundations terms.

All model names / endpoints / labels / weights live in `config.yaml`.

## Folder layout

```
CLAUDE.md, README.md, .env (git-ignored), .env.example, requirements.txt, config.yaml
app/            main.py (FastAPI), pipeline.py, schemas.py, intake.py, redflags.py, screen.py, retrieval.py,
                protocols.py, triage.py, smart.py, matching.py, smart_match.py, explain.py, speech.py, emergency_text.py
ios/            native iPhone client (SwiftUI; guide in docs/ios-app.md); the laptop runs the server
data/           organiser data (data/raw/ git-ignored)
docs/           ALL documentation: README (index), architecture.md, evaluation.md, ios-app.md,
                medgemma-finetune.md, problem statement
models/         GGUF files (git-ignored) + Ollama Modelfiles
training/       dataset builder + Kaggle notebook for the planned MedGemma LoRA (not run yet)
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

## Pipeline logic (`app/pipeline.py`, layered; each layer can only make the system safer or ask)

1. **Red flags (`redflags.py`)** on the raw words: EN / Hinglish / Devanagari, denials scoped to their clause, past history excluded, event rules (blast with injured people, drowning, electric shock, snake bite, choking, severe burns). `python eval/test_redflags.py` (85 offline checks + dataset coverage, 0 false alarms) must pass after any change.
2. **Intake analyst (`intake.py`, Qwen)**: whole conversation → `CaseFacts` (facts in English) + `message_check` (ok / unclear / off_topic / nonsense / abusive / mixed_up / contradictory), `check_notes`, `not_literal`, `user_wants`, `no_more_questions`, `asks_medicine_or_diagnosis`, `age_group`. Taught by worked examples, not keyword rules. Lenient validation: one odd value never voids a reading. A raw red-flag hit stands unless the analyst names it a figure of speech (only chest pain / breathlessness can be; self-harm, collapse, fits, stroke, bleeding, poisoning never).
3. **Rules again** on the English symptoms; a retracted red flag gets one safety check.
4. **Gate**: off-topic / nonsense / abuse → fixed redirect (`explain.REDIRECT*`); after 3 non-progress turns the conversation closes (`conversation_ended`). Voice mis-hearing → "did you say …?". Mixed-up / contradictory → one clarifying question per conversation.
5. **Information gathering**: a symptom complaint needs who / age (fever: a stated age), since when, and how bad (`protocols.KEY_ASK`: fever how high, bleeding stops with pressure, accident what happened / head hit …). Two facts per question, two questions, then decide. Named services ask nothing. Budgets count only medical / safety questions (`Turn.kind` echoed by clients, inferred otherwise). `config intake.ask_severity` switches the "how bad" question off.
6. **MedGemma clinical screen (`screen.py` + `retrieval.py`)**: clinical note → emergency_now / see_doctor_today / routine with the 4 most similar graded training cases. Danger signs must match reported findings; an emergency_now first asks its own check question with the pre-written call-112 line, then escalates. Chain on 133 held-out cases: rules + Qwen 66/77 → + screen 74/77 emergencies, 2/56 false alarms. `python eval/eval_screener.py`.
7. **Care decision**: dataset `triage.py` (Qwen, with questions already asked + protocol cards; MedGemma vote off) / smart `smart.py`. Either saying emergency → emergency.
8. **Matching + explanation**: templates for reasons and the place sentence; "we recommend", never "booked". `explain.scrub` removes disease / medicine names (EN, HI, Hinglish) from every shown line; the reply writer never sees raw user text.
9. **Emergency (`emergency_text.py`)**: NEVER generated. One pre-written card per red flag (bleeding, seizure, infant fever, unconsciousness, stroke, chest pain, snake bite, choking, suicidal with Tele-MANAS 14416 [VERIFY]) + generic.

## Smart mode (request `"mode": "smart"`; default stays `dataset`)
- `app/lexicon.py`: service/test/specialty/symptom/preference vocabulary (EN/Hinglish/Devanagari regexes).
- `data/build_providers.py` -> `data/providers.csv` (186 providers, Hindi/English only, realistic capability rarity).
- `app/smart.py`: MedGemma + Qwen seriousness analyser on CaseFacts + keyword hints -> SmartDecision (asks about severity when unclear).
- `app/smart_match.py`: hard filters on named services, profile weights (rare / test / procedure / consult / home / online),
  preference boosts, language multiplier (strict for consults, lenient for quick procedures), factor chips + reasons.
- `eval/eval_smart.py`: behaviour checks (21/21 incl. --e2e).
- Possible causes are produced internally but hidden (`smart.show_possible_causes: false`) because the brief forbids diagnosing.

## Clinical scope rules
- Routing only. Never diagnose (no disease names), never prescribe.
- Every emergency shows a clear call-for-help path.
- Only synthetic data and cited public datasets. No real patient data.
- Declare every model and API used in the write-up.

## Training (MedGemma 4B LoRA, planned; not run yet) — Kaggle
- Runbook: `docs/medgemma-finetune.md`. Goal: miss as few emergencies as possible (over-triage OK), MedGemma the star.
- Task = production call: `CaseFacts` JSON in → `Decision` JSON out, system prompt `triage.RULES` (`config.yaml classifier.prompt: rules_only` for the fine-tune).
- Data: `training/build_sft.py` (generate → facts → panel label → export), recall-first relabels in `training/manual_review.py`, follow-ups in `training/manual_followups.py`. Splits in `training/sft/` are a stable hash of the generation batch.
- Bulk API jobs: `build_sft.py` stops on quota errors, caps requests per provider and meters OpenAI usage; use a separate key (`CEREBRAS_TRAINING_API_KEY`) for bulk jobs. Ask before any large API job; prefer `eval/smoke_budget.py` (metered, capped) for live checks.
- Train: `python training/kaggle_run.py all` (private dataset, T4, phone-verified account; Gemma 3 trains in float32). Notebook is generated by `training/make_notebook.py`. One Kaggle notebook per round (`TAG`).
- Score: `training/eval_finetune.py`; format experiments: `training/medgemma_probe.py`.

## Evaluation (scoring: Routing 30, Safety 20, Matching 20, UX 20, Explainability 10)
- Macro-F1 on care type and urgency.
- Recall on emergency/red-flag cases; under-triage penalised heavily. Track on EVERY change (`eval/test_redflags.py` offline; `training/eval_finetune.py` for the classifier on `training/sft/test.jsonl`).
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

Pilot (API): voice ~Rs2.02 (Qwen Rs0.57, MedGemma on per-second L4 ~Rs0.30, Saaras transcribe Rs0.25, Bulbul Rs0.90); text ~Rs0.87. Self-host Qwen+MedGemma on 2x L40S (E2E, Rs102/h) above ~2 lakh conv/month -> LLM ~Rs0.17-0.22/conv. Details in the rationale doc.
