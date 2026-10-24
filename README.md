# CareCompass

Multilingual (English / Hindi / Hinglish), voice-first care navigation: understands a patient's request, asks a few follow-up questions, decides care type and urgency, escalates emergencies, and ranks the best caregivers.

Built for HACK IIITD — CAI, IIIT-Delhi ("AI for Healthcare Access").

> Routing support only — CareCompass does not diagnose or prescribe. In an emergency, call **112** (national emergency) or **108** (ambulance).

## Setup (Windows / PowerShell)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # then fill in SARVAM_API_KEY and GEMINI_API_KEY
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


## Status

Work in progress.
