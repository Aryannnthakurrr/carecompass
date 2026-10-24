import Foundation

// Client for the CareCompass FastAPI backend (app/main.py, app/auth.py, app/services.py).
// The session is the HttpOnly `cc_session` cookie, kept by URLSession's shared cookie store.

struct APIError: LocalizedError, Sendable {
    let status: Int
    let message: String
    var errorDescription: String? { message }
}

// ---------------------------------------------------------------- models

struct Turn: Codable, Hashable, Sendable {
    var role: String  // "user" | "assistant"
    var content: String
}

struct LatLng: Codable, Hashable, Sendable {
    var lat: Double
    var lng: Double
}

enum MatchMode: String, Codable, CaseIterable, Sendable {
    case dataset, smart
    var label: String { self == .dataset ? "Dataset" : "Smart" }
    var hint: String { self == .dataset ? "Organiser roster, reference scoring" : "Full provider directory, honest scoring" }
}

struct TriageRequest: Encodable, Sendable {
    var userInput: String
    var language = "auto"
    var location: LatLng?
    var conversationHistory: [Turn]
    var mode: MatchMode
}

struct Matched: Codable, Hashable, Identifiable, Sendable {
    var caregiverId: String
    var score: Double?
    var reason: String?
    var name: String?
    var role: String?
    var distanceKm: Double?
    var languages: [String]?
    var rating: Double?
    var nextSlot: String?
    var factors: [String]?
    var feeInr: Int?
    var providerType: String?
    var id: String { caregiverId }
}

struct EmergencyInfo: Codable, Hashable, Sendable {
    var title: String
    var call: [String]
    var steps: [String]
}

struct TriageResponse: Codable, Hashable, Sendable {
    var followUpQuestion: String?
    var careType: String?
    var urgency: String?
    var redFlags: [String]?
    var reasoning: String?
    var matchedCaregivers: [Matched]?
    var language: String?
    var explanation: String?
    var emergency: EmergencyInfo?
    var mode: MatchMode?
    var seriousness: String?
    var specialty: String?
    var recommendedTests: [String]?
    var servicesNeeded: [String]?
    var matchSummary: String?
    var possibleCauses: [String]?

    var isEmergency: Bool { careType == "emergency_112" || !(redFlags ?? []).isEmpty }
}

// GET /caregivers: dataset roster or (mode=smart) the full provider directory, same card shape.
// Explicit keys: skill_labels is a dictionary whose snake_case keys must not be camel-cased.
struct Caregiver: Decodable, Hashable, Identifiable, Sendable {
    var caregiverId: String
    var name: String
    var role: String
    var kind: String
    var careType: String
    var skills: [String]
    var languages: [String]
    var rating: Double
    var experienceYears: Int?
    var distanceKm: Double
    var servesYou: Bool
    var video: Bool
    var nextSlot: String?
    var hours: String?
    var reviews: Int?
    var feeInr: Int?
    var openText: String?
    var id: String { caregiverId }

    enum CodingKeys: String, CodingKey {
        case caregiverId = "caregiver_id", name, role, kind, careType = "care_type", skills, languages, rating
        case experienceYears = "experience_years", distanceKm = "distance_km", servesYou = "serves_you", video
        case nextSlot = "next_slot", hours, reviews, feeInr = "fee_inr", openText = "open_text"
    }
}

struct Directory: Decodable, Sendable {
    var count: Int
    var caregivers: [Caregiver]
    var skills: [String]
    var skillLabels: [String: String]?
    enum CodingKeys: String, CodingKey { case count, caregivers, skills, skillLabels = "skill_labels" }
}

struct DirectoryQuery: Sendable, Equatable {
    var kind = ""
    var skill = ""
    var q = ""
    var sort = "best"
    var topRated = false
    var experienced = false
    var near = false
}

struct User: Codable, Hashable, Sendable {
    var name: String?
    var phone: String?
    var email: String?
    var picture: String?
    var title: String { name ?? phone ?? email ?? "You" }
}

struct AuthConfig: Codable, Sendable {
    var googleClientId: String?
    var phone: String?  // "sms" | "dev" | nil
}

struct OtpSent: Codable, Sendable {
    var phone: String
    var resendIn: Int?
    var devCode: String?
}

struct BookingCaregiver: Codable, Hashable, Sendable {
    var caregiverId: String
    var name: String?
    var role: String?
    var reason: String?
    var distanceKm: Double?
    var rating: Double?
}

struct BookingLocation: Codable, Hashable, Sendable {
    var lat: Double
    var lng: Double
    var name: String?
}

struct BookingIn: Encodable, Sendable {
    var chatId: String?
    var caregiver: BookingCaregiver
    var careType: String
    var urgency: String?
    var slot: String?
    var location: BookingLocation?
}

struct Booking: Codable, Hashable, Identifiable, Sendable {
    var id: String
    var chatId: String?
    var caregiver: BookingCaregiver
    var careType: String
    var urgency: String?
    var slot: String?
    var location: BookingLocation?
    var createdAt: Int
}

struct ChatSave: Encodable, Sendable {
    var title: String
    var messages: [Turn]
    var outcome: TriageResponse?
}

// Simulated ambulance dispatch (Dial4242-shaped; nothing real is sent)
struct DispatchIn: Encodable, Sendable {
    var location: LatLng
    var address: String?
    var conversationHistory: [Turn]
    var redFlags: [String]
    var reasoning: String?
}

struct Ambulance: Codable, Hashable, Sendable {
    var code: String
    var name: String
    var crew: String
    var vehicleNo: String
    var driver: String
    var nurse: String
}

struct CrewBrief: Codable, Hashable, Sendable {
    var redFlags: [String]
    var patientSaid: [String]
    var assessment: String?
    var address: String?
}

struct Dispatch: Codable, Hashable, Sendable {
    var id: String
    var provider: String
    var ambulance: Ambulance
    var fareEstimate: Int
    var route: [[Double]]
    var routeKm: Double
    var patient: [Double]
    var demoDriveS: Double
    var crewBrief: CrewBrief
    var wouldCall: [String]
    var status: String
    var position: [Double]
    var progress: Double
    var etaS: Int
    var remainingKm: Double
}

struct Tracking: Codable, Sendable {
    var status: String
    var position: [Double]
    var progress: Double
    var etaS: Int
    var remainingKm: Double
}

// ---------------------------------------------------------------- client

enum API {
    static let defaultBase = "http://localhost:8000"
    /// Settable in Profile → Server, so a phone can reach the laptop over wifi.
    static var base: String {
        get { UserDefaults.standard.string(forKey: "apiBase") ?? defaultBase }
        set { UserDefaults.standard.set(newValue, forKey: "apiBase") }
    }

    private static let snake: JSONDecoder = { let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase; return d }()
    private static let plain = JSONDecoder()
    private static let encoder: JSONEncoder = { let e = JSONEncoder(); e.keyEncodingStrategy = .convertToSnakeCase; return e }()

    private static func request(_ path: String, method: String = "GET", body: Data? = nil, type: String = "application/json",
                                timeout: TimeInterval = 20) async throws -> Data {
        let root = base.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: "/+$", with: "", options: .regularExpression)
        guard let url = URL(string: root + path) else { throw APIError(status: 0, message: "Bad server address") }
        var r = URLRequest(url: url, timeoutInterval: timeout)
        r.httpMethod = method
        if let body { r.httpBody = body; r.setValue(type, forHTTPHeaderField: "Content-Type") }
        let data: Data, resp: URLResponse
        do { (data, resp) = try await URLSession.shared.data(for: r) }
        catch let e as URLError where e.code == .timedOut { throw APIError(status: 0, message: "The server is taking too long. Please try again.") }
        catch let e as URLError where e.code == .cancelled { throw CancellationError() }
        catch { throw APIError(status: 0, message: "Can’t reach the CareCompass server.") }
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
            throw APIError(status: status, message: detail ?? "Request failed (\(status))")
        }
        return data
    }

    private static func get<T: Decodable>(_ path: String, decoder: JSONDecoder = snake) async throws -> T {
        try decoder.decode(T.self, from: try await request(path))
    }

    private static func send<T: Decodable>(_ path: String, _ body: some Encodable, method: String = "POST", timeout: TimeInterval = 20) async throws -> T {
        try snake.decode(T.self, from: try await request(path, method: method, body: try encoder.encode(body), timeout: timeout))
    }

    // triage + speech
    static func triage(_ req: TriageRequest) async throws -> TriageResponse { try await send("/triage", req, timeout: 45) }

    static func stt(wav: Data) async throws -> String {
        struct R: Decodable { var transcript: String }
        return try snake.decode(R.self, from: try await request("/stt", method: "POST", body: wav, type: "audio/wav", timeout: 30)).transcript
    }

    static func tts(_ text: String, language: String) async throws -> Data {
        struct B: Encodable { var text: String; var language: String }
        return try await request("/tts", method: "POST", body: try encoder.encode(B(text: String(text.prefix(1500)), language: language)))
    }

    // directory
    static func caregivers(_ q: DirectoryQuery, near: LatLng?, mode: MatchMode) async throws -> Directory {
        let text = q.q.trimmingCharacters(in: .whitespaces)
        var c = URLComponents()
        c.queryItems = [
            near.map { URLQueryItem(name: "lat", value: String($0.lat)) }, near.map { URLQueryItem(name: "lng", value: String($0.lng)) },
            q.kind.isEmpty ? nil : URLQueryItem(name: "kind", value: q.kind),
            q.skill.isEmpty ? nil : URLQueryItem(name: "skill", value: q.skill),
            text.isEmpty ? nil : URLQueryItem(name: "q", value: text),
            q.topRated ? URLQueryItem(name: "min_rating", value: "4") : nil,
            q.experienced ? URLQueryItem(name: "min_experience", value: "5") : nil,
            q.near ? URLQueryItem(name: "max_km", value: "5") : nil,
            URLQueryItem(name: "sort", value: q.sort), URLQueryItem(name: "mode", value: mode.rawValue),
        ].compactMap { $0 }
        return try await get("/caregivers?\(c.percentEncodedQuery ?? "")", decoder: plain)
    }

    // account
    static func authConfig() async throws -> AuthConfig { try await get("/auth/config") }
    static func me() async throws -> User {
        struct R: Decodable { var user: User }
        let r: R = try await get("/auth/me")
        return r.user
    }
    static func sendOtp(_ phone: String) async throws -> OtpSent {
        struct B: Encodable { var phone: String }
        return try await send("/auth/otp/send", B(phone: phone))
    }
    static func verifyOtp(_ phone: String, code: String) async throws -> User {
        struct B: Encodable { var phone: String; var code: String }
        struct R: Decodable { var user: User }
        let r: R = try await send("/auth/otp/verify", B(phone: phone, code: code))
        return r.user
    }
    static func logout() async { _ = try? await request("/auth/logout", method: "POST") }
    static func bookings() async throws -> [Booking] { try await get("/me/bookings") }
    static func book(_ b: BookingIn) async throws -> Booking { try await send("/me/bookings", b) }
    static func saveChat(_ id: String, _ chat: ChatSave) async {
        _ = try? await request("/me/chats/\(id)", method: "PUT", body: try? encoder.encode(chat))
    }

    // emergency
    static func dispatch(_ d: DispatchIn) async throws -> Dispatch { try await send("/emergency/dispatch", d) }
    static func tracking(_ id: String) async throws -> Tracking { try await get("/emergency/\(id)/tracking") }
}
