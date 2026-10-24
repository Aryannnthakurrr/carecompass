# CareCompass iOS

Native SwiftUI app (iOS 18+) on the same FastAPI backend as the web demo. No third-party packages.

| Screen | What it does | API |
|---|---|---|
| Home | Talk / Emergency tiles, Dataset ↔ Smart matching toggle, example requests, caregiver directory with filters | `GET /caregivers` |
| Chat | Text or voice conversation, follow-up questions with answer chips, care plan, matched caregivers, booking | `POST /triage`, `/stt`, `/tts`, `PUT /me/chats/{id}` |
| Emergency | 10 s countdown with siren and haptics, simulated ambulance dispatch, live Apple Maps tracking, crew brief | `POST /emergency/dispatch`, `GET /emergency/{id}/tracking` |
| Appointments | Phone OTP sign-in, upcoming/past bookings (assistant vs search), server address | `/auth/*`, `/me/bookings` |

## Run

```bash
brew install xcodegen          # once
cd ios && xcodegen             # writes CareCompass.xcodeproj from project.yml
open CareCompass.xcodeproj     # Run on an iPhone simulator
```

Start the API on the Mac first (`uvicorn app.main:app --port 8000`); the simulator reaches it at `localhost`.
For Delhi results in the simulator: `xcrun simctl location booted set 28.5402,77.2700`.

On a real iPhone, run the API with `--host 0.0.0.0` and enter `http://<mac-lan-ip>:8000` under Appointments → Server.
The backend's CORS rule doesn't matter here (native apps don't send an Origin).

## Files

`API.swift` client + models (mirror `app/schemas.py`) · `AppState.swift` location, account, bookings ·
`HomeView.swift` · `ChatView.swift` (+ voice overlay) · `Booking.swift` · `SOSView.swift` · `ProfileView.swift` ·
`Audio.swift` recorder, TTS playback, synthesised siren · `Theme.swift` palette and labels shared with the web UI.
