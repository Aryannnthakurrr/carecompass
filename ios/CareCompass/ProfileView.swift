import SwiftUI

// Profile: phone OTP sign-in, the user's appointments (booked by the assistant or from search), server address.

struct ProfileView: View {
    @Environment(AppState.self) private var app
    @State private var tab = 0

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let user = app.user {
                    header(user)
                    SectionTitle(icon: "calendar", text: "Appointments")
                    Picker("Appointments", selection: $tab) {
                        Text("Upcoming (\(app.upcoming.count))").tag(0)
                        Text("Past (\(app.past.count))").tag(1)
                    }
                    .pickerStyle(.segmented)
                    let list = tab == 0 ? app.upcoming : app.past
                    if list.isEmpty {
                        EmptyBox(text: tab == 0 ? "No upcoming appointments." : "No past appointments yet.", action: "Find care") { app.tab = 0 }
                    }
                    ForEach(list) { AppointmentCard(b: $0, past: tab == 1) }
                    Button { Task { await app.signOut() } } label: {
                        Label("Sign out", systemImage: "rectangle.portrait.and.arrow.right").frame(maxWidth: .infinity, minHeight: 50)
                    }
                    .font(.body.weight(.heavy)).foregroundStyle(Color.ink2)
                    .overlay(Capsule().stroke(Color(hex: 0xD9D2C5), lineWidth: 1.5))
                } else if app.auth?.phone != nil {
                    SignInCard()
                } else {
                    EmptyBox(text: "Sign-in isn’t available on this server. Check the server address below.", action: "Retry") {
                        Task { await app.loadAccount() }
                    }
                }
                ServerSetting()
            }
            .padding(16)
        }
        .background(Color.appBg)
        .navigationTitle(app.user == nil ? "Sign in" : "Profile")
        .refreshable { await app.loadBookings() }
        .task { await app.loadBookings() }
    }

    private func header(_ u: User) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 14) {
                Text(u.name.map(initials) ?? "•").font(.title2.weight(.heavy))
                    .frame(width: 56, height: 56).background(.white.opacity(0.2), in: Circle())
                VStack(alignment: .leading, spacing: 2) {
                    Text(u.title).font(.title3.weight(.heavy))
                    if let p = u.phone, p != u.title { Text(p).font(.subheadline).opacity(0.8) }
                }
            }
            HStack(spacing: 8) {
                stat(app.upcoming.count, "Upcoming")
                stat(app.past.count, "Completed")
                stat(app.bookings.filter { $0.chatId != nil }.count, "Via assistant")
            }
        }
        .foregroundStyle(.white)
        .padding(18)
        .background(LinearGradient(colors: [.brand, .brand600], startPoint: .topLeading, endPoint: .bottomTrailing), in: RoundedRectangle(cornerRadius: 24))
    }

    private func stat(_ n: Int, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("\(n)").font(.title2.weight(.heavy))
            Text(label).font(.caption.weight(.semibold)).opacity(0.85)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(.white.opacity(0.14), in: RoundedRectangle(cornerRadius: 14))
    }
}

private struct AppointmentCard: View {
    let b: Booking
    let past: Bool

    var body: some View {
        let d = Labels.date(b.slot)
        let ai = b.chatId != nil
        HStack(alignment: .top, spacing: 14) {
            VStack(spacing: 0) {
                Text(d?.formatted(.dateTime.weekday(.abbreviated)).uppercased() ?? "—").font(.caption2.weight(.heavy))
                Text(d.map { "\(Calendar.current.component(.day, from: $0))" } ?? "?").font(.title2.weight(.heavy))
                Text(d?.formatted(.dateTime.month(.abbreviated)).uppercased() ?? "").font(.caption2.weight(.heavy))
            }
            .foregroundStyle(past ? Color.muted : Color.brand600)
            .frame(width: 58, height: 70)
            .background(past ? Color.sand2 : Color.brand50, in: RoundedRectangle(cornerRadius: 16))
            VStack(alignment: .leading, spacing: 3) {
                Label([d?.formatted(.dateTime.hour().minute()), Labels.careLabel(b.careType)].compactMap { $0 }.joined(separator: " · "),
                      systemImage: Labels.careIcon(b.careType))
                    .font(.footnote.weight(.bold)).foregroundStyle(Color.brand600)
                Text(b.caregiver.name ?? b.caregiver.caregiverId).font(.headline)
                if let r = b.caregiver.role { Text(r).font(.footnote).foregroundStyle(Color.muted) }
                FlowRow {
                    Label(ai ? "Booked by assistant" : "Booked from search", systemImage: ai ? "bubble.left.fill" : "magnifyingglass")
                        .font(.caption.weight(.heavy)).foregroundStyle(ai ? Color.voice600 : Color(hex: 0x2A5C9A))
                        .padding(.horizontal, 9).padding(.vertical, 3)
                        .background(ai ? Color.voice50 : Color(hex: 0xE8EEF6), in: Capsule())
                    Tag(text: Labels.videoCare.contains(b.careType) ? "Video call" : b.location?.name ?? "At home")
                    Text(b.id).font(.caption.weight(.bold)).foregroundStyle(Color.muted)
                }
                .padding(.top, 4)
            }
            Spacer(minLength: 0)
        }
        .padding(14)
        .background(Color.surface, in: RoundedRectangle(cornerRadius: 24))
        .overlay(RoundedRectangle(cornerRadius: 24).stroke(Color.line))
        .opacity(past ? 0.82 : 1)
    }
}

private struct SignInCard: View {
    @Environment(AppState.self) private var app
    @State private var phone = ""
    @State private var code = ""
    @State private var sent: OtpSent?
    @State private var busy = false
    @State private var error: String?
    @FocusState private var focus: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Image(systemName: "calendar.badge.checkmark").font(.title).foregroundStyle(Color.brand)
                .frame(width: 56, height: 56).background(Color.brand50, in: RoundedRectangle(cornerRadius: 16))
            Text("Keep your appointments").font(.title2.weight(.heavy))
            Text("Sign in with your phone to see your bookings and past chats on any device.").foregroundStyle(Color.muted)
            if let sent {
                Text("Enter the 6-digit code sent to \(sent.phone)").font(.subheadline.weight(.bold)).foregroundStyle(Color.ink2)
                if let dev = sent.devCode {
                    Text("Demo mode: your code is \(dev)").font(.subheadline.weight(.bold)).foregroundStyle(Color.amber)
                        .padding(10).frame(maxWidth: .infinity, alignment: .leading).background(Color.amber50, in: RoundedRectangle(cornerRadius: 12))
                }
                TextField("••••••", text: $code)
                    .keyboardType(.numberPad).textContentType(.oneTimeCode).focused($focus)
                    .font(.system(size: 26, weight: .heavy)).kerning(10).multilineTextAlignment(.center)
                    .frame(height: 56).background(Color.surface, in: RoundedRectangle(cornerRadius: 14))
                    .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color(hex: 0xD9D2C5), lineWidth: 1.5))
                    .onChange(of: code) {
                        let clean = String(code.filter(\.isNumber).prefix(6))
                        if clean != code { code = clean }
                        if clean.count == 6 { verify() }
                    }
                Button(action: verify) { if busy { ProgressView().tint(.white) } else { Text("Verify") } }
                    .buttonStyle(PrimaryButtonStyle()).disabled(busy || code.count != 6)
                HStack {
                    Button("Change number") { self.sent = nil; code = "" }
                    Spacer()
                    Button("Resend code") { send() }.disabled(busy)
                }
                .font(.subheadline.weight(.heavy))
            } else {
                Text("Mobile number").font(.subheadline.weight(.bold)).foregroundStyle(Color.ink2)
                HStack(spacing: 6) {
                    Text("+91").font(.title3.weight(.bold)).foregroundStyle(Color.muted)
                    TextField("98765 43210", text: $phone).keyboardType(.phonePad).textContentType(.telephoneNumber)
                        .font(.title3).focused($focus)
                }
                .padding(.horizontal, 16).frame(height: 56)
                .background(Color.surface, in: RoundedRectangle(cornerRadius: 14))
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(focus ? Color.brand : Color(hex: 0xD9D2C5), lineWidth: 1.5))
                Button(action: send) { if busy { ProgressView().tint(.white) } else { Text("Send code") } }
                    .buttonStyle(PrimaryButtonStyle()).disabled(busy || phone.filter(\.isNumber).count < 10)
            }
            if let error { Text(error).font(.subheadline.weight(.semibold)).foregroundStyle(Color.alert600) }
        }
        .padding(18)
        .background(Color.surface, in: RoundedRectangle(cornerRadius: 24))
        .overlay(RoundedRectangle(cornerRadius: 24).stroke(Color.line))
    }

    private func send() {
        busy = true
        error = nil
        Task {
            do {
                sent = try await API.sendOtp(sent?.phone ?? phone)
                code = ""
                focus = true
            } catch let e as APIError {
                error = e.status == 422 ? "Enter a valid 10-digit Indian mobile number"
                    : e.status == 429 ? "Too many tries. Please wait a little." : e.message
            } catch {}
            busy = false
        }
    }

    private func verify() {
        guard let sent, !busy else { return }
        busy = true
        error = nil
        Task {
            do {
                app.user = try await API.verifyOtp(sent.phone, code: code)
                await app.loadBookings()
                UINotificationFeedbackGenerator().notificationOccurred(.success)
            } catch let e as APIError {
                error = e.status == 429 ? "Too many wrong tries. Ask for a new code." : "That code didn’t work. Check it and try again."
                code = ""
            } catch {}
            busy = false
        }
    }
}

/// The API runs on a laptop during the hackathon; a real phone needs the laptop's LAN address here.
private struct ServerSetting: View {
    @Environment(AppState.self) private var app
    @State private var base = API.base
    @State private var open = false

    var body: some View {
        DisclosureGroup(isExpanded: $open) {
            VStack(alignment: .leading, spacing: 8) {
                TextField("http://192.168.1.10:8000", text: $base)
                    .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    .padding(12).background(Color.surface, in: RoundedRectangle(cornerRadius: 12))
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color(hex: 0xD9D2C5)))
                HStack {
                    Button("Save") { API.base = base; Task { await app.loadAccount() } }.font(.subheadline.weight(.heavy))
                    Spacer()
                    Button("Reset") { base = API.defaultBase; API.base = base; Task { await app.loadAccount() } }.font(.subheadline)
                }
            }
            .padding(.top, 8)
        } label: {
            Label("Server", systemImage: "server.rack").font(.subheadline.weight(.bold)).foregroundStyle(Color.muted)
        }
        .padding(.top, 8)
    }
}
