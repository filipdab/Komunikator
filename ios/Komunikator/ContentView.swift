// Ekrany aplikacji: logowanie, czat, emotki, lista osób i „O programie”.

import SwiftUI

struct ContentView: View {
    @StateObject private var chat = ChatModel()

    var body: some View {
        NavigationStack {
            if chat.state == .connected {
                ChatView(chat: chat)
            } else {
                LoginView(chat: chat)
            }
        }
    }
}

// MARK: - Logowanie

struct LoginView: View {
    @ObservedObject var chat: ChatModel
    @AppStorage("nick") private var nick = ""
    @AppStorage("host") private var host = ""
    @AppStorage("port") private var port = 5050
    @State private var showAbout = false

    private var canConnect: Bool {
        chat.state == .disconnected
            && !nick.trimmingCharacters(in: .whitespaces).isEmpty
            && !host.trimmingCharacters(in: .whitespaces).isEmpty
    }

    var body: some View {
        Form {
            Section("Twój nick") {
                TextField("Nick", text: $nick)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            }
            Section {
                TextField("Adres serwera, np. 192.168.1.111", text: $host)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                TextField("Port", value: $port, format: .number.grouping(.never))
                    .keyboardType(.numberPad)
            } header: {
                Text("Serwer")
            } footer: {
                if !chat.lastNotice.isEmpty {
                    Text(chat.lastNotice)
                }
            }
            Section {
                Button {
                    chat.connect(host: host.trimmingCharacters(in: .whitespaces), port: port,
                                 nick: nick.trimmingCharacters(in: .whitespaces))
                } label: {
                    HStack {
                        Text(chat.state == .connecting ? "Łączenie…" : "Połącz")
                        Spacer()
                        if chat.state == .connecting {
                            ProgressView()
                        }
                    }
                }
                .disabled(!canConnect)
                if chat.state == .connecting {
                    Button("Anuluj", role: .cancel) { chat.disconnect() }
                }
            }
        }
        .navigationTitle("Komunikator")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { showAbout = true } label: { Image(systemName: "info.circle") }
            }
        }
        .sheet(isPresented: $showAbout) { AboutView() }
    }
}

// MARK: - Czat

struct ChatView: View {
    @ObservedObject var chat: ChatModel
    @State private var draft = ""
    @State private var showEmoji = false
    @State private var showUsers = false
    @State private var showAbout = false

    private var canSend: Bool {
        !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 6) {
                        ForEach(chat.lines) { line in
                            ChatLineView(line: line)
                                .id(line.id)
                        }
                    }
                    .padding(.horizontal)
                    .padding(.vertical, 8)
                }
                .scrollDismissesKeyboard(.interactively)
                .onChange(of: chat.lines.count) { _, _ in
                    if let last = chat.lines.last {
                        withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                    }
                }
                .onAppear {
                    if let last = chat.lines.last {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }

            if !chat.typingText.isEmpty {
                Text(chat.typingText)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal)
                    .padding(.bottom, 4)
            }

            Divider()
            VoiceBar(chat: chat)
            HStack(alignment: .bottom, spacing: 8) {
                Button { showEmoji.toggle() } label: {
                    Text("😊").font(.title2)
                }
                .popover(isPresented: $showEmoji) {
                    EmojiPicker { draft += $0 }
                        .presentationCompactAdaptation(.popover)
                }
                TextField("Wiadomość", text: $draft, axis: .vertical)
                    .lineLimit(1...5)
                    .textFieldStyle(.roundedBorder)
                    .onChange(of: draft) { _, newValue in chat.userTyped(newValue) }
                Button(action: send) {
                    Image(systemName: "paperplane.fill").font(.title2)
                }
                .disabled(!canSend)
            }
            .padding(8)
        }
        .navigationTitle("Komunikator")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button("Rozłącz") { chat.disconnect() }
            }
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button { showUsers = true } label: {
                    Label("Osoby (\(chat.users.count))", systemImage: "person.2")
                }
                Button { showAbout = true } label: { Image(systemName: "info.circle") }
            }
        }
        .sheet(isPresented: $showUsers) { UsersView(chat: chat) }
        .sheet(isPresented: $showAbout) { AboutView() }
    }

    private func send() {
        chat.sendMessage(draft)
        draft = ""
    }
}

struct VoiceBar: View {
    @ObservedObject var chat: ChatModel

    var body: some View {
        HStack(spacing: 10) {
            Button {
                Task { await chat.toggleVoice() }
            } label: {
                Label(chat.inVoice ? "Opuść rozmowę" : "Dołącz do rozmowy",
                      systemImage: chat.inVoice ? "phone.down.fill" : "phone.fill")
            }
            .buttonStyle(.bordered)
            .tint(chat.inVoice ? .red : .green)

            if chat.inVoice {
                Button {
                    chat.micMuted.toggle()
                } label: {
                    Image(systemName: chat.micMuted ? "mic.slash.fill" : "mic.fill")
                }
                .buttonStyle(.bordered)
                .accessibilityLabel(chat.micMuted ? "Włącz mikrofon" : "Wycisz mikrofon")
                ProgressView(value: chat.micLevel)
                    .frame(maxWidth: 70)
            }
            Spacer()
            if !chat.voiceUsers.isEmpty {
                Label("\(chat.voiceUsers.count)", systemImage: "speaker.wave.2.fill")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal)
        .padding(.top, 6)
    }
}

struct ChatLineView: View {
    let line: ChatLine

    private static let clock: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm"
        return f
    }()

    var body: some View {
        switch line.kind {
        case .system:
            Text("[\(Self.clock.string(from: line.time))] * \(line.text)")
                .font(.footnote)
                .italic()
                .foregroundStyle(.secondary)
        case .message(let nick, let mine):
            Text(messageText(nick: nick, mine: mine))
                .textSelection(.enabled)
        }
    }

    private func messageText(nick: String, mine: Bool) -> AttributedString {
        var time = AttributedString("[\(Self.clock.string(from: line.time))] ")
        time.foregroundColor = Color.gray
        var name = AttributedString("\(nick): ")
        name.font = Font.body.bold()
        name.foregroundColor = mine ? Color.green : Color.blue
        return time + name + linkified(line.text)
    }
}

/// Zamienia adresy w tekście na klikalne linki (http://, https://, www.).
func linkified(_ text: String) -> AttributedString {
    var result = AttributedString(text)
    guard let detector = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue) else {
        return result
    }
    let matches = detector.matches(in: text, range: NSRange(text.startIndex..., in: text))
    for match in matches {
        guard let url = match.url,
              let range = Range(match.range, in: text),
              let lower = AttributedString.Index(range.lowerBound, within: result),
              let upper = AttributedString.Index(range.upperBound, within: result) else { continue }
        result[lower..<upper].link = url
        result[lower..<upper].underlineStyle = .single
    }
    return result
}

// MARK: - Emotki

let emojiList = """
😀 😃 😄 😁 😆 😅 😂 🤣 😊 🙂 😉 😍 😘 😎 🤓 🤔 \
😐 😏 😴 😮 😢 😭 😡 🤯 😱 🥳 🙃 😇 🤗 🤫 🙄 😬 \
👍 👎 👏 🙌 🙏 💪 👋 ✌️ 👌 🤝 ❤️ 💔 🔥 ⭐ 🎉 🎂 \
☕ 🍺 🍕 ⚽ 🎮 🎵 💻 📱 ✅ ❌ ❓ ❗ 💯 👀 🚀 🤖
""".split(separator: " ").map(String.init)

struct EmojiPicker: View {
    let onPick: (String) -> Void

    var body: some View {
        ScrollView {
            LazyVGrid(columns: Array(repeating: GridItem(.fixed(38)), count: 8), spacing: 6) {
                ForEach(emojiList, id: \.self) { emoji in
                    Button { onPick(emoji) } label: {
                        Text(emoji).font(.title2)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(10)
        }
        .frame(width: 340, height: 300)
    }
}

// MARK: - Osoby

struct UsersView: View {
    @ObservedObject var chat: ChatModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section("Na czacie") {
                    ForEach(chat.users, id: \.self) { Text($0) }
                }
                Section("W rozmowie głosowej") {
                    if chat.voiceUsers.isEmpty {
                        Text("Nikt").foregroundStyle(.secondary)
                    } else {
                        ForEach(chat.voiceUsers, id: \.self) { Text($0) }
                    }
                }
            }
            .navigationTitle("Osoby")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Gotowe") { dismiss() }
                }
            }
        }
    }
}

// MARK: - O programie

struct AboutView: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(spacing: 10) {
                Text("Komunikator").font(.largeTitle.bold())
                Text("Wersja \(appVersion) na iOS").foregroundStyle(.secondary)
                Text("Autor: \(appAuthor)").padding(.top, 12)
                Link(appEmail, destination: URL(string: "mailto:\(appEmail)")!)
                Link("www.dabrowski.in", destination: URL(string: appWebsite)!)
                Spacer()
            }
            .padding(.top, 40)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Zamknij") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium])
    }
}
