import SwiftUI

// The conversation: describe → up to 4 follow-up questions → care plan + matched caregivers → book.
// An emergency result skips straight to the ambulance countdown (SOSView).

enum ChatItem: Identifiable {
    case user(UUID, String)
    case question(UUID, String, n: Int, chips: [String])
    case plan(UUID, TriageResponse)
    case emergency(UUID, TriageResponse)
    case error(UUID, String, retry: String)

    var id: UUID {
        switch self {
        case .user(let id, _), .question(let id, _, _, _), .plan(let id, _), .emergency(let id, _), .error(let id, _, _): id
        }
    }
}

enum VoiceState { case off, listening, thinking, speaking, idle }

@MainActor @Observable
final class ChatModel {
    var items: [ChatItem] = []
    var history: [Turn] = []
    var busy = false
    var booked: String?
    var outcome: TriageResponse?
    var voice: VoiceState = .off
    var caption = ""
    var voiceChips: [String] = []
    var started = false
    let recorder = VoiceRecorder()
    let chatId = UUID().uuidString.lowercased()  // the API's chat ids are lowercase UUIDs
    private var questions = 0
    private var gen = 0   // bumps when voice is interrupted so stale callbacks are dropped

    var step: Int { booked != nil ? 3 : outcome != nil ? 2 : questions > 0 ? 1 : 0 }

    func send(_ raw: String, app: AppState) async {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !busy else { return }
        items.append(.user(UUID(), text))
        busy = true
        defer { busy = false }
        do {
            let res = try await API.triage(TriageRequest(userInput: text, location: app.point, conversationHistory: history, mode: app.mode))
            history.append(Turn(role: "user", content: text))
            handle(res, app: app)
        } catch {
            items.append(.error(UUID(), (error as? APIError)?.message ?? "Something went wrong while checking. Please try again.", retry: text))
            if voice != .off { voice = .idle; caption = "Something went wrong. Tap to try again." }
        }
    }

    private func handle(_ res: TriageResponse, app: AppState) {
        if res.isEmergency {
            outcome = res
            items.append(.emergency(UUID(), res))
            stopVoice()
            app.sos = SOSContext(history: history, outcome: res)
        } else if let q = res.followUpQuestion {
            history.append(Turn(role: "assistant", content: q))
            questions += 1
            let chips = quickChips(for: q)
            items.append(.question(UUID(), q, n: questions, chips: chips))
            if voice != .off { Task { await ask(q, chips: chips, app: app) } }
        } else {
            outcome = res
            items.append(.plan(UUID(), res))
            if voice != .off {
                stopVoice()
                Task { await Speaker.shared.say(planSpeech(res), language: res.language ?? "en") }
            }
        }
        save(app)
    }

    private func save(_ app: AppState) {
        guard app.user != nil, let first = history.first?.content else { return }
        let chat = ChatSave(title: String(first.prefix(120)), messages: history, outcome: outcome)
        Task { [chatId] in await API.saveChat(chatId, chat) }
    }

    // ---- voice loop: listen → transcribe → triage → speak the question → listen again

    func startVoice(app: AppState) {
        Speaker.shared.stop()
        voiceChips = []
        listen(app: app)
    }

    func listen(app: AppState) {
        gen += 1
        let g = gen
        voice = .listening
        caption = ""
        Task {
            let ok = await recorder.start { Task { await self.finishListening(app: app) } }
            if g != gen { recorder.cancel() } else if !ok { voice = .idle; caption = "The microphone is blocked. Allow it in Settings, or type instead." }
        }
    }

    func finishListening(app: AppState) async {
        guard recorder.recording else { return }
        gen += 1
        let g = gen
        let wav = recorder.stop()
        voice = .thinking
        guard let wav, let text = try? await API.stt(wav: wav), !text.isEmpty else {
            if g == gen, voice != .off { voice = .idle; caption = "Sorry, I didn’t catch that. Tap the circle and try again." }
            return
        }
        guard g == gen, voice != .off else { return }
        caption = text
        voiceChips = []
        await send(text, app: app)
    }

    func pickVoiceChip(_ c: String, app: AppState) {
        gen += 1
        recorder.cancel()
        Speaker.shared.stop()
        voice = .thinking
        caption = c
        voiceChips = []
        Task { await send(c, app: app) }
    }

    private func ask(_ q: String, chips: [String], app: AppState) async {
        gen += 1
        let g = gen
        voice = .speaking
        caption = q
        voiceChips = chips
        await Speaker.shared.say(q, language: detectLang(q))
        if g == gen, voice == .speaking { listen(app: app) }
    }

    func stopVoice() {
        gen += 1
        recorder.cancel()
        Speaker.shared.stop()
        voice = .off
    }

    func tapOrb(app: AppState) {
        switch voice {
        case .listening: Task { await finishListening(app: app) }
        case .speaking: Speaker.shared.stop(); listen(app: app)
        case .idle: listen(app: app)
        default: break
        }
    }
}

func planSpeech(_ r: TriageResponse) -> String {
    r.explanation ?? "\(Labels.careLabel(r.careType)). Urgency: \(Labels.urgency[r.urgency ?? ""] ?? "")."
}

// ---- quick-answer chips (same keyword rules as frontend/i18n.js chipsFor)

func detectLang(_ s: String) -> String {
    if s.unicodeScalars.contains(where: { (0x0900...0x097F).contains($0.value) }) { return "hi" }
    let words: Set<String> = ["hai", "hain", "ho", "ko", "ka", "ki", "ke", "se", "mein", "mujhe", "meri", "mera", "nahi", "bhi", "aur", "kya", "kab",
                              "kaise", "kitni", "kitne", "dard", "bukhar", "khansi", "raha", "rahi", "papa", "mummy", "bahut", "abhi", "kal", "din",
                              "saal", "ji", "yeh", "umar"]
    return s.lowercased().split { !$0.isLetter }.filter { words.contains(String($0)) }.count >= 2 ? "hinglish" : "en"
}

func quickChips(for q: String) -> [String] {
    let chips: [String: [String: [String]]] = [
        "en": ["duration": ["Since today", "2–3 days", "About a week", "More than a week"],
               "age": ["Under 3 months", "3–12 months", "1–12 years", "13–59 years", "60 years or older"],
               "yesno": ["Yes", "No", "Not sure"]],
        "hi": ["duration": ["आज से", "2–3 दिन से", "लगभग एक हफ़्ते से", "एक हफ़्ते से ज़्यादा"],
               "age": ["3 महीने से कम", "3–12 महीने", "1–12 साल", "13–59 साल", "60 साल या ज़्यादा"],
               "yesno": ["हाँ", "नहीं", "पता नहीं"]],
        "hinglish": ["duration": ["Aaj se", "2-3 din se", "Lagbhag ek hafte se", "Ek hafte se zyada"],
                     "age": ["3 mahine se kam", "3-12 mahine", "1-12 saal", "13-59 saal", "60 saal ya zyada"],
                     "yesno": ["Haan", "Nahi", "Pata nahi"]],
    ]
    let rules = [
        ("duration", #"how long|since when|kab se|कब से|kitne (din|samay|time)|कितने (दिन|समय)"#),
        ("age", #"how old|\bage\b|umar|umra|उम्र|kitne saal|कितने साल"#),
        ("yesno", #"^\s*(is|are|do|does|did|has|have|any|can|could|kya)\b|^\s*क्या"#),
    ]
    guard let rule = rules.first(where: { q.range(of: $0.1, options: [.regularExpression, .caseInsensitive]) != nil }) else { return [] }
    return chips[detectLang(q)]?[rule.0] ?? []
}

// ---------------------------------------------------------------- view

struct ChatView: View {
    @Environment(AppState.self) private var app
    let seed: ChatSeed
    @State private var model = ChatModel()
    @State private var draft = ""
    @State private var booking: BookTarget?
    @FocusState private var typing: Bool

    var body: some View {
        VStack(spacing: 0) {
            Steps(current: model.step)
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 14) {
                        ForEach(model.items) { item in row(item).id(item.id) }
                        if model.busy { TypingBubble().id("typing") }
                    }
                    .padding(16)
                }
                .scrollDismissesKeyboard(.interactively)
                .onChange(of: model.items.count) { withAnimation { proxy.scrollTo(model.items.last?.id, anchor: .top) } }
                .onChange(of: model.busy) { if model.busy { withAnimation { proxy.scrollTo("typing") } } }
            }
            composer
        }
        .background(Color.appBg)
        .navigationTitle("CareCompass")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Link(destination: URL(string: "tel:112")!) { Label("112", systemImage: "phone.fill").labelStyle(.titleAndIcon) }
                    .font(.subheadline.weight(.heavy)).foregroundStyle(Color.alert)
            }
        }
        .overlay { if model.voice != .off { VoiceOverlay(model: model).transition(.opacity) } }
        .animation(.easeOut(duration: 0.25), value: model.voice == .off)
        .sheet(item: $booking) { t in BookSheet(target: t) { model.booked = $0 } }
        .onAppear {
            // once per conversation: reappearing (tab switch) must not resend, and the request must
            // outlive the view's own task so switching tabs doesn't cancel it
            guard !model.started else { return }
            model.started = true
            if let text = seed.text { Task { await model.send(text, app: app) } } else if seed.voice { model.startVoice(app: app) }
        }
        .onDisappear { model.stopVoice() }
    }

    @ViewBuilder private func row(_ item: ChatItem) -> some View {
        switch item {
        case .user(_, let text):
            HStack {
                Spacer(minLength: 50)
                Text(text).padding(.horizontal, 16).padding(.vertical, 12).foregroundStyle(.white)
                    .background(Color.brand, in: UnevenRoundedRectangle(topLeadingRadius: 20, bottomLeadingRadius: 20, bottomTrailingRadius: 6, topTrailingRadius: 20))
            }
        case .question(let id, let q, let n, let chips):
            BotRow {
                Text("QUESTION \(n)").font(.caption.weight(.heavy)).foregroundStyle(Color.brand)
                Bubble(text: q)
                if !chips.isEmpty && id == model.items.last?.id && !model.busy {
                    FlowRow(spacing: 8) {
                        ForEach(chips, id: \.self) { c in
                            Button(c) { Task { await model.send(c, app: app) } }
                                .font(.body.weight(.bold)).foregroundStyle(Color.brand600)
                                .padding(.horizontal, 18).frame(minHeight: 44)
                                .overlay(Capsule().stroke(Color(hex: 0xCDE8DF), lineWidth: 1.5))
                        }
                    }
                }
                ListenButton(text: q, language: detectLang(q))
            }
        case .plan(_, let res):
            PlanCard(res: res, booked: model.booked) { cg in
                booking = BookTarget(caregiverId: cg.caregiverId, name: cg.name ?? cg.caregiverId, role: cg.role, rating: cg.rating,
                                     careType: res.careType ?? "paramedic_home_visit", slot: cg.nextSlot, distanceKm: cg.distanceKm,
                                     reason: cg.reason, chatId: app.user != nil ? model.chatId : nil, urgency: res.urgency)
            }
        case .emergency(_, let res):
            EmergencyCard(res: res) { app.sos = SOSContext(history: model.history, outcome: res) }
        case .error(_, let text, let retry):
            BotRow {
                Text(text).font(.body.weight(.semibold)).foregroundStyle(Color.alert600)
                    .padding(14).background(Color.alert50, in: RoundedRectangle(cornerRadius: 18))
                Button("Try again") { Task { await model.send(retry, app: app) } }.font(.body.weight(.heavy))
            }
        }
    }

    private var empty: Bool { draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.busy }

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField("Describe the problem…", text: $draft, axis: .vertical)
                .lineLimit(1...5).focused($typing).padding(.vertical, 12).padding(.leading, 16)
                .onSubmit(submit)
            Button { typing = false; model.startVoice(app: app) } label: {
                Image(systemName: "mic.fill").font(.title3).foregroundStyle(Color.voice).frame(width: 46, height: 46).background(Color.voice50, in: Circle())
            }
            .accessibilityLabel("Speak")
            Button(action: submit) {
                Image(systemName: "arrow.up").font(.title3.weight(.bold)).foregroundStyle(.white).frame(width: 46, height: 46)
                    .background(empty ? Color(hex: 0xC9CDD1) : Color.brand, in: Circle())
            }
            .disabled(empty)
            .accessibilityLabel("Send")
        }
        .padding(5)
        .background(Color.surface, in: RoundedRectangle(cornerRadius: 28))
        .overlay(RoundedRectangle(cornerRadius: 28).stroke(typing ? Color.brand : Color(hex: 0xD9D2C5), lineWidth: 1.5))
        .padding(.horizontal, 12).padding(.vertical, 8)
        .background(Color.appBg)
    }

    private func submit() {
        let t = draft
        draft = ""
        Task { await model.send(t, app: app) }
    }
}

// ---------------------------------------------------------------- pieces

private struct Steps: View {
    let current: Int
    private let labels = ["Describe", "Questions", "Care plan", "Book"]
    var body: some View {
        HStack(spacing: 4) {
            ForEach(labels.indices, id: \.self) { i in
                HStack(spacing: 5) {
                    ZStack {
                        Circle().fill(i < current ? Color.brand : i == current ? Color.ink : Color.sand2)
                        if i < current { Image(systemName: "checkmark").font(.caption2.weight(.heavy)).foregroundStyle(.white) }
                        else { Text("\(i + 1)").font(.caption2.weight(.heavy)).foregroundStyle(i == current ? .white : Color.muted) }
                    }
                    .frame(width: 22, height: 22)
                    if i == current { Text(labels[i]).font(.caption.weight(.bold)).lineLimit(1).fixedSize() }
                    if i < labels.count - 1 { Capsule().fill(i < current ? Color.brand : Color(hex: 0xD9D2C5)).frame(height: 2) }
                }
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 8)
        .animation(.easeOut, value: current)
    }
}

private struct BotRow<Content: View>: View {
    @ViewBuilder let content: Content
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "safari.fill").foregroundStyle(Color.brand).frame(width: 32, height: 32)
                .background(Color.brand50, in: RoundedRectangle(cornerRadius: 11))
            VStack(alignment: .leading, spacing: 8) { content }
            Spacer(minLength: 0)
        }
    }
}

private struct Bubble: View {
    let text: String
    var body: some View {
        let shape = UnevenRoundedRectangle(topLeadingRadius: 20, bottomLeadingRadius: 6, bottomTrailingRadius: 20, topTrailingRadius: 20)
        Text(text).font(.title3.weight(.semibold)).foregroundStyle(Color.ink)
            .padding(.horizontal, 16).padding(.vertical, 12)
            .background(Color.surface, in: shape)
            .overlay(shape.stroke(Color.line))
    }
}

private struct TypingBubble: View {
    @State private var on = false
    var body: some View {
        BotRow {
            HStack(spacing: 5) {
                ForEach(0..<3) { i in
                    Circle().fill(Color.brand).frame(width: 8, height: 8).opacity(on ? 1 : 0.3).offset(y: on ? -4 : 0)
                        .animation(.easeInOut(duration: 0.5).repeatForever().delay(Double(i) * 0.15), value: on)
                }
            }
            .padding(16).background(Color.surface, in: RoundedRectangle(cornerRadius: 20))
        }
        .onAppear { on = true }
        .accessibilityLabel("Understanding…")
    }
}

struct ListenButton: View {
    let text: String
    let language: String
    var body: some View {
        Button { Task { await Speaker.shared.say(text, language: language) } } label: {
            Label("Listen", systemImage: "speaker.wave.2.fill").font(.subheadline.weight(.bold))
        }
        .foregroundStyle(Color.brand)
    }
}

private struct PlanCard: View {
    let res: TriageResponse
    let booked: String?
    let book: (Matched) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .top, spacing: 14) {
                    Image(systemName: Labels.careIcon(res.careType)).font(.title2).foregroundStyle(Color.brand)
                        .frame(width: 52, height: 52).background(Color.brand50, in: RoundedRectangle(cornerRadius: 16))
                    VStack(alignment: .leading, spacing: 3) {
                        Text("RECOMMENDED CARE").font(.caption.weight(.heavy)).foregroundStyle(Color.muted)
                        Text(Labels.careLabel(res.careType)).font(.title2.weight(.heavy))
                        Text(Labels.careDesc(res.careType)).font(.subheadline).foregroundStyle(Color.muted)
                    }
                }
                urgency
                if let e = res.explanation { Text(e).font(.body.weight(.semibold)) }
                if res.mode == .smart { smart }
                if let r = res.reasoning {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("WHY THIS RECOMMENDATION").font(.caption.weight(.heavy)).foregroundStyle(Color.muted)
                        Text(r).font(.subheadline).foregroundStyle(Color.ink2)
                    }
                    .padding(12).frame(maxWidth: .infinity, alignment: .leading).background(Color.sand, in: RoundedRectangle(cornerRadius: 12))
                }
                ListenButton(text: planSpeech(res), language: res.language ?? "en")
            }
            .padding(18)
            .background(Color.surface, in: RoundedRectangle(cornerRadius: 24))
            .overlay(RoundedRectangle(cornerRadius: 24).stroke(Color.line))
            .overlay(alignment: .top) { UnevenRoundedRectangle(topLeadingRadius: 24, topTrailingRadius: 24).fill(Color.brand).frame(height: 4) }

            Text("Best caregivers for you").font(.subheadline.weight(.heavy)).foregroundStyle(Color.ink2)
            if let s = res.matchSummary {
                (Text("How we chose: ").bold() + Text(s)).font(.footnote).foregroundStyle(Color.brand600)
                    .padding(12).background(Color.brand50, in: RoundedRectangle(cornerRadius: 12))
            }
            let cgs = res.matchedCaregivers ?? []
            if cgs.isEmpty {
                Text("No caregiver is free in time right now. If things get worse, call 112.").font(.subheadline.weight(.semibold))
                    .foregroundStyle(Color.amber).padding(14).background(Color.amber50, in: RoundedRectangle(cornerRadius: 18))
            }
            ForEach(Array(cgs.enumerated()), id: \.element.id) { i, cg in
                MatchCard(cg: cg, best: i == 0, video: Labels.videoCare.contains(res.careType ?? ""), booked: booked) { book(cg) }
            }
        }
    }

    private var urgency: some View {
        let text = (res.mode == .smart ? res.seriousness.flatMap { Labels.seriousness[$0] } : nil)
            ?? Labels.urgency[res.urgency ?? ""] ?? Labels.humanize(res.urgency)
        let color: Color = res.urgency == "same_day" ? .amber : res.urgency == "emergency" ? .alert : .ok
        return HStack(spacing: 8) {
            Circle().fill(color).frame(width: 10, height: 10)
            Text("Urgency:").fontWeight(.semibold)
            Text(text).fontWeight(.heavy)
        }
        .font(.subheadline).foregroundStyle(color)
        .padding(.horizontal, 14).padding(.vertical, 10).frame(maxWidth: .infinity, alignment: .leading)
        .background(color.opacity(0.1), in: RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder private var smart: some View {
        let list = { (xs: [String]?) in (xs ?? []).isEmpty ? nil : xs!.joined(separator: ", ") }
        let rows: [(String, String)] = [
            res.specialty.map { ("See", $0) },
            list(res.servicesNeeded).map { ("Needs", $0) },
            list(res.recommendedTests).map { ("A doctor may ask for", $0) },
            list(res.possibleCauses).map { ("Not a diagnosis — a doctor may check for", $0) },
        ].compactMap { $0 }
        if !rows.isEmpty { Summary(rows: rows) }
    }
}

private struct MatchCard: View {
    let cg: Matched
    let best: Bool
    let video: Bool
    let booked: String?
    let book: () -> Void

    var body: some View {
        let name = cg.name ?? cg.caregiverId
        let isBooked = booked == cg.caregiverId
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                Avatar(name: name)
                VStack(alignment: .leading, spacing: 2) {
                    Text(name).font(.headline)
                    Text(cg.role ?? cg.caregiverId).font(.footnote).foregroundStyle(Color.muted).lineLimit(1)
                }
                Spacer()
                if let r = cg.rating { Rating(value: r) }
            }
            if let reason = cg.reason {
                Label(reason, systemImage: "checkmark").font(.subheadline.weight(.semibold)).foregroundStyle(Color.brand600)
                    .padding(10).frame(maxWidth: .infinity, alignment: .leading).background(Color.brand50, in: RoundedRectangle(cornerRadius: 12))
            }
            FlowRow {
                if video { Pill(icon: "video.fill", text: "Video call") } else if let d = cg.distanceKm { Pill(icon: "mappin", text: String(format: "%.1f km away", d)) }
                if let s = cg.nextSlot { Pill(icon: "clock", text: Labels.slot(s)) }
                if let fee = cg.feeInr { Pill(icon: "indianrupeesign", text: "₹\(fee)") }
                if let l = cg.languages, !l.isEmpty { Pill(icon: "character.bubble", text: l.joined(separator: ", ")) }
            }
            if let f = cg.factors, !f.isEmpty { FlowRow { ForEach(f, id: \.self) { Tag(text: $0) } } }
            HStack {
                ProgressView(value: min(1, max(0, cg.score ?? 0))).tint(Color.brand).frame(width: 64)
                Text("\(Int(((cg.score ?? 0) * 100).rounded()))% match").font(.footnote.weight(.bold)).foregroundStyle(Color.muted)
                Spacer()
                Button(isBooked ? "Booked" : "Book", action: book)
                    .font(.body.weight(.heavy)).foregroundStyle(.white)
                    .frame(minWidth: 112, minHeight: 46)
                    .background(isBooked ? Color.ok : best ? Color.brand : Color.ink, in: Capsule())
                    .disabled(booked != nil)
                    .opacity(booked != nil && !isBooked ? 0.35 : 1)
            }
        }
        .padding(16)
        .padding(.top, best ? 6 : 0)
        .background(Color.surface, in: RoundedRectangle(cornerRadius: 24))
        .overlay(RoundedRectangle(cornerRadius: 24).stroke(isBooked ? Color.ok : best ? Color.brand : Color.line, lineWidth: best || isBooked ? 2 : 1))
        .overlay(alignment: .topLeading) {
            if best {
                Label("Best match", systemImage: "rosette").font(.caption.weight(.heavy)).foregroundStyle(.white)
                    .padding(.horizontal, 10).frame(height: 24).background(Color.brand, in: Capsule()).offset(x: 14, y: -12)
            }
        }
        .padding(.top, best ? 8 : 0)
    }
}

private struct EmergencyCard: View {
    let res: TriageResponse
    let sendAmbulance: () -> Void
    @State private var steps = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("This may be an emergency", systemImage: "exclamationmark.triangle.fill").font(.title3.weight(.heavy)).foregroundStyle(Color.alert)
            if let flags = res.redFlags, !flags.isEmpty {
                FlowRow {
                    ForEach(flags, id: \.self) { f in
                        Text(Labels.flag(f)).font(.subheadline.weight(.bold)).foregroundStyle(Color.alert600)
                            .padding(.horizontal, 12).padding(.vertical, 5).background(.white, in: Capsule()).overlay(Capsule().stroke(Color(hex: 0xF1C4C4)))
                    }
                }
            }
            if let r = res.reasoning { Text(r).font(.subheadline).foregroundStyle(Color.ink2) }
            HStack(spacing: 8) {
                Link(destination: URL(string: "tel:112")!) { Label("Call 112", systemImage: "phone.fill").frame(maxWidth: .infinity, minHeight: 52) }
                    .foregroundStyle(.white).background(Color.alert, in: RoundedRectangle(cornerRadius: 16))
                Link(destination: URL(string: "tel:108")!) { Label("Call 108", systemImage: "phone.fill").frame(maxWidth: .infinity, minHeight: 52) }
                    .foregroundStyle(Color.alert).overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.alert, lineWidth: 2))
            }
            .font(.body.weight(.heavy))
            Button(action: sendAmbulance) { Label("Send an ambulance", systemImage: "cross.case.fill").frame(maxWidth: .infinity, minHeight: 52) }
                .font(.body.weight(.heavy)).foregroundStyle(Color.alert)
                .background(.white, in: RoundedRectangle(cornerRadius: 16)).overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.alert, lineWidth: 2))
            DisclosureGroup("Show emergency steps", isExpanded: $steps) {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(Array(Labels.emSteps.enumerated()), id: \.offset) { i, s in
                        HStack(alignment: .top, spacing: 10) {
                            Text("\(i + 1)").font(.caption.weight(.heavy)).foregroundStyle(.white).frame(width: 24, height: 24).background(Color.alert, in: Circle())
                            Text(s).font(.subheadline.weight(.semibold))
                        }
                    }
                }
                .padding(.top, 8)
            }
            .font(.subheadline.weight(.heavy)).tint(Color.alert)
        }
        .padding(18)
        .background(LinearGradient(colors: [Color.alert50, .white], startPoint: .top, endPoint: .bottom), in: RoundedRectangle(cornerRadius: 24))
        .overlay(RoundedRectangle(cornerRadius: 24).stroke(Color.alert, lineWidth: 2))
    }
}

// ---------------------------------------------------------------- voice overlay

private struct VoiceOverlay: View {
    @Environment(AppState.self) private var app
    let model: ChatModel
    @State private var pulse = false

    private var tone: Color { model.voice == .listening ? .voice : model.voice == .idle ? Color(hex: 0x8C949C) : .brand }
    private var title: String {
        switch model.voice {
        case .listening: "Listening…"
        case .thinking: "Understanding…"
        case .speaking: "Speaking…"
        default: "Tap the circle to speak"
        }
    }
    private var hint: String {
        switch model.voice {
        case .listening: "Speak naturally. Tap the circle when you’re done."
        case .speaking: "Tap the circle to answer now"
        case .thinking: "Finding the right care…"
        default: ""
        }
    }

    var body: some View {
        let lvl = CGFloat(model.recorder.level)
        VStack(spacing: 14) {
            HStack {
                Spacer()
                Button { model.stopVoice() } label: { Image(systemName: "xmark").font(.title3.weight(.semibold)).frame(width: 44, height: 44) }
                    .foregroundStyle(Color.ink2).background(Color.black.opacity(0.05), in: Circle()).accessibilityLabel("Close")
            }
            Spacer()
            Text(title.uppercased()).font(.subheadline.weight(.heavy)).kerning(1.2).foregroundStyle(tone)
            Button { model.tapOrb(app: app) } label: {
                ZStack {
                    ForEach(0..<3) { i in
                        Circle().fill(tone.opacity(0.14 - Double(i) * 0.04))
                            .frame(width: 168, height: 168)
                            .scaleEffect(1 + lvl * CGFloat(0.45 + Double(i) * 0.35) + (pulse && model.voice != .listening ? CGFloat(i + 1) * 0.12 : 0))
                    }
                    Circle().fill(RadialGradient(colors: [tone.opacity(0.75), tone], center: .topLeading, startRadius: 0, endRadius: 170))
                        .frame(width: 168, height: 168).scaleEffect(1 + lvl * 0.14)
                        .shadow(color: tone.opacity(0.5), radius: 24, y: 14)
                    if model.voice == .thinking {
                        ProgressView().controlSize(.large).tint(.white)
                    } else {
                        Image(systemName: model.voice == .speaking ? "waveform" : "mic.fill").font(.system(size: 54, weight: .semibold)).foregroundStyle(.white)
                            .symbolEffect(.variableColor.iterative, isActive: model.voice == .speaking)
                    }
                }
                .frame(width: 260, height: 260)
                .animation(.linear(duration: 0.1), value: lvl)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(title)
            Text(hint).font(.subheadline).foregroundStyle(Color.muted).multilineTextAlignment(.center).frame(minHeight: 22)
            Text(model.caption.isEmpty && model.voice == .listening ? "…" : model.caption)
                .font(.title2.weight(.bold)).multilineTextAlignment(.center).foregroundStyle(model.voice == .idle ? Color.alert600 : Color.ink)
                .frame(minHeight: 70).padding(.horizontal, 24)
            if !model.voiceChips.isEmpty {
                FlowRow(spacing: 8) {
                    ForEach(model.voiceChips, id: \.self) { c in
                        Button(c) { model.pickVoiceChip(c, app: app) }.font(.body.weight(.bold)).foregroundStyle(Color.brand600)
                            .padding(.horizontal, 18).frame(minHeight: 44).background(.white, in: Capsule())
                            .overlay(Capsule().stroke(Color(hex: 0xCDE8DF), lineWidth: 1.5))
                    }
                }
                .padding(.horizontal, 24)
            }
            Spacer()
            HStack(spacing: 10) {
                Button { model.stopVoice() } label: { Label("Type instead", systemImage: "keyboard").padding(.horizontal, 20).frame(height: 50) }
                    .foregroundStyle(Color.ink2).background(.white, in: Capsule()).overlay(Capsule().stroke(Color(hex: 0xD9D2C5), lineWidth: 1.5))
                if model.voice == .listening {
                    Button { Task { await model.finishListening(app: app) } } label: { Label("Done", systemImage: "checkmark").padding(.horizontal, 20).frame(height: 50) }
                        .foregroundStyle(.white).background(Color.voice, in: Capsule())
                }
            }
            .font(.body.weight(.heavy))
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(RadialGradient(colors: [.white, Color(hex: 0xF7F3EC), Color(hex: 0xEFE9DF)], center: UnitPoint(x: 0.5, y: 0.35),
                                   startRadius: 0, endRadius: 600).ignoresSafeArea())
        .onAppear { withAnimation(.easeOut(duration: 1.6).repeatForever(autoreverses: true)) { pulse = true } }
    }
}
