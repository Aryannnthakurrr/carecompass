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
ollama pull gemma3:4b
```

## Models and APIs used

| Stage | Model | Provider |
|---|---|---|
| Speech → text | Sarvam Saarika | Sarvam API |
| Intake + explanation | Gemma 3 27B | Google AI Studio |
| Triage | MedGemma 4B (LoRA, Q4 GGUF) + rule-based red flags | Local (Ollama) |
| Text → speech | Sarvam Bulbul | Sarvam API |
| Fallback LLM | Gemma 3 4B | Local (Ollama) |

## Status

Work in progress.
