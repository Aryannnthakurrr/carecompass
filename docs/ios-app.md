# CareCompass iOS app

The app lives in `ios/`; commands below run from the repository root.

Native SwiftUI app (iOS 18+) on the same FastAPI backend as the web demo. No third-party packages.

| Screen | What it does | API |
|---|---|---|
| Home | Talk / Emergency tiles, example requests, caregiver directory with filters | `GET /caregivers` |
| Chat | Text or voice conversation, follow-up questions with answer chips, care plan, matched caregivers, booking | `POST /triage`, `/stt`, `/tts`, `PUT /me/chats/{id}` |
| Emergency | 10 s countdown with siren and haptics, simulated ambulance dispatch, live Apple Maps tracking, crew brief | `POST /emergency/dispatch`, `GET /emergency/{id}/tracking` |
| Appointments | Phone OTP sign-in, upcoming/past bookings (assistant vs search), server address | `/auth/*`, `/me/bookings` |

## Run

```bash
brew install xcodegen          # once
cd ios && xcodegen             # writes CareCompass.xcodeproj from project.yml
open CareCompass.xcodeproj     # Run on an iPhone simulator
```

### The server (a laptop)

The app is the client; a laptop runs the CareCompass server (FastAPI + MedGemma through Ollama). On the laptop
(Windows / PowerShell, see the top-level README for setup):

```powershell
.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- **Real iPhone:** phone and laptop on the same Wi-Fi; enter `http://<laptop-ip>:8000` under Appointments → Server
  (`ipconfig` on the laptop shows the IPv4 address). Allow port 8000 through the Windows firewall if the phone can't connect.
- **Simulator on a Mac:** run the server on the Mac or point the Server setting at the laptop's address; `localhost` works
  only when the server runs on the same machine. For Delhi results: `xcrun simctl location booted set 28.5402,77.2700`.

The backend's CORS rule doesn't matter here (native apps don't send an Origin).

### Response fields the app can use

All optional and backwards compatible (see `app/schemas.py`):

| Field | Use in the app |
|---|---|
| `follow_up_kind` | `medical`, `safety_check`, `clarify`, `redirect`, `misheard`. Echo it back as `kind` on that assistant turn in `conversation_history` so only medical questions count toward the question budget (the server infers it if missing) |
| `safety_note` | show as a red banner above the question (the call-112 line during a safety check) |
| `conversation_ended` | repeated non-health messages: show `explanation` as the closing line and disable the input |
| `emergency` | pre-written card per red flag: title, numbers to call, first-aid steps |
| request `input_mode: "voice"` | send with transcribed speech so the server can ask "did you say …?" on a mis-heard word |

## Files

`API.swift` client + models (mirror `app/schemas.py`) · `AppState.swift` location, account, bookings ·
`HomeView.swift` · `ChatView.swift` (+ voice overlay) · `Booking.swift` · `SOSView.swift` · `ProfileView.swift` ·
`Audio.swift` recorder, TTS playback, synthesised siren · `Theme.swift` palette and labels shared with the web UI.
