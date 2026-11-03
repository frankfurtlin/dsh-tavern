import Foundation
import UIKit
import AVFoundation
import MediaPlayer

/// The app owns playback; destroying a WebView does not destroy the synthesizer.
final class ReaderSpeechEngine: NSObject, AVSpeechSynthesizerDelegate {
    static let shared = ReaderSpeechEngine()
    private let queue = ReaderSpeechQueue()
    private let store = ReaderSpeechStateStore()
    private var synthesizer = AVSpeechSynthesizer()
    private var tickets: [ObjectIdentifier: String] = [:]
    private var listeners: [UUID: ([String: Any]) -> Void] = [:]
    private var observers: [NSObjectProtocol] = []
    private var commands: [(MPRemoteCommand, Any)] = []
    private var timer: DispatchWorkItem?
    private var startTimeout: DispatchWorkItem?
    private var resolvedVoiceId = ""
    private var voiceFallback = false
    private var sleepDeadline: TimeInterval?
    private var audioActive = false
    private var accountOwner: String?
    private var accountOrigin: String?
    private let commandLock = NSLock()
    private var commandGeneration = 0
    private let audio = AVAudioSession.sharedInstance()

    private override init() {
        super.init(); prepareSynthesizer()
        let notifications = NotificationCenter.default
        observers.append(notifications.addObserver(forName: AVAudioSession.interruptionNotification, object: audio, queue: .main) { [weak self] notification in
            guard let raw = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                  AVAudioSession.InterruptionType(rawValue: raw) == .began else { return }
            self?.pause(reason: "audio-interruption")
            // Ending an interruption never resumes speech automatically.
        })
        observers.append(notifications.addObserver(forName: AVAudioSession.routeChangeNotification, object: audio, queue: .main) { [weak self] notification in
            guard let raw = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
                  AVAudioSession.RouteChangeReason(rawValue: raw) == .oldDeviceUnavailable else { return }
            self?.pause(reason: "headphones-disconnected")
        })
        observers.append(notifications.addObserver(forName: AVAudioSession.mediaServicesWereLostNotification, object: audio, queue: .main) { [weak self] _ in self?.fail("VOICE_ENGINE_UNAVAILABLE") })
        observers.append(notifications.addObserver(forName: AVAudioSession.mediaServicesWereResetNotification, object: audio, queue: .main) { [weak self] _ in
            self?.pause(reason: "voice-engine-reset"); self?.prepareSynthesizer()
        })
    }
    private func prepareSynthesizer() {
        startTimeout?.cancel(); startTimeout = nil
        synthesizer.delegate = nil; synthesizer.stopSpeaking(at: .immediate); tickets.removeAll()
        synthesizer = AVSpeechSynthesizer(); synthesizer.usesApplicationAudioSession = true; synthesizer.delegate = self
    }
    func retryEngine() { pause(reason: "voice-engine-retry"); prepareSynthesizer() }
    private func installCommands() {
        guard accountOwner != nil, accountOrigin != nil, !queue.scopeKey.isEmpty else { return }
        let remote = MPRemoteCommandCenter.shared()
        register(remote.playCommand) { [weak self] in try self?.play() }
        register(remote.pauseCommand) { [weak self] in self?.pause(reason: "user-paused") }
        register(remote.stopCommand) { [weak self] in try self?.stop(clearPosition: false) }
        register(remote.previousTrackCommand) { [weak self] in try self?.skip(-1) }
        register(remote.nextTrackCommand) { [weak self] in try self?.skip(1) }
    }
    private func register(_ command: MPRemoteCommand, operation: @escaping () throws -> Void) {
        // Capture authority at registration. A framework-delayed old handler
        // must not borrow a new account's generation when it first enters.
        let generation = commandStamp(), owner = accountOwner, origin = accountOrigin, scope = queue.scopeKey
        command.isEnabled = true
        let target = command.addTarget { [weak self] _ in
            guard let self = self, generation == self.commandStamp() else { return .commandFailed }
            DispatchQueue.main.async {
                guard generation == self.commandStamp(), owner == self.accountOwner, origin == self.accountOrigin,
                      owner != nil, origin != nil, scope == self.queue.scopeKey else { return }
                do { try operation() } catch { self.fail(Self.code(error)) }
            }
            return .success
        }
        commands.append((command, target))
    }
    private func commandStamp() -> Int { commandLock.lock(); defer { commandLock.unlock() }; return commandGeneration }
    private func invalidateCommands() {
        commandLock.lock(); commandGeneration += 1; commandLock.unlock()
        for (command, target) in commands { command.removeTarget(target); command.isEnabled = false }
        commands.removeAll()
    }
    func bindAccount(owner: String, origin: String) throws {
        if accountOwner == owner && accountOrigin == origin { return }
        try stop(clearPosition: false)
        accountOwner = nil; accountOrigin = nil
        try store.authorize(owner: owner, origin: origin)
        accountOwner = owner; accountOrigin = origin
    }
    func revokeAccount() throws {
        defer { accountOwner = nil; accountOrigin = nil }
        try stop(clearPosition: true)
    }
    func addListener(_ listener: @escaping ([String: Any]) -> Void) -> UUID { let id = UUID(); listeners[id] = listener; return id }
    func removeListener(_ id: UUID) { listeners.removeValue(forKey: id) }
    func voices() -> [String: Any] {
        let values = AVSpeechSynthesisVoice.speechVoices().filter { $0.language.lowercased().hasPrefix("zh") }
        var result: [String: Any] = ["voices": values.map { ["id": $0.identifier, "name": $0.name, "language": $0.language, "offline": true] }, "available": !values.isEmpty, "initializing": false]
        if values.isEmpty { result["reason"] = "NO_CHINESE_OFFLINE_VOICE" }
        return result
    }
    private func selectedVoice() -> AVSpeechSynthesisVoice? {
        let values = AVSpeechSynthesisVoice.speechVoices().filter { $0.language.lowercased().hasPrefix("zh") }
        if let exact = values.first(where: { $0.identifier == queue.voiceId }) { voiceFallback = false; return exact }
        let fallback = values.first { $0.language == "zh-CN" } ?? values.first
        voiceFallback = !queue.voiceId.isEmpty && fallback != nil
        return fallback
    }
    func load(scope: String, title: String, segments: [ReaderSpeechQueue.Segment], fromId: String?, rate: Double, voice: String, endChapter: Bool, minutes: Int) throws {
        guard accountOwner != nil, accountOrigin != nil else { throw ReaderSpeechFailure.code("SPEECH_CONTEXT_CHANGED") }
        try ReaderSpeechQueue.checkSleep(minutes)
        try ReaderSpeechQueue().load(scope: scope, title: title, values: segments, fromId: fromId, rate: rate, voice: voice, endChapter: endChapter)
        invalidateCommands(); pause(reason: "loading")
        try queue.load(scope: scope, title: title, values: segments, fromId: fromId, rate: rate, voice: voice, endChapter: endChapter)
        resolvedVoiceId = ""; voiceFallback = false
        configureTimer(minutes); try persist(); installCommands(); publish()
    }
    func configure(rate: Double, voice: String, endChapter: Bool, minutes: Int) throws {
        try ReaderSpeechQueue.checkRate(rate); try ReaderSpeechQueue.checkSleep(minutes)
        guard voice.utf16.count <= 500 else { throw ReaderSpeechFailure.code("INVALID_SPEECH_VOICE") }
        let playing = queue.wantsPlayback; pause(reason: "preferences-changed")
        queue.rate = rate; queue.voiceId = voice; queue.stopAtChapterEnd = endChapter
        configureTimer(minutes); try persist(); if playing { try play() } else { publish() }
    }
    private func configureTimer(_ minutes: Int) {
        timer?.cancel(); timer = nil
        sleepDeadline = minutes == 0 ? nil : ProcessInfo.processInfo.systemUptime + Double(minutes * 60)
        if minutes == 0 { return }
        let work = DispatchWorkItem { [weak self] in self?.pause(reason: "sleep-timer") }
        timer = work; DispatchQueue.main.asyncAfter(deadline: .now() + Double(minutes * 60), execute: work)
    }
    func play() throws {
        guard let voice = selectedVoice() else { throw ReaderSpeechFailure.code("NO_CHINESE_OFFLINE_VOICE") }
        resolvedVoiceId = voice.identifier
        if let deadline = sleepDeadline, deadline <= ProcessInfo.processInfo.systemUptime { throw ReaderSpeechFailure.code("SLEEP_TIMER_EXPIRED") }
        guard queue.current != nil else { throw ReaderSpeechFailure.code("SPEECH_QUEUE_EMPTY") }
        do {
            try audio.setCategory(.playback, mode: .spokenAudio, options: [])
            do { try audio.setActive(true) } catch { throw ReaderSpeechFailure.code("AUDIO_FOCUS_UNAVAILABLE") }
            audioActive = true; UIApplication.shared.beginReceivingRemoteControlEvents()
            try queue.play(); try persist(); publish(); try speakNext()
        } catch { fail(Self.code(error)); throw error }
    }
    private func speakNext() throws {
        guard queue.wantsPlayback else { return }
        if let deadline = sleepDeadline, deadline <= ProcessInfo.processInfo.systemUptime { pause(reason: "sleep-timer"); return }
        guard let value = try queue.beginUtterance() else { return }
        guard let voice = selectedVoice() else { throw ReaderSpeechFailure.code("NO_CHINESE_OFFLINE_VOICE") }
        let utterance = AVSpeechUtterance(string: value.text); utterance.voice = voice
        utterance.rate = min(AVSpeechUtteranceMaximumSpeechRate, max(AVSpeechUtteranceMinimumSpeechRate, AVSpeechUtteranceDefaultSpeechRate * Float(queue.rate)))
        tickets[ObjectIdentifier(utterance)] = value.ticket; synthesizer.speak(utterance)
        startTimeout?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self = self, self.queue.accepts(value.ticket) else { return }
            self.fail("VOICE_ENGINE_UNAVAILABLE")
        }
        startTimeout = work; DispatchQueue.main.asyncAfter(deadline: .now() + 15, execute: work)
    }
    func pause(reason: String) {
        startTimeout?.cancel(); startTimeout = nil
        queue.pause(reason); tickets.removeAll(); synthesizer.stopSpeaking(at: .immediate); releaseAudio()
        do { if !queue.scopeKey.isEmpty { try persist() } } catch { queue.fail("SPEECH_POSITION_SAVE_FAILED") }
        publish()
    }
    func skip(_ direction: Int) throws {
        guard direction == -1 || direction == 1 else { throw ReaderSpeechFailure.code("INVALID_SPEECH_DIRECTION") }
        let playing = queue.wantsPlayback; pause(reason: "selected")
        try queue.move(direction); try persist(); if playing { try play() } else { publish() }
    }
    func stop(clearPosition: Bool) throws {
        invalidateCommands()
        pause(reason: "stopped"); queue.clear(); timer?.cancel(); timer = nil; sleepDeadline = nil
        // Even a Keychain deletion failure must remove the old account's
        // lock-screen title and remote reception, then publish the empty queue.
        // The typed store error still reaches the caller for explicit recovery.
        defer {
            MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
            UIApplication.shared.endReceivingRemoteControlEvents()
            publish()
        }
        if clearPosition { try store.clear() }
    }
    private func releaseAudio() { if audioActive { try? audio.setActive(false, options: .notifyOthersOnDeactivation); audioActive = false } }
    private func fail(_ code: String) {
        startTimeout?.cancel(); startTimeout = nil
        queue.fail(code); tickets.removeAll(); synthesizer.stopSpeaking(at: .immediate); releaseAudio()
        do { if !queue.scopeKey.isEmpty { try persist() } } catch { queue.fail("SPEECH_POSITION_SAVE_FAILED") }
        publish()
    }
    static func code(_ error: Error) -> String { (error as? ReaderSpeechFailure)?.code ?? "SYSTEM_SPEECH_FAILED" }
    func state(scope: String) throws -> [String: Any] {
        if !queue.scopeKey.isEmpty, queue.scopeKey != scope { try stop(clearPosition: true); return ["status": "idle", "queueLoaded": false] }
        if queue.scopeKey.isEmpty {
            guard let stored = try store.read(scope: scope), let owner = accountOwner, let origin = accountOrigin,
                  let value = ReaderSpeechContextGate.restoredMarker(stored, scope: scope, owner: owner, origin: origin)
            else { return ["status": "idle", "queueLoaded": false] }
            return value
        }
        return snapshot()
    }
    private func snapshot() -> [String: Any] {
        var value: [String: Any] = ["scopeKey": queue.scopeKey, "title": queue.title, "status": queue.status, "reason": queue.reason,
            "index": queue.index, "count": queue.segments.count, "eventVersion": queue.eventVersion, "rate": queue.rate,
            "voiceId": queue.voiceId, "stopAtChapterEnd": queue.stopAtChapterEnd, "queueLoaded": !queue.segments.isEmpty,
            "resolvedVoiceId": resolvedVoiceId, "voiceFallback": voiceFallback,
            "sleepRemainingMs": max(0, ((sleepDeadline ?? ProcessInfo.processInfo.systemUptime) - ProcessInfo.processInfo.systemUptime) * 1000)]
        if let current = queue.current { value["currentId"] = current.id; value["chapterId"] = current.chapterId; value["chapterTitle"] = current.chapterTitle; value["currentAnchor"] = current.anchor }
        if let completed = queue.lastCompleted { value["lastCompletedId"] = completed.id; value["lastCompletedAnchor"] = completed.anchor }
        return value
    }
    private func persist() throws {
        var marker = snapshot(); marker["queueLoaded"] = false
        guard let owner = accountOwner, let origin = accountOrigin else { throw ReaderSpeechFailure.code("SPEECH_CONTEXT_CHANGED") }
        marker["speechOwner"] = owner; marker["speechOrigin"] = origin
        do { try store.save(marker) } catch { throw ReaderSpeechFailure.code("SPEECH_POSITION_SAVE_FAILED") }
    }
    private func publish() {
        if !queue.segments.isEmpty {
            MPNowPlayingInfoCenter.default().nowPlayingInfo = [MPMediaItemPropertyTitle: queue.title,
                MPMediaItemPropertyArtist: queue.current?.chapterTitle ?? "听书已结束",
                MPNowPlayingInfoPropertyPlaybackRate: queue.status == "playing" ? queue.rate : 0,
                MPNowPlayingInfoPropertyDefaultPlaybackRate: queue.rate]
        }
        let value = snapshot(); for listener in Array(listeners.values) { listener(value) }
    }
    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didStart utterance: AVSpeechUtterance) {
        DispatchQueue.main.async { [weak self] in
            guard let self = self, let ticket = self.tickets[ObjectIdentifier(utterance)], self.queue.started(ticket) else { return }
            self.startTimeout?.cancel(); self.startTimeout = nil
            self.publish()
        }
    }
    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        DispatchQueue.main.async { [weak self] in
            guard let self = self, let ticket = self.tickets.removeValue(forKey: ObjectIdentifier(utterance)), self.queue.accepts(ticket) else { return }
            self.startTimeout?.cancel(); self.startTimeout = nil
            let completed = self.queue.finished(ticket)
            do { if completed { try self.persist() }; self.publish()
                if self.queue.wantsPlayback { try self.speakNext() } else { self.releaseAudio() }
            } catch { self.fail(Self.code(error)) }
        }
    }
    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        DispatchQueue.main.async { [weak self] in
            guard let self = self, let ticket = self.tickets.removeValue(forKey: ObjectIdentifier(utterance)), self.queue.accepts(ticket) else { return }
            self.pause(reason: "system-stopped")
        }
    }
}
