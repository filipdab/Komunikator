// Rozmowa głosowa: mikrofon → PCM 16 kHz → serwer, a od rozmówców → miksowanie → głośnik.
// Typy są nonisolated, bo pracują w wątkach audio (wymaga Xcode 26 / Swift 6.2).
// Format jak w voice.py: mono, 16 bitów, ramki po 320 próbek (20 ms), base64 w JSON.

import AVFoundation

nonisolated enum VoiceError: LocalizedError {
    case noMicrophone

    var errorDescription: String? { "Nie znaleziono mikrofonu." }
}

/// Bufory ramek od każdego rozmówcy, wyrównujące nierówne tempo sieci.
nonisolated final class JitterBuffer: @unchecked Sendable {
    static let frameSamples = 320
    private static let prebuffer = 3   // ile ramek zebrać, zanim zaczniemy odtwarzać rozmówcę
    private static let maxBuffer = 12  // powyżej tego opóźnienie rośnie, więc odrzucamy najstarsze

    private let lock = NSLock()
    private var buffers: [String: [[Int16]]] = [:]
    private var playing = Set<String>()
    private var current: [Int16] = []
    private var position = 0

    func push(nick: String, frame: [Int16]) {
        guard frame.count == Self.frameSamples else { return }
        lock.lock()
        defer { lock.unlock() }
        var queue = buffers[nick, default: []]
        queue.append(frame)
        if queue.count > Self.maxBuffer {
            queue.removeFirst(queue.count - Self.prebuffer)
        }
        buffers[nick] = queue
    }

    func keepOnly(_ nicks: [String]) {
        lock.lock()
        defer { lock.unlock() }
        buffers = buffers.filter { nicks.contains($0.key) }
        playing = playing.filter { nicks.contains($0) }
    }

    func clear() {
        lock.lock()
        defer { lock.unlock() }
        buffers = [:]
        playing = []
        current = []
        position = 0
    }

    /// Wypełnia bufor wyjściowy próbkami (wywoływane z wątku audio).
    func render(into out: UnsafeMutablePointer<Float>, count: Int) {
        lock.lock()
        defer { lock.unlock() }
        var written = 0
        while written < count {
            if position >= current.count {
                current = nextMixedFrame()
                position = 0
            }
            let n = min(count - written, current.count - position)
            for k in 0..<n {
                out[written + k] = Float(current[position + k]) / 32768
            }
            written += n
            position += n
        }
    }

    private func nextMixedFrame() -> [Int16] {
        var mixed = [Int](repeating: 0, count: Self.frameSamples)
        for nick in Array(buffers.keys) {
            guard var queue = buffers[nick] else { continue }
            if !playing.contains(nick) {
                if queue.count < Self.prebuffer { continue }
                playing.insert(nick)
            }
            if queue.isEmpty {
                playing.remove(nick) // bufor pusty: znowu czekamy na zapas
                continue
            }
            let frame = queue.removeFirst()
            buffers[nick] = queue
            for i in 0..<Self.frameSamples {
                mixed[i] += Int(frame[i])
            }
        }
        return mixed.map { Int16(clamping: $0) }
    }
}

nonisolated final class VoiceEngine: @unchecked Sendable {
    private nonisolated final class Flag: @unchecked Sendable { var done = false }

    private let engine = AVAudioEngine()
    private let jitter = JitterBuffer()
    private let sendFrame: @Sendable (String) -> Void
    private let netFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000,
                                          channels: 1, interleaved: true)!
    private var converter: AVAudioConverter?
    private var pending: [Int16] = []  // tylko z wątku mikrofonu

    private let lock = NSLock()
    private var _muted = false
    private var _level = 0
    private var _maxLevel = 0

    var muted: Bool {
        get { lock.lock(); defer { lock.unlock() }; return _muted }
        set { lock.lock(); _muted = newValue; lock.unlock() }
    }

    /// Poziom ostatniej ramki z mikrofonu (0–32767).
    var level: Int { lock.lock(); defer { lock.unlock() }; return _level }

    /// Najwyższy poziom od dołączenia; 0 znaczy, że mikrofon daje samą ciszę.
    var maxLevel: Int { lock.lock(); defer { lock.unlock() }; return _maxLevel }

    init(sendFrame: @escaping @Sendable (String) -> Void) {
        self.sendFrame = sendFrame
    }

    func start() throws {
        let session = AVAudioSession.sharedInstance()
        // .voiceChat włącza usuwanie echa, więc da się rozmawiać na głośniku
        try session.setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker, .allowBluetooth])
        try session.setActive(true)

        let input = engine.inputNode
        try? input.setVoiceProcessingEnabled(true)
        let inputFormat = input.outputFormat(forBus: 0)
        guard inputFormat.sampleRate > 0, inputFormat.channelCount > 0 else { throw VoiceError.noMicrophone }
        converter = AVAudioConverter(from: inputFormat, to: netFormat)
        input.installTap(onBus: 0, bufferSize: 1024, format: inputFormat) { [weak self] buffer, _ in
            self?.process(buffer)
        }

        let playFormat = AVAudioFormat(standardFormatWithSampleRate: 16000, channels: 1)!
        let jitter = self.jitter
        let source = AVAudioSourceNode(format: playFormat) { _, _, frameCount, audioBufferList -> OSStatus in
            let buffers = UnsafeMutableAudioBufferListPointer(audioBufferList)
            if let data = buffers.first?.mData {
                jitter.render(into: data.assumingMemoryBound(to: Float.self), count: Int(frameCount))
            }
            return noErr
        }
        engine.attach(source)
        engine.connect(source, to: engine.mainMixerNode, format: playFormat)
        engine.prepare()
        try engine.start()
    }

    func stop() {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        jitter.clear()
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    /// Ramka od innego rozmówcy (base64).
    func receive(nick: String, base64: String) {
        guard let data = Data(base64Encoded: base64), data.count == JitterBuffer.frameSamples * 2 else { return }
        var frame = [Int16](repeating: 0, count: JitterBuffer.frameSamples)
        _ = frame.withUnsafeMutableBytes { data.copyBytes(to: $0) }
        jitter.push(nick: nick, frame: frame)
    }

    func keepOnly(_ nicks: [String]) {
        jitter.keepOnly(nicks)
    }

    private func process(_ buffer: AVAudioPCMBuffer) {
        guard let converter else { return }
        let ratio = netFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 64
        guard let out = AVAudioPCMBuffer(pcmFormat: netFormat, frameCapacity: capacity) else { return }

        let fed = Flag()
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if fed.done {
                status.pointee = .noDataNow
                return nil
            }
            fed.done = true
            status.pointee = .haveData
            return buffer
        }
        guard error == nil, let samples = out.int16ChannelData?[0] else { return }
        pending.append(contentsOf: UnsafeBufferPointer(start: samples, count: Int(out.frameLength)))

        let size = JitterBuffer.frameSamples
        while pending.count >= size {
            let frame = Array(pending[0..<size])
            pending.removeFirst(size)
            let peak = frame.reduce(0) { max($0, abs(Int($1))) }
            lock.lock()
            _level = peak
            _maxLevel = max(_maxLevel, peak)
            let isMuted = _muted
            lock.unlock()
            if !isMuted {
                let data = frame.withUnsafeBufferPointer { Data(buffer: $0) }
                sendFrame(data.base64EncodedString())
            }
        }
    }
}
