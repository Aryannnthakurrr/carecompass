import SwiftUI

private let examples = [
    "Papa ko do din se bukhar hai aur khansi bhi",
    "I need a nurse to change a wound dressing at home",
    "मेरी माँ के घुटनों में 3 महीने से दर्द है",
]

struct HomeView: View {
    @Environment(AppState.self) private var app

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                hero
                tiles
                ModePicker()
                VStack(alignment: .leading, spacing: 10) {
                    SectionTitle(icon: "text.bubble", text: "Or try one of these")
                    ForEach(examples, id: \.self) { ex in
                        Button { app.openChat = ChatSeed(text: ex) } label: {
                            HStack {
                                Text(ex).multilineTextAlignment(.leading).foregroundStyle(Color.ink2)
                                Spacer(minLength: 8)
                                Image(systemName: "arrow.right").foregroundStyle(Color.brand)
                            }
                            .padding(14)
                            .background(Color.surface, in: RoundedRectangle(cornerRadius: 18))
                            .overlay(RoundedRectangle(cornerRadius: 18).stroke(Color.line))
                        }
                    }
                }
                if let next = app.upcoming.first { NextAppointment(booking: next) }
                DirectorySection()
            }
            .padding(16)
        }
        .background(Color.appBg)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) { LocationMenu() }
            ToolbarItem(placement: .topBarTrailing) {
                Link(destination: URL(string: "tel:112")!) {
                    Label("112", systemImage: "phone.fill").labelStyle(.titleAndIcon)
                        .font(.subheadline.weight(.heavy)).foregroundStyle(Color.alert)
                }
                .accessibilityLabel("Call 112")
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await app.loadBookings() }
    }

    private var hero: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let name = app.user?.name?.split(separator: " ").first {
                Text("Namaste, \(name)").font(.subheadline.weight(.heavy)).foregroundStyle(Color.brand)
            }
            Text("How can we help today?").font(.system(size: 30, weight: .heavy)).foregroundStyle(Color.ink)
            Text("Tell us what’s wrong in your own words. Speak or type in English, Hindi or Hinglish.")
                .foregroundStyle(Color.muted)
        }
    }

    private var tiles: some View {
        HStack(spacing: 12) {
            Tile(icon: "mic.fill", title: "Tap and speak", sub: "Describe symptoms by voice", colors: [Color(hex: 0xF27C5F), .voice, .voice600]) {
                app.openChat = ChatSeed(voice: true)
            }
            Tile(icon: "cross.case.fill", title: "Emergency", sub: "One tap sends an ambulance", colors: [Color(hex: 0xE64A4A), .alert, .alert600]) {
                app.sos = SOSContext()
            }
            .accessibilityHint("Starts a 10 second countdown, then sends an ambulance")
        }
    }
}

private struct Tile: View {
    let icon: String, title: String, sub: String, colors: [Color]
    let action: () -> Void
    @State private var breathe = false

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 18) {
                ZStack {
                    Circle().fill(.white.opacity(0.22)).scaleEffect(breathe ? 1.3 : 1).opacity(breathe ? 0 : 1)
                    Circle().fill(.white.opacity(0.2))
                    Image(systemName: icon).font(.title2.weight(.semibold))
                }
                .frame(width: 58, height: 58)
                Spacer(minLength: 0)
                VStack(alignment: .leading, spacing: 3) {
                    Text(title).font(.title3.weight(.heavy))
                    Text(sub).font(.footnote.weight(.semibold)).opacity(0.92)
                }
                .multilineTextAlignment(.leading)
            }
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity, minHeight: 168, alignment: .leading)
            .padding(16)
            .background(RadialGradient(colors: colors, center: .topLeading, startRadius: 0, endRadius: 260), in: RoundedRectangle(cornerRadius: 24))
            .shadow(color: colors[1].opacity(0.45), radius: 14, y: 10)
        }
        .buttonStyle(.plain)
        .onAppear { withAnimation(.easeOut(duration: 2.8).repeatForever(autoreverses: false)) { breathe = true } }
    }
}

struct ModePicker: View {
    @Environment(AppState.self) private var app
    var body: some View {
        @Bindable var app = app
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                SectionTitle(icon: "slider.horizontal.3", text: "Matching")
                Spacer()
                Picker("Matching", selection: $app.mode) {
                    ForEach(MatchMode.allCases, id: \.self) { Text($0.label).tag($0) }
                }
                .pickerStyle(.segmented)
                .frame(width: 180)
            }
            Text(app.mode.hint).font(.footnote).foregroundStyle(Color.muted)
        }
    }
}

struct LocationMenu: View {
    @Environment(AppState.self) private var app
    var body: some View {
        Menu {
            Button { Task { await app.locate() } } label: { Label("Use my current location", systemImage: "location.fill") }
            Divider()
            ForEach(localities, id: \.self) { l in Button(l.name) { app.choose(l) } }
        } label: {
            HStack(spacing: 5) {
                Image(systemName: "mappin.and.ellipse").foregroundStyle(Color.brand)
                Text(app.placeText).lineLimit(1).foregroundStyle(Color.ink2)
                Image(systemName: "chevron.down").font(.caption2.weight(.bold)).foregroundStyle(Color.muted)
            }
            .font(.subheadline.weight(.semibold))
        }
        .accessibilityLabel("Location: \(app.placeText)")
    }
}

private struct NextAppointment: View {
    @Environment(AppState.self) private var app
    let booking: Booking
    var body: some View {
        Button { app.tab = 1 } label: {
            HStack(spacing: 12) {
                Image(systemName: Labels.careIcon(booking.careType)).foregroundStyle(Color.brand)
                    .frame(width: 42, height: 42).background(Color.brand50, in: RoundedRectangle(cornerRadius: 12))
                VStack(alignment: .leading, spacing: 2) {
                    Text("Next appointment").font(.caption.weight(.heavy)).foregroundStyle(Color.muted)
                    Text(booking.caregiver.name ?? booking.caregiver.caregiverId).font(.headline).foregroundStyle(Color.ink)
                    Text("\(Labels.slot(booking.slot)) · \(Labels.careLabel(booking.careType))").font(.footnote).foregroundStyle(Color.muted)
                }
                Spacer()
                Image(systemName: "chevron.right").foregroundStyle(Color.muted)
            }
            .padding(14)
            .background(Color.surface, in: RoundedRectangle(cornerRadius: 20))
            .overlay(RoundedRectangle(cornerRadius: 20).stroke(Color.line))
        }
        .buttonStyle(.plain)
    }
}

// ---------------------------------------------------------------- directory

private let datasetKinds = [("", "All"), ("doctor", "Doctors"), ("medic", "Nurses & medics"), ("phlebotomist", "Lab tests")]
private let smartKinds = [("", "All"), ("hospital", "Hospitals"), ("clinic", "Clinics"), ("diagnostic", "Tests & scans"), ("home", "Home care"), ("video", "Video doctors")]
private let sorts = [("best", "Best for you"), ("distance", "Nearest"), ("rating", "Top rated"), ("experience", "Most experienced")]
private let page = 6

struct DirectorySection: View {
    @Environment(AppState.self) private var app
    @State private var query = DirectoryQuery()
    @State private var data: Directory?
    @State private var failed = false
    @State private var loading = false
    @State private var shown = page
    @State private var booking: Caregiver?

    private struct Key: Equatable { var q: DirectoryQuery; var mode: MatchMode; var place: Place? }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                SectionTitle(icon: "stethoscope", text: app.place?.name.map { "Find care near \($0)" } ?? "Find care near you")
                Spacer()
                if let data { Text("\(data.count) available").font(.caption.weight(.bold)).foregroundStyle(Color.muted) }
            }
            HStack {
                Image(systemName: "magnifyingglass").foregroundStyle(Color.muted)
                TextField("Search name, service or area", text: $query.q).submitLabel(.search)
            }
            .padding(.horizontal, 16).frame(height: 48)
            .background(Color.surface, in: Capsule()).overlay(Capsule().stroke(Color(hex: 0xD9D2C5), lineWidth: 1.5))
            kindBar
            filterBar
            list
        }
        .task(id: Key(q: query, mode: app.mode, place: app.place)) {
            if !query.q.isEmpty { try? await Task.sleep(for: .milliseconds(250)) }  // debounce typing
            guard !Task.isCancelled else { return }
            await load()
        }
        .onChange(of: app.mode) { query.kind = ""; query.skill = "" }
        .sheet(item: $booking) { BookSheet(caregiver: $0) }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            data = try await API.caregivers(query, near: app.place?.point, mode: app.mode)
            failed = false
            shown = page
        } catch {
            if !Task.isCancelled { failed = true }
        }
    }

    private func label(_ skill: String) -> String { data?.skillLabels?[skill] ?? Labels.skills[skill] ?? Labels.humanize(skill) }

    private var kindBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(app.mode == .smart ? smartKinds : datasetKinds, id: \.0) { k in
                    let on = query.kind == k.0
                    Button(k.1) { query.kind = k.0 }
                        .font(.subheadline.weight(.bold))
                        .foregroundStyle(on ? Color.ink : Color.muted)
                        .padding(.horizontal, 14).frame(height: 36)
                        .background(on ? Color.surface : Color.clear, in: Capsule())
                        .shadow(color: on ? .black.opacity(0.06) : .clear, radius: 2, y: 1)
                }
            }
            .padding(4)
        }
        .background(Color.sand2, in: Capsule())
    }

    private var filterBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                Menu {
                    Picker("Sort", selection: $query.sort) { ForEach(sorts, id: \.0) { Text($0.1).tag($0.0) } }
                } label: { chip(sorts.first { $0.0 == query.sort }?.1 ?? "Sort", icon: "arrow.up.arrow.down", on: query.sort != "best") }
                if let skills = data?.skills, !skills.isEmpty {
                    Menu {
                        Picker("Service", selection: $query.skill) {
                            Text("Any service").tag("")
                            ForEach(skills, id: \.self) { Text(label($0)).tag($0) }
                        }
                    } label: { chip(query.skill.isEmpty ? "Any service" : label(query.skill), icon: "cross.case", on: !query.skill.isEmpty) }
                }
                Button { query.topRated.toggle() } label: { chip("4★ & up", icon: "star", on: query.topRated) }
                if app.mode == .dataset { Button { query.experienced.toggle() } label: { chip("5+ yrs", icon: "rosette", on: query.experienced) } }
                Button { query.near.toggle() } label: { chip("Within 5 km", icon: "mappin", on: query.near) }
            }
        }
    }

    private func chip(_ text: String, icon: String, on: Bool) -> some View {
        HStack(spacing: 6) {
            Image(systemName: icon).font(.caption).foregroundStyle(on ? Color.brand : Color.muted)
            Text(text).lineLimit(1)
        }
        .font(.subheadline.weight(.bold))
        .foregroundStyle(on ? Color.brand600 : Color.ink2)
        .padding(.horizontal, 14).frame(height: 38)
        .background(on ? Color.brand50 : Color.surface, in: Capsule())
        .overlay(Capsule().stroke(on ? Color.brand : Color(hex: 0xD9D2C5), lineWidth: 1.5))
    }

    @ViewBuilder private var list: some View {
        if failed {
            EmptyBox(text: "Couldn’t load caregivers right now.", action: "Try again") { Task { await load() } }
        } else if let all = data?.caregivers {
            if all.isEmpty {
                EmptyBox(text: "No one matches these filters.", action: "Clear filters") { query = DirectoryQuery() }
            } else {
                LazyVStack(spacing: 10) {
                    ForEach(all.prefix(shown)) { cg in CaregiverCard(cg: cg, label: label) { booking = cg } }
                }
                .opacity(loading ? 0.5 : 1)
                if all.count > shown {
                    Button("Show \(all.count - shown) more") { shown += page }
                        .font(.body.weight(.heavy)).foregroundStyle(Color.ink2)
                        .frame(maxWidth: .infinity, minHeight: 46)
                        .overlay(Capsule().stroke(Color(hex: 0xD9D2C5), lineWidth: 1.5))
                }
            }
        } else {
            ProgressView().frame(maxWidth: .infinity, minHeight: 120)
        }
    }
}

struct EmptyBox: View {
    let text: String, action: String, run: () -> Void
    var body: some View {
        VStack(spacing: 6) {
            Text(text).font(.subheadline.weight(.semibold)).foregroundStyle(Color.muted).multilineTextAlignment(.center)
            Button(action, action: run).font(.subheadline.weight(.heavy))
        }
        .frame(maxWidth: .infinity).padding(20)
        .overlay(RoundedRectangle(cornerRadius: 22).stroke(Color(hex: 0xD9D2C5), style: StrokeStyle(lineWidth: 1, dash: [4])))
    }
}

struct CaregiverCard: View {
    let cg: Caregiver
    let label: (String) -> String
    let book: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                Avatar(name: cg.name)
                VStack(alignment: .leading, spacing: 2) {
                    Text(cg.name).font(.headline).foregroundStyle(Color.ink)
                    Text(cg.role).font(.footnote).foregroundStyle(Color.muted).lineLimit(1)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 1) {
                    Rating(value: cg.rating)
                    if let r = cg.reviews { Text("\(r) reviews").font(.caption2).foregroundStyle(Color.muted) }
                }
            }
            FlowRow {
                if let y = cg.experienceYears { Pill(icon: "rosette", text: "\(y) yrs exp") }
                if cg.video { Pill(icon: "video.fill", text: "Video call") } else { Pill(icon: "mappin", text: String(format: "%.1f km away", cg.distanceKm)) }
                if let fee = cg.feeInr { Pill(icon: "indianrupeesign", text: "₹\(fee)") }
                Pill(icon: "character.bubble", text: cg.languages.prefix(2).joined(separator: ", "))
            }
            FlowRow {
                ForEach(cg.skills.prefix(3), id: \.self) { s in Tag(text: label(s)) }
                if cg.skills.count > 3 { Tag(text: "+\(cg.skills.count - 3)", dashed: true) }
            }
            HStack {
                if cg.servesYou {
                    Image(systemName: "clock").foregroundStyle(Color.brand)
                    VStack(alignment: .leading, spacing: 0) {
                        Text((cg.openText ?? "Next free").uppercased()).font(.caption2.weight(.heavy)).foregroundStyle(Color.muted)
                        Text(Labels.slot(cg.nextSlot)).font(.subheadline.weight(.bold))
                    }
                } else {
                    Label("Doesn’t cover your area", systemImage: "mappin.slash").font(.subheadline).foregroundStyle(Color.muted)
                }
                Spacer()
                Button("Book", action: book)
                    .font(.body.weight(.heavy)).foregroundStyle(.white)
                    .frame(minWidth: 96, minHeight: 44)
                    .background(Color.brand, in: Capsule())
                    .disabled(!cg.servesYou)
            }
        }
        .padding(16)
        .background(Color.surface, in: RoundedRectangle(cornerRadius: 24))
        .overlay(RoundedRectangle(cornerRadius: 24).stroke(Color.line))
        .opacity(cg.servesYou ? 1 : 0.6)
    }
}

struct Tag: View {
    let text: String
    var dashed = false
    var body: some View {
        Text(text).font(.caption.weight(.bold)).foregroundStyle(Color.brand600)
            .padding(.horizontal, 10).padding(.vertical, 4)
            .overlay(Capsule().stroke(Color(hex: 0xCDE8DF), style: StrokeStyle(lineWidth: 1, dash: dashed ? [3] : [])))
    }
}

/// Wrapping row for pills and tags.
struct FlowRow: Layout {
    var spacing: CGFloat = 6
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = arrange(proposal.width ?? .infinity, subviews)
        let height = rows.reduce(0) { $0 + $1.height } + spacing * CGFloat(max(0, rows.count - 1))
        return CGSize(width: proposal.width ?? rows.map(\.width).max() ?? 0, height: height)
    }
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in arrange(bounds.width, subviews) {
            var x = bounds.minX
            for i in row.items {
                let s = subviews[i].sizeThatFits(.unspecified)
                subviews[i].place(at: CGPoint(x: x, y: y), proposal: .unspecified)
                x += s.width + spacing
            }
            y += row.height + spacing
        }
    }
    private func arrange(_ width: CGFloat, _ subviews: Subviews) -> [(items: [Int], width: CGFloat, height: CGFloat)] {
        var rows: [(items: [Int], width: CGFloat, height: CGFloat)] = []
        var cur: (items: [Int], width: CGFloat, height: CGFloat) = ([], 0, 0)
        for i in subviews.indices {
            let s = subviews[i].sizeThatFits(.unspecified)
            if !cur.items.isEmpty, cur.width + spacing + s.width > width { rows.append(cur); cur = ([], 0, 0) }
            cur.width += (cur.items.isEmpty ? 0 : spacing) + s.width
            cur.height = max(cur.height, s.height)
            cur.items.append(i)
        }
        if !cur.items.isEmpty { rows.append(cur) }
        return rows
    }
}
