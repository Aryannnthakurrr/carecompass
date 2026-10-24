import SwiftUI

@main
struct CareCompassApp: App {
    @State private var app = AppState.shared

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(app)
                .tint(.brand)
                .task { await app.start() }
        }
    }
}

struct RootView: View {
    @Environment(AppState.self) private var app

    var body: some View {
        @Bindable var app = app
        TabView(selection: $app.tab) {
            Tab("Home", systemImage: "house.fill", value: 0) {
                NavigationStack {
                    HomeView()
                        .navigationDestination(item: $app.openChat) { seed in ChatView(seed: seed) }
                }
            }
            Tab("Appointments", systemImage: "calendar", value: 1) {
                NavigationStack { ProfileView() }
            }
        }
        // the emergency countdown covers everything, wherever it was started from
        .fullScreenCover(item: $app.sos) { ctx in SOSView(context: ctx) }
    }
}
