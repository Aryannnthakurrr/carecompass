import AVFoundation
import Observation

// Voice in (16 kHz mono WAV -> /stt, Sarvam Saaras), voice out (/tts WAV, Sarvam Bulbul, with the
// system voice as fallback) and the emergency siren.

@MainActor @Observable
final class VoiceRecorder {
    var level: Float = 0          // 0...1, drives the orb animation
    private(set) var recording = false
    private var recorder: AVAudioRecorder?
    private var meter: Task<Void, Never>?
    private let url = FileManager.default.temporaryDirectory.appendingPathComponent("turn.wav")

    /// Starts recording; `onSilence` fires once the speaker has talked and then paused ~1.6 s.
    func start(onSilence: @escaping @MainActor () -> Void) async -> Bool {
        guard await AVAudioApplication.requestRecordPermission() else { return false }
        do {
            let s = AVAudioSession.sharedInstance()
            try s.setCategory(.playAndRecord, mode: .spokenAudio, options: [.defaultToSpeaker, .allowBluetoothHFP])
            try s.setActive(true)
            let r = try AVAudioRecorder(url: url, settings: [
                AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 16_000, AVNumberOfChannelsKey: 1,
                AVLinearPCMBitDepthKey: 16, AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false,
            ])
            r.isMeteringEnabled = true
            guard r.record() else { return false }
            recorder = r
        } catch {
            return false
        }
        recording = true
        let started = Date()
        meter = Task { [weak self] in
            var heard = false, quietSince: Date?
            while !Task.isCancelled, let self, let r = self.recorder {
                r.updateMeters()
                let db = r.averagePower(forChannel: 0)                 // -160...0 dBFS
                let lvl = max(0, min(1, (db + 50) / 40))
                self.level = self.level * 0.6 + lvl * 0.4
                if lvl > 0.35 { heard = true; quietSince = nil } else if heard { quietSince = quietSince ?? Date() }
                if let q = quietSince, Date().timeIntervalSince(q) > 1.6 { onSilence(); return }
                if Date().timeIntervalSince(started) > 30 { onSilence(); return }
                try? await Task.sleep(for: .milliseconds(50))
            }
        }
        return true
    }

    /// Stops and returns the WAV bytes (nil if nothing usable was recorded).
    func stop() -> Data? {
        meter?.cancel()
        recorder?.stop()
        recorder = nil
        recording = false
        level = 0
        guard let d = try? Data(contentsOf: url), d.count > 8_000 else { return nil }  // < ~0.25 s is a mis-tap
        return d
    }

    func cancel() { _ = stop() }
}

@MainActor
final class Speaker: NSObject, AVAudioPlayerDelegate, AVSpeechSynthesizerDelegate {
    static let shared = Speaker()
    private var player: AVAudioPlayer?
    private let synth = AVSpeechSynthesizer()
    private var done: CheckedContinuation<Void, Never>?

    override init() {
        super.init()
        synth.delegate = self
    }

    /// Speaks and returns when finished (or stopped).
    func say(_ text: String, language: String) async {
        stop()
        try? AVAudioSession.sharedInstance().setCategory(.playAndRecord, mode: .spokenAudio, options: [.defaultToSpeaker])
        try? AVAudioSession.sharedInstance().setActive(true)
        if let wav = try? await API.tts(text, language: language == "en" ? "en" : language == "hi" ? "hi" : "hinglish"),
           let p = try? AVAudioPlayer(data: wav) {
            player = p
            p.delegate = self
            await withCheckedContinuation { c in done = c; p.play() }
            return
        }
        let u = AVSpeechUtterance(string: text)
        u.voice = AVSpeechSynthesisVoice(language: language == "en" ? "en-IN" : "hi-IN")
        await withCheckedContinuation { c in done = c; synth.speak(u) }
    }

    func stop() {
        player?.stop()
        player = nil
        if synth.isSpeaking { synth.stopSpeaking(at: .immediate) }
        resume()
    }

    private func resume() {
        done?.resume()
        done = nil
    }

    nonisolated func audioPlayerDidFinishPlaying(_ p: AVAudioPlayer, successfully: Bool) { Task { @MainActor in self.resume() } }
    nonisolated func speechSynthesizer(_ s: AVSpeechSynthesizer, didFinish u: AVSpeechUtterance) { Task { @MainActor in self.resume() } }
    nonisolated func speechSynthesizer(_ s: AVSpeechSynthesizer, didCancel u: AVSpeechUtterance) { Task { @MainActor in self.resume() } }
}

/// Rising/falling wail (650 ↔ 1450 Hz, one cycle per second), synthesised, so there is no audio file to ship.
final class Siren: @unchecked Sendable {
    private let engine = AVAudioEngine()
    private var node: AVAudioSourceNode?

    func start() {
        stop()
        try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .default, options: [])
        try? AVAudioSession.sharedInstance().setActive(true)
        let rate = engine.outputNode.outputFormat(forBus: 0).sampleRate
        let node = AVAudioSourceNode(renderBlock: Siren.render(rate: rate))
        engine.attach(node)
        engine.connect(node, to: engine.mainMixerNode, format: AVAudioFormat(standardFormatWithSampleRate: rate, channels: 1))
        self.node = node
        try? engine.start()
    }

    func stop() {
        engine.stop()
        if let node { engine.detach(node) }
        node = nil
    }

    // Runs on the real-time audio thread; state lives in the closure.
    private static func render(rate: Double) -> AVAudioSourceNodeRenderBlock {
        var phase = 0.0, t = 0.0
        return { _, _, frames, abl in
            for f in 0..<Int(frames) {
                let cycle = t.truncatingRemainder(dividingBy: 1.0)
                let tri = cycle < 0.5 ? cycle * 2 : (1 - cycle) * 2
                phase += (650 + 800 * tri) / rate
                if phase >= 1 { phase -= 1 }
                let sample = Float(2 * phase - 1) * 0.22
                for b in UnsafeMutableAudioBufferListPointer(abl) { b.mData?.assumingMemoryBound(to: Float.self)[f] = sample }
                t += 1 / rate
            }
            return noErr
        }
    }
}
