# CareCompass demo UI

Conversational, voice-first front end for the full journey: describe the problem (voice or text) → follow-up questions with answer chips → care plan with reasons → ranked caregivers → one-tap booking. Red flags jump to a full-screen emergency path (112 / 108) with pre-written steps.

Plain HTML/CSS/ES modules, no build step.

## Run

```powershell
uvicorn app.main:app --port 8000          # API: /triage, /stt, /tts; keys in .env
python -m http.server 5173 -d frontend    # UI, in a second terminal
```

Open http://localhost:5173. The UI calls the API at `http://localhost:8000` by default.

- `?api=http://host:port` points it at another backend.
- `?mock=1` forces the offline mock. The mock also switches on automatically when `/triage` is unreachable, and the header then shows "Demo mode".
- Voice needs a browser with microphone access (Chrome or Edge recommended). Pages on `localhost` count as secure, so the mic works there.

## Backend contract

The request and response shapes the UI expects are documented at the top of [`api.js`](api.js). In short:

- **`POST /triage`** follows the problem statement's schema. It may also return the optional extras `explanation` and caregiver `name` / `role` / `distance_km` / `languages` / `rating` / `next_slot`.
- **`POST /stt`** (Sarvam Saaras) and **`POST /tts`** (Sarvam Bulbul) are optional. Without them the browser's Web Speech API handles listening and speaking.
- **CORS:** the backend must allow the page's origin, or serve this folder itself.

## Files

| File | Role |
|---|---|
| `app.js` | Conversation state, rendering, voice loop, booking, emergency path |
| `voice.js` | Mic capture, live level for the orb animation, silence auto-stop, speech output |
| `api.js` | Backend calls and contract; falls back to `mock.js` |
| `mock.js` | Offline stand-in for `/triage` (synthetic caregivers, same weights as `config.yaml`) |
| `i18n.js` | English / Hindi / Hinglish copy, pre-written emergency text, answer-chip rules |
| `geo.js`, `icons.js` | Delhi NCR localities and haversine; inline SVG icons |

Self-check for the mock's routing and red flags: `node frontend/mock.check.mjs`
