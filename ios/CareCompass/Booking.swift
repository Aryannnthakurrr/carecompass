import SwiftUI

/// Who is being booked, from the directory (chatId nil) or from an AI care plan (chatId set).
struct BookTarget: Identifiable, Hashable {
    var caregiverId: String
    var name: String
    var role: String?
    var rating: Double?
    var careType: String
    var slot: String?
    var distanceKm: Double?
    var reason: String?
    var chatId: String?
    var urgency: String?
    var id: String { caregiverId }
    var video: Bool { Labels.videoCare.contains(careType) }
}

extension BookTarget {
    init(_ c: Caregiver) {
        self.init(caregiverId: c.caregiverId, name: c.name, role: c.role, rating: c.rating, careType: c.careType,
                  slot: c.nextSlot, distanceKm: c.video ? nil : c.distanceKm)
    }
}

struct BookSheet: View {
    @Environment(AppState.self) private var app
    @Environment(\.dismiss) private var dismiss
    let target: BookTarget
    var onBooked: (String) -> Void = { _ in }
    @State private var busy = false
    @State private var result: (ref: String, saved: Bool)?

    init(caregiver: Caregiver) { target = BookTarget(caregiver) }
    init(target: BookTarget, onBooked: @escaping (String) -> Void) { self.target = target; self.onBooked = onBooked }

    var body: some View {
        ScrollView {
            VStack(spacing: 6) {
                if let result { done(result) } else { confirm }
            }
            .padding(20)
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }

    private var rows: [(String, String)] {
        var r = [
            ("Care", Labels.careLabel(target.careType)),
            ("When", Labels.slot(target.slot)),
            ("Where", target.video ? "Video link will be sent by SMS" : app.placeText),
        ]
        if let rating = target.rating { r.append(("Rating", String(format: "★ %.1f", rating))) }
        return r
    }

    private var confirm: some View {
        VStack(spacing: 8) {
            Avatar(name: target.name, size: 64).padding(.top, 6)
            Text("Book \(target.name)?").font(.title2.weight(.heavy)).multilineTextAlignment(.center)
            if let role = target.role { Text(role).foregroundStyle(Color.muted) }
            Summary(rows: rows).padding(.vertical, 10)
            Button {
                busy = true
                Task {
                    let r = await app.book(BookingIn(
                        chatId: target.chatId,
                        caregiver: BookingCaregiver(caregiverId: target.caregiverId, name: target.name, role: target.role,
                                                    reason: target.reason, distanceKm: target.distanceKm, rating: target.rating),
                        careType: target.careType, urgency: target.urgency, slot: target.slot,
                        location: BookingLocation(lat: app.point.lat, lng: app.point.lng, name: app.place?.name)))
                    busy = false
                    withAnimation(.spring) { result = r }
                    onBooked(target.caregiverId)
                    UINotificationFeedbackGenerator().notificationOccurred(.success)
                }
            } label: {
                if busy { ProgressView().tint(.white) } else { Text("Confirm booking") }
            }
            .buttonStyle(PrimaryButtonStyle())
            .disabled(busy)
            Button("Cancel") { dismiss() }.font(.body.weight(.heavy)).frame(minHeight: 44)
        }
    }

    private func done(_ r: (ref: String, saved: Bool)) -> some View {
        VStack(spacing: 8) {
            Image(systemName: "checkmark").font(.system(size: 34, weight: .heavy)).foregroundStyle(.white)
                .frame(width: 76, height: 76).background(Color.ok, in: Circle())
                .padding(10).background(Color.ok50, in: Circle())
                .transition(.scale.combined(with: .opacity))
            Text("Booking confirmed").font(.title2.weight(.heavy))
            Text(target.video ? "\(target.name) will call you on video. Keep your phone nearby." : "\(target.name) will come to you. You’ll get an SMS before they arrive.")
                .multilineTextAlignment(.center).foregroundStyle(Color.muted)
            Summary(rows: [("Caregiver", [target.name, target.role].compactMap { $0 }.joined(separator: " · "))] + rows.prefix(3) + [("Booking ID", r.ref)])
                .padding(.vertical, 10)
            if !r.saved && app.auth != nil {
                Text("Sign in on the Appointments tab to keep your bookings.").font(.footnote.weight(.semibold))
                    .foregroundStyle(Color.brand600).padding(12).frame(maxWidth: .infinity)
                    .background(Color.brand50, in: RoundedRectangle(cornerRadius: 14))
            }
            Button("Done") { dismiss() }.buttonStyle(PrimaryButtonStyle())
            if r.saved {
                Button("See my appointments") { dismiss(); app.tab = 1 }.font(.body.weight(.heavy)).frame(minHeight: 44)
            }
        }
    }
}

struct Summary: View {
    let rows: [(String, String)]
    var body: some View {
        Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 10) {
            ForEach(rows, id: \.0) { row in
                GridRow {
                    Text(row.0).foregroundStyle(Color.muted).font(.subheadline.weight(.semibold))
                    Text(row.1).font(.subheadline.weight(.bold))
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(Color.sand, in: RoundedRectangle(cornerRadius: 18))
    }
}
