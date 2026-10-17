// Połączenie z serwerem komunikatora i stan czatu.
// Protokół jest ten sam co w client.py: jedna wiadomość JSON na linię, przez TCP.

import AVFoundation
import Foundation
import Network

let appVersion = "0.4"
let appAuthor = "Filip Dąbrowski"
let appEmail = "filip@dabrowski.in"
let appWebsite = "https://www.dabrowski.in"

struct ChatLine: Identifiable {
    enum Kind {
        case message(nick: String, mine: Bool)
        case system
    }

    let id = UUID()
    let kind: Kind
    let text: String
    let time: Date
}

@MainActor
final class ChatModel: ObservableObject {
    enum State { case disconnected, connecting, connected }

    @Published private(set) var state: State = .disconnected
    @Published private(set) var lines: [ChatLine] = []
    @Published private(set) var users: [String] = []
    @Published private(set) var voiceUsers: [String] = []
    @Published private(set) var typingUsers: [String: Date] = [:]
    @Published private(set) var myNick: String?
    @Published private(set) var lastNotice = ""
    @Published private(set) var inVoice = false
    @Published private(set) var micLevel = 0.0   // 0–1, do paska poziomu mikrofonu
    @Published var micMuted = false {
        didSet { voice?.muted = micMuted }
    }

    private var connection: NWConnection?
    private var buffer = Data()
    private var errorReason: String?
    private var lastTypingSent = Date.distantPast
    private var typingStopTask: Task<Void, Never>?
    private var expireTask: Task<Void, Never>?
    private var voice: VoiceEngine?
    private var meterTask: Task<Void, Never>?

    var typingText: String {
        let nicks = typingUsers.keys.sorted()
        switch nicks.count {
        case 0: return ""
        case 1: return "\(nicks[0]) pisze…"
        case 2...3: return nicks.dropLast().joined(separator: ", ") + " i \(nicks.last!) piszą…"
        default: return "Kilka osób pisze…"
        }
    }

    // MARK: - Połączenie

    func connect(host: String, port: Int, nick: String) {
        disconnect()
        guard (1...65535).contains(port), let nwPort = NWEndpoint.Port(rawValue: UInt16(port)) else {
            addSystem("Port musi być liczbą od 1 do 65535.")
            return
        }
        let conn = NWConnection(host: NWEndpoint.Host(host), port: nwPort, using: .tcp)
        connection = conn
        buffer = Data()
        errorReason = nil
        state = .connecting
        addSystem("Łączenie z \(host):\(port)…")

        conn.stateUpdateHandler = { [weak self] newState in
            Task { @MainActor in self?.handle(newState, of: conn, nick: nick) }
        }
        conn.start(queue: .global(qos: .userInitiated))

        expireTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                self?.expireTyping()
            }
        }
    }

    func disconnect() {
        stopVoice(notifyServer: false)
        connection?.cancel()
        connection = nil
        expireTask?.cancel()
        typingStopTask?.cancel()
        state = .disconnected
        users = []
        voiceUsers = []
        typingUsers = [:]
        lastTypingSent = .distantPast
    }

    private func close(reason: String) {
        guard connection != nil else { return }
        addSystem(errorReason ?? reason)
        disconnect()
    }

    private func handle(_ newState: NWConnection.State, of conn: NWConnection, nick: String) {
        guard conn === connection else { return }
        switch newState {
        case .ready:
            send(["type": "join", "nick": nick])
            receive(on: conn)
        case .waiting(let error):
            close(reason: "Serwer jest niedostępny: \(error.localizedDescription)")
        case .failed(let error):
            close(reason: "Nie udało się połączyć: \(error.localizedDescription)")
        default:
            break
        }
    }

    private func receive(on conn: NWConnection) {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, isComplete, error in
            Task { @MainActor in
                guard let self, conn === self.connection else { return }
                if let data, !data.isEmpty {
                    self.consume(data)
                }
                if isComplete || error != nil {
                    self.close(reason: "Połączenie z serwerem zostało zamknięte.")
                } else {
                    self.receive(on: conn)
                }
            }
        }
    }

    private func consume(_ data: Data) {
        buffer.append(data)
        while let newline = buffer.firstIndex(of: 0x0A) {
            let line = buffer[buffer.startIndex..<newline]
            buffer.removeSubrange(buffer.startIndex...newline)
            if let event = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any] {
                handle(event: event)
            }
        }
        if buffer.count > 64 * 1024 {
            close(reason: "Serwer przysłał za długą wiadomość.")
        }
    }

    private func handle(event: [String: Any]) {
        let time = (event["time"] as? Double).map { Date(timeIntervalSince1970: $0) } ?? Date()
        switch event["type"] as? String {
        case "welcome":
            myNick = event["nick"] as? String
            state = .connected
            addSystem("Połączono jako \(myNick ?? "?").")
        case "msg":
            let nick = event["nick"] as? String ?? "?"
            typingUsers[nick] = nil
            lines.append(ChatLine(kind: .message(nick: nick, mine: nick == myNick),
                                  text: event["text"] as? String ?? "", time: time))
        case "system":
            addSystem(event["text"] as? String ?? "", time: time)
        case "users":
            users = event["users"] as? [String] ?? []
            typingUsers = typingUsers.filter { users.contains($0.key) }
        case "voice_users":
            voiceUsers = event["users"] as? [String] ?? []
            voice?.keepOnly(voiceUsers)
        case "audio":
            if let nick = event["nick"] as? String, let data = event["data"] as? String {
                voice?.receive(nick: nick, base64: data)
            }
        case "typing":
            guard let nick = event["nick"] as? String, nick != myNick else { return }
            typingUsers[nick] = (event["active"] as? Bool ?? false) ? Date() : nil
        case "error":
            errorReason = event["text"] as? String
        default:
            break
        }
    }

    private func send(_ object: [String: Any]) {
        guard let conn = connection,
              var data = try? JSONSerialization.data(withJSONObject: object) else { return }
        data.append(0x0A)
        conn.send(content: data, completion: .contentProcessed { _ in })
    }

    // MARK: - Wiadomości i sygnał pisania

    func sendMessage(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard state == .connected, !trimmed.isEmpty else { return }
        send(["type": "msg", "text": trimmed])
        stopTyping()
    }

    func userTyped(_ text: String) {
        guard state == .connected else { return }
        typingStopTask?.cancel()
        if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            stopTyping()
            return
        }
        if Date().timeIntervalSince(lastTypingSent) > 2 {
            lastTypingSent = Date()
            send(["type": "typing", "active": true])
        }
        typingStopTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            guard !Task.isCancelled else { return }
            self?.stopTyping()
        }
    }

    private func stopTyping() {
        typingStopTask?.cancel()
        if lastTypingSent != .distantPast {
            send(["type": "typing", "active": false])
        }
        lastTypingSent = .distantPast
    }

    private func expireTyping() {
        let now = Date()
        let fresh = typingUsers.filter { now.timeIntervalSince($0.value) < 6 }
        if fresh.count != typingUsers.count {
            typingUsers = fresh
        }
    }

    // MARK: - Rozmowa głosowa

    func toggleVoice() async {
        if inVoice {
            stopVoice(notifyServer: true)
            addSystem("Opuściłeś rozmowę głosową.")
            return
        }
        guard state == .connected, let conn = connection else { return }
        guard await AVAudioApplication.requestRecordPermission() else {
            addSystem("Brak dostępu do mikrofonu. Włącz go w Ustawieniach → Prywatność i ochrona → Mikrofon → Komunikator.")
            return
        }
        guard conn === connection else { return } // rozłączono w trakcie pytania o zgodę
        // Ramki dźwięku idą prosto z wątku mikrofonu, bez przechodzenia przez wątek główny.
        let engine = VoiceEngine { base64 in
            let line = "{\"type\":\"audio\",\"data\":\"\(base64)\"}\n"
            conn.send(content: Data(line.utf8), completion: .contentProcessed { _ in })
        }
        engine.muted = micMuted
        do {
            try engine.start()
        } catch {
            engine.stop()
            addSystem("Nie udało się włączyć mikrofonu lub głośnika: \(error.localizedDescription)")
            return
        }
        voice = engine
        inVoice = true
        send(["type": "voice_join"])
        addSystem("Dołączyłeś do rozmowy głosowej.")

        let started = Date()
        meterTask = Task { [weak self] in
            var warned = false
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 100_000_000)
                guard let self, let voice = self.voice, voice === engine else { return }
                let level = Double(voice.level)
                // skala logarytmiczna, żeby cichy głos też poruszał paskiem
                self.micLevel = level < 30 ? 0 : min(1, log10(level / 30) / log10(32767.0 / 30))
                if !warned, voice.maxLevel == 0, Date().timeIntervalSince(started) > 4 {
                    warned = true
                    self.addSystem("Mikrofon nic nie nagrywa (sama cisza). Sprawdź dostęp do mikrofonu w Ustawieniach.")
                }
            }
        }
    }

    private func stopVoice(notifyServer: Bool) {
        guard let engine = voice else { return }
        voice = nil
        meterTask?.cancel()
        engine.stop()
        inVoice = false
        micLevel = 0
        if notifyServer {
            send(["type": "voice_leave"])
        }
    }

    private func addSystem(_ text: String, time: Date = Date()) {
        lines.append(ChatLine(kind: .system, text: text, time: time))
        lastNotice = text
    }
}
