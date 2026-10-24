import MapKit
import SwiftUI

// One-tap emergency: 10 s countdown with a siren (cancel covers accidental taps), then a SIMULATED
// ambulance dispatch (POST /emergency/dispatch, Dial4242-shaped) and a live map of the ambulance driving in.

struct SOSView: View {
    @Environment(AppState.self) private var app
    @Environment(\.dismiss) private var dismiss
    let context: SOSContext

    enum Phase { case count, request, track(Dispatch), failed }
    @State private var phase = Phase.count
    @State private var left = 10
    @State private var steps = 0
    @State private var siren = Siren()

    var body: some View {
        ZStack {
            switch phase {
            case .count: countdown
            case .request: requesting
            case .track(let d): TrackingView(d: d, close: close)
            case .failed: failed
            }
        }
        .task { await run() }
        .onDisappear { siren.stop() }
    }

    private var counting: Bool { if case .count = phase { true } else { false } }

    private func close() {
        siren.stop()
        dismiss()
    }

    private func run() async {
        siren.start()
        let haptic = UIImpactFeedbackGenerator(style: .heavy)
        while left > 0 {
            haptic.impactOccurred()
            try? await Task.sleep(for: .seconds(1))
            guard !Task.isCancelled, counting else { return }   // closed, or "Send now" moved on already
            withAnimation(.linear(duration: 1)) { left -= 1 }
        }
        await dispatch()
    }

    private func dispatch() async {
        guard counting else { return }
        siren.stop()
        UINotificationFeedbackGenerator().notificationOccurred(.warning)
        withAnimation { phase = .request }
        let body = DispatchIn(location: app.point, address: app.placeText, conversationHistory: Array(context.history.suffix(40)),
                              redFlags: context.outcome?.redFlags ?? [], reasoning: context.outcome?.reasoning.map { String($0.prefix(600)) })
        async let call = API.dispatch(body)
        for i in 1...3 {
            try? await Task.sleep(for: .milliseconds(850))
            withAnimation { steps = i }
        }
        do {
            let d = try await call
            withAnimation(.easeInOut) { phase = .track(d) }
        } catch {
            withAnimation { phase = .failed }
        }
    }

    private var red: some View {
        LinearGradient(colors: [Color(hex: 0xD42A2A), Color(hex: 0x8E1414)], startPoint: .top, endPoint: .bottom).ignoresSafeArea()
    }

    private var countdown: some View {
        VStack(spacing: 16) {
            Spacer()
            Label("SENDING AN AMBULANCE IN", systemImage: "exclamationmark.triangle.fill").font(.subheadline.weight(.heavy)).kerning(1)
            ZStack {
                Circle().stroke(.white.opacity(0.18), lineWidth: 12)
                Circle().trim(from: 0, to: CGFloat(left) / 10).stroke(.white, style: StrokeStyle(lineWidth: 12, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                Text("\(left)").font(.system(size: 96, weight: .heavy)).monospacedDigit().contentTransition(.numericText(countsDown: true))
            }
            .frame(width: 220, height: 220)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Sending an ambulance in \(left) seconds")
            Text("Your location and symptoms will be shared with the ambulance crew.").multilineTextAlignment(.center).opacity(0.92)
            Label(app.placeText, systemImage: "mappin").font(.subheadline.weight(.bold)).padding(.horizontal, 12).padding(.vertical, 6)
                .background(.black.opacity(0.18), in: Capsule())
            Spacer()
            Button(action: close) { Label("Cancel, I’m OK", systemImage: "xmark").font(.title3.weight(.heavy)).frame(maxWidth: .infinity, minHeight: 64) }
                .foregroundStyle(Color.alert).background(.white, in: Capsule()).shadow(color: .black.opacity(0.3), radius: 14, y: 8)
            Button("Send now") { Task { await dispatch() } }.font(.body.weight(.heavy)).underline().frame(minHeight: 48)
        }
        .foregroundStyle(.white)
        .padding(24)
        .background(red)
    }

    private var requesting: some View {
        VStack(spacing: 18) {
            Spacer()
            PulseIcon(systemName: "cross.case.fill")
            Text("Requesting an ambulance…").font(.title.weight(.heavy))
            VStack(spacing: 10) {
                ForEach(Array(["Finding the nearest free ambulance", "Working out the route and fare", "Sharing your symptoms with the crew"].enumerated()), id: \.offset) { i, s in
                    HStack(spacing: 12) {
                        ZStack {
                            if steps > i { Image(systemName: "checkmark.circle.fill").font(.title3).foregroundStyle(.white) }
                            else if steps == i { ProgressView().tint(.white) }
                            else { Circle().stroke(.white.opacity(0.5), lineWidth: 2.5).frame(width: 20, height: 20) }
                        }
                        .frame(width: 24)
                        Text(s).font(.body.weight(.semibold))
                        Spacer()
                    }
                    .padding(14).background(.black.opacity(0.14), in: RoundedRectangle(cornerRadius: 16))
                    .opacity(steps >= i ? 1 : 0.5)
                }
            }
            Spacer()
        }
        .foregroundStyle(.white)
        .padding(24)
        .background(red)
    }

    private var failed: some View {
        VStack(spacing: 18) {
            Spacer()
            PulseIcon(systemName: "exclamationmark.triangle.fill")
            Text("Couldn’t reach the ambulance service. Call 112 now.").font(.title2.weight(.heavy)).multilineTextAlignment(.center)
            Link(destination: URL(string: "tel:112")!) {
                Label("Call 112", systemImage: "phone.fill").font(.title2.weight(.heavy)).frame(maxWidth: .infinity, minHeight: 64)
            }
            .foregroundStyle(Color.alert).background(.white, in: RoundedRectangle(cornerRadius: 20))
            Spacer()
            Button("Close", action: close).font(.body.weight(.heavy)).underline().frame(minHeight: 48)
        }
        .foregroundStyle(.white)
        .padding(24)
        .background(red)
    }
}

private struct PulseIcon: View {
    let systemName: String
    @State private var on = false
    var body: some View {
        Image(systemName: systemName).font(.system(size: 50, weight: .semibold))
            .frame(width: 112, height: 112)
            .background(.white.opacity(0.16), in: Circle())
            .overlay(Circle().stroke(.white.opacity(on ? 0 : 0.35), lineWidth: on ? 24 : 0).scaleEffect(on ? 1.3 : 1))
            .onAppear { withAnimation(.easeOut(duration: 1.4).repeatForever(autoreverses: false)) { on = true } }
    }
}

// ---------------------------------------------------------------- live tracking

private struct TrackingView: View {
    let d: Dispatch
    let close: () -> Void
    @State private var status: String
    @State private var etaS: Int
    @State private var remainingKm: Double
    @State private var start: Date
    @State private var now = Date()
    @State private var camera: MapCameraPosition
    private let route: [CLLocationCoordinate2D]
    private let seg: [Double]
    private let total: Double

    init(d: Dispatch, close: @escaping () -> Void) {
        self.d = d
        self.close = close
        let r = d.route.map { CLLocationCoordinate2D(latitude: $0[0], longitude: $0[1]) }
        route = r
        seg = zip(r, r.dropFirst()).map { haversineKm(LatLng(lat: $0.latitude, lng: $0.longitude), LatLng(lat: $1.latitude, lng: $1.longitude)) }
        total = seg.reduce(0, +)
        _status = State(initialValue: d.status)
        _etaS = State(initialValue: d.etaS)
        _remainingKm = State(initialValue: d.remainingKm)
        _start = State(initialValue: Date().addingTimeInterval(-d.progress * d.demoDriveS))
        _camera = State(initialValue: .rect(Self.rect(r)))
    }

    private var patient: CLLocationCoordinate2D { CLLocationCoordinate2D(latitude: d.patient[0], longitude: d.patient[1]) }
    private var arrived: Bool { status == "arrived" }
    private var frac: Double { min(1, now.timeIntervalSince(start) / d.demoDriveS) }

    var body: some View {
        let (pos, i) = point(at: frac)
        VStack(spacing: 0) {
            Map(position: $camera, interactionModes: [.pan, .zoom]) {
                MapPolyline(coordinates: Array(route.prefix(i + 1)) + [pos])
                    .stroke(Color.gray.opacity(0.55), style: StrokeStyle(lineWidth: 5, lineCap: .round, dash: [2, 8]))
                MapPolyline(coordinates: [pos] + Array(route.suffix(from: min(i + 1, route.count)))).stroke(Color.alert, lineWidth: 6)
                Annotation("You", coordinate: patient) {
                    Circle().fill(Color(hex: 0x1F6FEB)).frame(width: 22, height: 22).overlay(Circle().stroke(.white, lineWidth: 4)).shadow(radius: 3)
                }
                Annotation("Ambulance", coordinate: pos) {
                    Image(systemName: "cross.case.fill").font(.headline).foregroundStyle(.white)
                        .frame(width: 40, height: 40).background(Color.alert, in: RoundedRectangle(cornerRadius: 12))
                        .overlay(RoundedRectangle(cornerRadius: 12).stroke(.white, lineWidth: 3)).shadow(radius: 4)
                }
            }
            .overlay(alignment: .top) {
                Text("Simulation: no real ambulance is sent").font(.footnote.weight(.heavy)).foregroundStyle(.white)
                    .padding(.horizontal, 12).padding(.vertical, 6).background(Color.ink.opacity(0.82), in: Capsule()).padding(.top, 8)
            }
            .frame(maxHeight: .infinity)
            panel.frame(maxHeight: .infinity)
        }
        .background(Color.appBg)
        .task { await animate() }
        .task { await poll() }
        .task { await follow() }
    }

    private var panel: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 8) {
                            Circle().fill(arrived ? Color.ok : Color.alert).frame(width: 9, height: 9)
                            Text(arrived ? "THE AMBULANCE HAS ARRIVED" : status == "arriving" ? "ARRIVING NOW" : "AMBULANCE ON THE WAY")
                                .font(.footnote.weight(.heavy)).kerning(0.8).foregroundStyle(arrived ? Color.ok : Color.alert)
                        }
                        if !arrived { Text("\(max(1, Int((Double(etaS) / 60).rounded()))) min").font(.system(size: 34, weight: .heavy)) }
                        Text(arrived ? "Go to the door or send someone to meet the crew." : String(format: "%.1f km away", remainingKm))
                            .font(.subheadline.weight(.semibold)).foregroundStyle(Color.muted)
                    }
                    Spacer()
                    Text(d.id).font(.caption.weight(.heavy)).foregroundStyle(Color.muted)
                        .padding(.horizontal, 10).padding(.vertical, 4).background(Color.sand, in: Capsule())
                }
                ProgressView(value: frac).tint(arrived ? Color.ok : Color.alert)
                Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 12) {
                    GridRow {
                        info("cross.case.fill", "\(d.ambulance.code) · \(d.ambulance.name)", d.ambulance.vehicleNo)
                        info("person.fill", "Driver", d.ambulance.driver)
                    }
                    GridRow {
                        info("syringe.fill", "Paramedic nurse", d.ambulance.nurse)
                        info("indianrupeesign", "Estimated fare", "₹\(d.fareEstimate) · pay later")
                    }
                }
                brief
                DisclosureGroup {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("In production this request goes to an ambulance network’s developer API, such as Dial4242. This demo simulates \(d.provider) with these calls:")
                            .font(.footnote)
                        ForEach(d.wouldCall, id: \.self) {
                            Text($0).font(.caption.monospaced()).padding(.horizontal, 6).padding(.vertical, 2).background(Color.sand, in: RoundedRectangle(cornerRadius: 6))
                        }
                    }
                    .padding(.top, 6)
                } label: {
                    Label("How this works in a real emergency", systemImage: "chevron.left.forwardslash.chevron.right").font(.subheadline.weight(.heavy))
                }
                .padding(12)
                .overlay(RoundedRectangle(cornerRadius: 18).stroke(Color(hex: 0xD9D2C5), style: StrokeStyle(lineWidth: 1, dash: [4])))
                HStack(spacing: 8) {
                    Link(destination: URL(string: "tel:112")!) { Label("Call 112", systemImage: "phone.fill").frame(maxWidth: .infinity, minHeight: 52) }
                        .foregroundStyle(.white).background(Color.alert, in: RoundedRectangle(cornerRadius: 16))
                    Button(action: close) { Text("Close").frame(maxWidth: .infinity, minHeight: 52) }
                        .foregroundStyle(Color.alert).overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.alert, lineWidth: 2))
                }
                .font(.body.weight(.heavy))
            }
            .padding(16)
        }
        .background(Color.surface)
        .clipShape(UnevenRoundedRectangle(topLeadingRadius: 26, topTrailingRadius: 26))
        .shadow(color: .black.opacity(0.12), radius: 16, y: -6)
    }

    private var brief: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Shared with the driver and nurse", systemImage: "checkmark.shield.fill").font(.subheadline.weight(.heavy))
            if !d.crewBrief.redFlags.isEmpty {
                FlowRow {
                    ForEach(d.crewBrief.redFlags, id: \.self) {
                        Text(Labels.flag($0)).font(.footnote.weight(.bold)).foregroundStyle(Color.alert600)
                            .padding(.horizontal, 10).padding(.vertical, 4).background(.white, in: Capsule()).overlay(Capsule().stroke(Color(hex: 0xF1C4C4)))
                    }
                }
            }
            if d.crewBrief.patientSaid.isEmpty {
                Text("No symptoms described. The crew will check on arrival.").font(.subheadline).foregroundStyle(Color.ink2)
            } else {
                Text("WHAT YOU TOLD US").font(.caption.weight(.heavy)).foregroundStyle(Color.muted)
                ForEach(d.crewBrief.patientSaid, id: \.self) { Text("“\($0)”").font(.subheadline.weight(.semibold)) }
            }
            if let a = d.crewBrief.assessment { Text(a).font(.subheadline).foregroundStyle(Color.ink2) }
            Label(d.crewBrief.address ?? "Your location", systemImage: "mappin").font(.subheadline.weight(.bold)).foregroundStyle(Color.ink2)
        }
        .padding(14).frame(maxWidth: .infinity, alignment: .leading).background(Color.sand, in: RoundedRectangle(cornerRadius: 18))
    }

    private func info(_ icon: String, _ k: String, _ v: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon).foregroundStyle(Color.alert).frame(width: 38, height: 38).background(Color.alert50, in: RoundedRectangle(cornerRadius: 12))
            VStack(alignment: .leading, spacing: 1) {
                Text(k).font(.caption.weight(.bold)).foregroundStyle(Color.muted)
                Text(v).font(.subheadline.weight(.heavy)).fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // The drive is time-based (same formula as the server); polling the tracking endpoint corrects drift.
    private func animate() async {
        while !Task.isCancelled, frac < 1 {
            now = Date()
            try? await Task.sleep(for: .milliseconds(50))
        }
        now = Date()
    }

    private func poll() async {
        while !Task.isCancelled, !arrived {
            try? await Task.sleep(for: .seconds(3))
            guard let t = try? await API.tracking(d.id) else { continue }
            start = Date().addingTimeInterval(-t.progress * d.demoDriveS)
            etaS = t.etaS
            remainingKm = t.remainingKm
            if t.status == "arrived", !arrived { UINotificationFeedbackGenerator().notificationOccurred(.success) }
            status = t.status
        }
    }

    private func follow() async {   // zoom in as it closes in, keeping both in view
        while !Task.isCancelled, frac < 1 {
            try? await Task.sleep(for: .seconds(5))
            withAnimation(.easeInOut(duration: 1.2)) { camera = .rect(Self.rect([point(at: frac).0, patient], pad: 0.5)) }
        }
    }

    private func point(at f: Double) -> (CLLocationCoordinate2D, Int) {
        var left = f * total
        for (i, s) in seg.enumerated() {
            if left <= s {
                let k = s > 0 ? left / s : 1
                let a = route[i], b = route[i + 1]
                return (CLLocationCoordinate2D(latitude: a.latitude + (b.latitude - a.latitude) * k, longitude: a.longitude + (b.longitude - a.longitude) * k), i)
            }
            left -= s
        }
        return (route.last ?? patient, max(0, route.count - 2))
    }

    private static func rect(_ pts: [CLLocationCoordinate2D], pad: Double = 0.25) -> MKMapRect {
        let r = pts.map { MKMapRect(origin: MKMapPoint($0), size: MKMapSize(width: 0, height: 0)) }.reduce(MKMapRect.null) { $0.union($1) }
        let side = max(r.width, r.height, 1500)
        return r.insetBy(dx: -side * pad - (side - r.width) / 2, dy: -side * pad - (side - r.height) / 2)
    }
}
