import AppIntents

// One-tap emergency from outside the app: Shortcuts, Siri, Spotlight, the Action Button and Lock Screen
// controls all run this intent, which opens CareCompass straight into the 10 s ambulance countdown.

struct StartEmergencyIntent: AppIntent {
    static let title: LocalizedStringResource = "Emergency: send an ambulance"
    static let description = IntentDescription("Opens CareCompass straight into the 10-second ambulance countdown. Cancel within 10 seconds if it was a mistake.")
    static let openAppWhenRun = true

    @MainActor
    func perform() async throws -> some IntentResult {
        let app = AppState.shared
        if app.sos == nil { app.sos = SOSContext() }  // already counting down: don't restart it
        return .result()
    }
}

struct CareCompassShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: StartEmergencyIntent(),
            phrases: ["\(.applicationName) emergency", "Emergency in \(.applicationName)", "Send an ambulance with \(.applicationName)"],
            shortTitle: "Emergency",
            systemImageName: "cross.case.fill"
        )
    }
}
