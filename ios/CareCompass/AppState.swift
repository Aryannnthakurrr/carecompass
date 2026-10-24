import CoreLocation
import Foundation
import Observation

struct Locality: Hashable, Sendable {
    let name: String
    let lat: Double
    let lng: Double
}

// Delhi NCR reference points (frontend/geo.js): name a GPS fix and back the manual picker.
let localities = [
    Locality(name: "Okhla", lat: 28.5402, lng: 77.2700), Locality(name: "Saket", lat: 28.5245, lng: 77.2066),
    Locality(name: "Lajpat Nagar", lat: 28.5677, lng: 77.2433), Locality(name: "Connaught Place", lat: 28.6315, lng: 77.2167),
    Locality(name: "Laxmi Nagar", lat: 28.6304, lng: 77.2777), Locality(name: "Vasant Kunj", lat: 28.5200, lng: 77.1590),
    Locality(name: "Dwarka", lat: 28.5921, lng: 77.0460), Locality(name: "Rohini", lat: 28.7495, lng: 77.0565),
    Locality(name: "Noida Sector 18", lat: 28.5708, lng: 77.3260), Locality(name: "Gurugram Cyber City", lat: 28.4950, lng: 77.0895),
]

func haversineKm(_ a: LatLng, _ b: LatLng) -> Double {
    let r = Double.pi / 180
    let h = pow(sin((b.lat - a.lat) * r / 2), 2) + cos(a.lat * r) * cos(b.lat * r) * pow(sin((b.lng - a.lng) * r / 2), 2)
    return 2 * 6371 * asin(sqrt(h))
}

func nearestLocality(_ p: LatLng, maxKm: Double = 6) -> Locality? {
    localities.map { ($0, haversineKm(p, LatLng(lat: $0.lat, lng: $0.lng))) }.min { $0.1 < $1.1 }.flatMap { $0.1 <= maxKm ? $0.0 : nil }
}

struct Place: Hashable, Sendable {
    enum Source: Sendable { case gps, manual, fallback }
    var point: LatLng
    var name: String?
    var source: Source

    var text: String {
        switch source {
        case .manual: name ?? "Chosen area"
        case .fallback: "\(name ?? "Delhi") area"
        case .gps: name.map { "Near \($0)" } ?? "Current location"
        }
    }
}

/// What the emergency screen needs to brief the ambulance crew.
struct SOSContext: Identifiable, Sendable {
    let id = UUID()
    var history: [Turn] = []
    var outcome: TriageResponse?
}

@MainActor @Observable
final class AppState {
    var mode: MatchMode = MatchMode(rawValue: UserDefaults.standard.string(forKey: "mode") ?? "") ?? .dataset {
        didSet { UserDefaults.standard.set(mode.rawValue, forKey: "mode") }
    }
    var place: Place?
    var auth: AuthConfig?
    var user: User?
    var bookings: [Booking] = []
    var sos: SOSContext?           // non-nil presents the emergency countdown
    var openChat: ChatSeed?        // non-nil pushes a conversation (from the home tiles)
    var tab = 0
    var serverRev = 0              // bumps when the server address changes, so every screen reloads

    private let locator = Locator()

    var placeText: String { place?.text ?? "Finding you…" }
    var point: LatLng { place?.point ?? LatLng(lat: localities[0].lat, lng: localities[0].lng) }

    func start() async {
        async let account: Void = loadAccount()
        await locate()
        await account
    }

    func locate() async {
        if let p = await locator.current() {
            let pt = LatLng(lat: (p.latitude * 1e5).rounded() / 1e5, lng: (p.longitude * 1e5).rounded() / 1e5)
            place = Place(point: pt, name: nearestLocality(pt)?.name, source: .gps)
        } else if place == nil {
            let l = localities[0]
            place = Place(point: LatLng(lat: l.lat, lng: l.lng), name: l.name, source: .fallback)
        }
    }

    func choose(_ l: Locality) {
        place = Place(point: LatLng(lat: l.lat, lng: l.lng), name: l.name, source: .manual)
    }

    /// Points the app at another backend; returns nil when it answers, else what went wrong.
    func setServer(_ base: String) async -> String? {
        API.base = base
        serverRev += 1
        do { auth = try await API.authConfig() } catch { auth = nil; user = nil; bookings = []; return error.localizedDescription }
        user = try? await API.me()
        await loadBookings()
        return nil
    }

    func loadAccount() async {
        auth = try? await API.authConfig()
        guard auth != nil else { user = nil; return }
        user = try? await API.me()
        await loadBookings()
    }

    func loadBookings() async {
        guard user != nil else { return }
        do { bookings = try await API.bookings() } catch let e as APIError where e.status == 401 { user = nil; bookings = [] } catch {}
    }

    func signOut() async {
        await API.logout()
        user = nil
        bookings = []
    }

    /// Saved to the account when signed in; a guest gets a local reference only (like the web app).
    func book(_ b: BookingIn) async -> (ref: String, saved: Bool) {
        if user != nil, let saved = try? await API.book(b) {
            bookings.insert(saved, at: 0)
            return (saved.id, true)
        }
        try? await Task.sleep(for: .milliseconds(500))
        return ("CC-" + String(UUID().uuidString.prefix(6)), false)
    }

    var upcoming: [Booking] {
        let now = Date()
        return bookings.filter { (Labels.date($0.slot) ?? .distantFuture) > now }
            .sorted { (Labels.date($0.slot) ?? .distantFuture) < (Labels.date($1.slot) ?? .distantFuture) }
    }
    var past: [Booking] {
        let now = Date()
        return bookings.filter { (Labels.date($0.slot) ?? .distantFuture) <= now }
            .sorted { (Labels.date($0.slot) ?? .distantPast) > (Labels.date($1.slot) ?? .distantPast) }
    }
}

/// Starts a conversation: optionally with typed text, or straight into voice.
struct ChatSeed: Hashable, Identifiable {
    let id = UUID()
    var text: String?
    var voice = false
}

// One-shot location fix; nil when denied or it takes too long.
@MainActor
final class Locator: NSObject, CLLocationManagerDelegate {
    private let manager = CLLocationManager()
    private var waiting: [CheckedContinuation<CLLocationCoordinate2D?, Never>] = []

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }

    func current() async -> CLLocationCoordinate2D? {
        switch manager.authorizationStatus {
        case .denied, .restricted: return nil
        case .notDetermined: manager.requestWhenInUseAuthorization()
        default: manager.requestLocation()
        }
        return await withCheckedContinuation { c in
            waiting.append(c)
            Task { try? await Task.sleep(for: .seconds(8)); self.finish(nil) }
        }
    }

    private func finish(_ p: CLLocationCoordinate2D?) {
        let w = waiting
        waiting = []
        w.forEach { $0.resume(returning: p) }
    }

    nonisolated func locationManagerDidChangeAuthorization(_ m: CLLocationManager) {
        let s = m.authorizationStatus
        Task { @MainActor in
            if s == .authorizedWhenInUse || s == .authorizedAlways { self.manager.requestLocation() }
            else if s == .denied || s == .restricted { self.finish(nil) }
        }
    }
    nonisolated func locationManager(_ m: CLLocationManager, didUpdateLocations locs: [CLLocation]) {
        let p = locs.last?.coordinate
        Task { @MainActor in self.finish(p) }
    }
    nonisolated func locationManager(_ m: CLLocationManager, didFailWithError error: Error) {
        Task { @MainActor in self.finish(nil) }
    }
}
