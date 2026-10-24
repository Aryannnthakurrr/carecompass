import SwiftUI

// Same palette and wording as the web UI (frontend/styles.css, frontend/i18n.js, English copy).

extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255, blue: Double(hex & 0xFF) / 255)
    }
    static let appBg = Color(hex: 0xFAF8F4)
    static let surface = Color.white
    static let ink = Color(hex: 0x16202A)
    static let ink2 = Color(hex: 0x3A4651)
    static let muted = Color(hex: 0x66707B)
    static let line = Color(hex: 0xE6E0D5)
    static let sand = Color(hex: 0xF6F3EC)
    static let sand2 = Color(hex: 0xEEE9DF)
    static let brand = Color(hex: 0x0B7A64)
    static let brand600 = Color(hex: 0x08624F)
    static let brand50 = Color(hex: 0xE7F3EF)
    static let voice = Color(hex: 0xE2583A)
    static let voice600 = Color(hex: 0xC8452A)
    static let voice50 = Color(hex: 0xFCEDE8)
    static let alert = Color(hex: 0xC42121)
    static let alert600 = Color(hex: 0x9F1A1A)
    static let alert50 = Color(hex: 0xFCEBEB)
    static let amber = Color(hex: 0xA35A06)
    static let amber50 = Color(hex: 0xFDF1DF)
    static let ok = Color(hex: 0x177245)
    static let ok50 = Color(hex: 0xE6F4EC)
}

enum Labels {
    static let care: [String: (String, String, String)] = [  // label, description, SF Symbol
        "paramedic_home_visit": ("Medic home visit", "A trained Medic comes home to check the patient; a doctor can join by video.", "house.fill"),
        "home_nursing": ("Home nursing", "A trained Medic comes home for the procedure: injection, dressing, IV or catheter care.", "syringe.fill"),
        "lab_test_home": ("Lab test at home", "A sample collector comes home at a scheduled time.", "flask.fill"),
        "teleconsultation": ("Doctor on video call", "A doctor can help with this safely over a video call.", "video.fill"),
        "emergency_112": ("Emergency: call 112", "Get emergency help now.", "exclamationmark.triangle.fill"),
        "hospital_visit": ("Hospital visit today", "This needs a hospital today, but not an ambulance.", "cross.case.fill"),
        "clinic_visit": ("Doctor visit", "See a doctor in person at a nearby clinic.", "stethoscope"),
        "specialist_visit": ("Specialist visit", "See the right specialist for this.", "stethoscope"),
        "diagnostic_test": ("Test or scan", "Get the test done at a place that has the equipment.", "waveform.path.ecg"),
    ]
    static func careLabel(_ k: String?) -> String { care[k ?? ""]?.0 ?? humanize(k) }
    static func careDesc(_ k: String?) -> String { care[k ?? ""]?.1 ?? "" }
    static func careIcon(_ k: String?) -> String { care[k ?? ""]?.2 ?? "stethoscope" }
    static let videoCare: Set<String> = ["teleconsultation"]

    static let urgency = ["emergency": "Right now", "same_day": "Today", "scheduled": "Scheduled · at a time you choose"]
    static let seriousness = ["routine": "Routine · when convenient", "soon": "Within 2–3 days", "urgent": "Today", "emergency": "Right now"]
    static let flags = [
        "chest_pain": "Chest pain", "breathlessness": "Trouble breathing", "unconsciousness": "Unconscious or not responding",
        "severe_bleeding": "Heavy bleeding", "stroke_symptoms": "Stroke signs", "seizure": "Seizure", "high_fever_infant": "Fever in a young baby",
        "suicidal_ideation": "Thoughts of self-harm", "severe_dehydration": "Severe dehydration",
    ]
    static func flag(_ k: String) -> String { flags[k] ?? humanize(k) }

    static let skills = [
        "teleconsultation": "Video consult", "prescription": "Prescriptions", "report_review": "Report review", "vitals_monitoring": "Vitals check",
        "wound_dressing": "Wound dressing", "catheter_care": "Catheter care", "post_surgical_care": "After-surgery care", "nebulization": "Nebulisation",
        "injection_im": "Injections", "injection_iv": "IV drip", "bp_sugar_check": "BP & sugar", "elderly_care": "Elderly care",
        "sample_collection": "Sample collection", "home_phlebotomy": "Blood draw at home",
    ]

    static let emSteps = [
        "Stay with the person. Keep them still, sitting or lying comfortably.",
        "Don’t give food, drink or medicine unless a doctor tells you to.",
        "If they stop breathing or don’t respond, tell the call operator at once.",
    ]

    static func humanize(_ s: String?) -> String {
        let t = (s ?? "").replacingOccurrences(of: "_", with: " ")
        return t.prefix(1).uppercased() + t.dropFirst()
    }

    nonisolated(unsafe) private static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()
    static func date(_ s: String?) -> Date? { s.flatMap { iso.date(from: $0) } }

    /// "Today, 4:30 pm" / "Tomorrow, 9:00 am" / "Mon 27 Oct, 9:00 am"
    static func slot(_ s: String?) -> String {
        guard let d = date(s) else { return s ?? "To be confirmed" }
        let cal = Calendar.current
        let time = d.formatted(.dateTime.hour().minute())
        if cal.isDateInToday(d) { return "Today, \(time)" }
        if cal.isDateInTomorrow(d) { return "Tomorrow, \(time)" }
        return "\(d.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated))), \(time)"
    }
}

func initials(_ name: String) -> String {
    name.replacingOccurrences(of: #"^Dr\.?\s+"#, with: "", options: .regularExpression)
        .split(separator: " ").prefix(2).compactMap(\.first).map(String.init).joined().uppercased()
}

// ---------------------------------------------------------------- small shared views

struct Avatar: View {
    let name: String
    var size: CGFloat = 46
    private var hue: Double { Double(name.unicodeScalars.reduce(7) { ($0 * 31 + Int($1.value)) % 360 }) / 360 }
    var body: some View {
        Text(initials(name))
            .font(.system(size: size * 0.35, weight: .heavy))
            .foregroundStyle(Color(hue: hue, saturation: 0.45, brightness: 0.35))
            .frame(width: size, height: size)
            .background(Color(hue: hue, saturation: 0.25, brightness: 0.95), in: Circle())
    }
}

struct Pill: View {
    let icon: String?
    let text: String
    var tint: Color = .brand
    var body: some View {
        HStack(spacing: 5) {
            if let icon { Image(systemName: icon).font(.caption).foregroundStyle(tint) }
            Text(text).font(.footnote.weight(.semibold)).foregroundStyle(Color.ink2)
        }
        .padding(.horizontal, 10).padding(.vertical, 5)
        .background(Color.sand, in: Capsule())
    }
}

struct Rating: View {
    let value: Double
    var body: some View {
        HStack(spacing: 3) {
            Image(systemName: "star.fill").foregroundStyle(Color(hex: 0xE3A008)).font(.caption)
            Text(value, format: .number.precision(.fractionLength(1))).font(.subheadline.weight(.heavy))
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(String(format: "%.1f out of 5", value))
    }
}

struct SectionTitle: View {
    let icon: String
    let text: String
    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: icon).foregroundStyle(Color.brand)
            Text(text.uppercased()).kerning(0.8)
        }
        .font(.caption.weight(.heavy))
        .foregroundStyle(Color.muted)
    }
}

struct PrimaryButtonStyle: ButtonStyle {
    var color: Color = .brand
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.body.weight(.heavy))
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity, minHeight: 52)
            .background(color, in: Capsule())
            .opacity(configuration.isPressed ? 0.85 : 1)
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
    }
}
