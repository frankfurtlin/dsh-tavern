import Foundation
import UIKit
import Capacitor

@objc(ReaderSpeechPlugin)
public class ReaderSpeechPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "ReaderSpeechPlugin"
    public let jsName = "ReaderSpeech"
    public let pluginMethods: [CAPPluginMethod] = ["capabilities", "setContext", "revokeContext", "voices", "status", "loadQueue", "play", "pause", "skip", "stop", "configure", "openSettings"].map {
        CAPPluginMethod(name: $0, returnType: CAPPluginReturnPromise)
    }
    private var listener: UUID?
    private var navigationObservation: NSKeyValueObservation?
    private let contextLock = NSLock()
    private var documentGeneration = 0
    private var contextGate = ReaderSpeechContextGate()
    private var transitioning = false
    public override func load() {
        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            self.listener = ReaderSpeechEngine.shared.addListener { [weak self] state in self?.publish(state) }
            self.navigationObservation = self.webView?.observe(\.isLoading, options: [.new]) { [weak self] _, change in
                guard change.newValue == true else { return }
                if Thread.isMainThread { self?.resetDocument() }
                else { DispatchQueue.main.async { self?.resetDocument() } }
            }
        }
    }
    deinit {
        navigationObservation?.invalidate()
        if let listener = listener { DispatchQueue.main.async { ReaderSpeechEngine.shared.removeListener(listener) } }
    }
    private func stamp() -> Int { contextLock.lock(); defer { contextLock.unlock() }; return documentGeneration }
    private func gate() -> ReaderSpeechContextGate { contextLock.lock(); defer { contextLock.unlock() }; return contextGate }
    private func install(_ next: ReaderSpeechContextGate) { contextLock.lock(); defer { contextLock.unlock() }; contextGate = next }
    private func clearDocumentListeners() {
        for group in eventListeners?.allValues ?? [] {
            for call in group as? [CAPPluginCall] ?? [] { call.keepAlive = false; bridge?.releaseCall(call) }
        }
        eventListeners?.removeAllObjects()
    }
    private func resetDocument() {
        contextLock.lock(); documentGeneration += 1; contextGate.resetDocument(); contextLock.unlock()
        clearDocumentListeners()
        // Navigation is different from backgrounding the app. Reauthenticate
        // the document before it can see or control the app-owned synthesizer.
        ReaderSpeechEngine.shared.pause(reason: "document-changed")
    }
    private func currentDocument(_ call: CAPPluginCall, _ action: @escaping () throws -> Void) {
        let generation = stamp(), claimed = call.getString("documentNonce")
        DispatchQueue.main.async { [weak self] in
            guard let self = self, generation == self.stamp(), let webView = self.webView else { return }
            webView.evaluateJavaScript("window.__readerSpeechDocumentNonce") { [weak self] current, error in
                guard let self = self, error == nil, generation == self.stamp(),
                      ReaderSpeechContextGate.sameDocument(claimed: claimed, current: current) else { return }
                do { try action() } catch { self.reject(call, ReaderSpeechEngine.code(error)) }
            }
        }
    }
    private func resolve(_ call: CAPPluginCall, _ value: [String: Any]) {
        guard let nonce = call.getString("documentNonce"), ReaderSpeechContextGate.validNonce(nonce) else { return }
        call.resolve(ReaderSpeechContextGate.bind(value, nonce: nonce, epoch: call.getInt("epoch")))
    }
    private func reject(_ call: CAPPluginCall, _ code: String) {
        guard let nonce = call.getString("documentNonce"), ReaderSpeechContextGate.validNonce(nonce) else { return }
        call.reject(Self.message(code), code, nil, ReaderSpeechContextGate.bind([:], nonce: nonce, epoch: call.getInt("epoch")))
    }
    private func publish(_ state: [String: Any]) {
        guard !transitioning, let captured = gate().value, let webView = webView else { return }
        let generation = stamp()
        webView.evaluateJavaScript("window.__readerSpeechDocumentNonce") { [weak self] current, error in
            guard let self = self, error == nil, !self.transitioning, generation == self.stamp(),
                  self.gate().value == captured, ReaderSpeechContextGate.sameDocument(claimed: captured.documentNonce, current: current) else { return }
            self.notifyListeners("speechState", data: ReaderSpeechContextGate.bind(state, nonce: captured.documentNonce, epoch: captured.epoch))
        }
    }
    private func execute(_ call: CAPPluginCall, operation: @escaping (ReaderSpeechEngine) throws -> [String: Any]?) {
        let captured = gate().value
        currentDocument(call) { [self] in
            guard let captured = captured, !transitioning,
                  gate().accepts(captured, epoch: call.getInt("epoch"), nonce: call.getString("documentNonce")) else {
                throw ReaderSpeechFailure.code("SPEECH_CONTEXT_CHANGED")
            }
            if let result = try operation(ReaderSpeechEngine.shared) { resolve(call, result) }
        }
    }
    @objc func capabilities(_ call: CAPPluginCall) {
        currentDocument(call) { [self] in resolve(call, ["ready": true, "documentProtocol": "reader-document-v1", "contextRequired": true]) }
    }
    @objc func setContext(_ call: CAPPluginCall) {
        currentDocument(call) { [self] in
            guard let owner = call.getString("owner"), let origin = call.getString("origin"),
                  let epoch = call.getInt("epoch"), let nonce = call.getString("documentNonce") else { throw ReaderSpeechFailure.code("INVALID_SPEECH_CONTEXT") }
            #if DEBUG
            let debug = true
            #else
            let debug = false
            #endif
            let next = try ReaderSpeechContext(owner: owner, origin: origin, epoch: epoch, documentNonce: nonce, debug: debug)
            var updated = gate(); try updated.replace(next, epoch: epoch)
            if updated.value == gate().value { resolve(call, ["ready": true]); return }
            transitioning = true; defer { transitioning = false }
            clearDocumentListeners()
            do { try ReaderSpeechEngine.shared.bindAccount(owner: next.owner, origin: next.origin) }
            catch {
                var revoked = ReaderSpeechContextGate(); try revoked.replace(nil, epoch: epoch); install(revoked)
                throw error
            }
            install(updated); resolve(call, ["ready": true])
        }
    }
    @objc func revokeContext(_ call: CAPPluginCall) {
        currentDocument(call) { [self] in
            guard let epoch = call.getInt("epoch") else { throw ReaderSpeechFailure.code("INVALID_SPEECH_CONTEXT") }
            var updated = gate(); try updated.replace(nil, epoch: epoch)
            install(updated); clearDocumentListeners()
            try ReaderSpeechEngine.shared.revokeAccount()
            resolve(call, ["ready": false])
        }
    }
    private static func scope(_ call: CAPPluginCall) throws -> String {
        guard let value = call.getString("scopeKey"), !value.isEmpty, value.utf16.count <= 512 else { throw ReaderSpeechFailure.code("INVALID_SPEECH_CONTEXT") }
        return value
    }
    private static func message(_ code: String) -> String {
        if code == "SPEECH_CONTEXT_CHANGED" || code == "INVALID_SPEECH_CONTEXT" { return "账号或服务器已变化，请重新打开正文后播放" }
        if code.contains("VOICE") { return "设备暂时没有可用中文语音，请在系统辅助功能的朗读内容中安装后重试" }
        if code.contains("POSITION") { return "听书位置暂时无法保存，已暂停播放；请检查本机存储后重试" }
        if code == "AUDIO_FOCUS_UNAVAILABLE" { return "其他应用正在使用音频，请稍后点击继续播放" }
        if code == "SLEEP_TIMER_EXPIRED" { return "听书定时已结束，请重新设置后继续" }
        return "系统听书暂时无法继续，请重新打开正文后重试"
    }
    @objc func voices(_ call: CAPPluginCall) { execute(call) { if call.getBool("retry") == true { $0.retryEngine() }; return $0.voices() } }
    @objc func status(_ call: CAPPluginCall) { execute(call) { try $0.state(scope: Self.scope(call)) } }
    @objc func loadQueue(_ call: CAPPluginCall) {
        execute(call) { engine in
            guard let input = call.getArray("segments", [String: Any].self), input.count <= 20000 else { throw ReaderSpeechFailure.code("INVALID_SPEECH_QUEUE") }
            let segments = try input.map { item -> ReaderSpeechQueue.Segment in
                guard let id = item["id"] as? String, let text = item["text"] as? String,
                      let chapterId = item["chapterId"] as? String, let chapterTitle = item["chapterTitle"] as? String,
                      let anchor = item["anchor"] as? [String: Any] else { throw ReaderSpeechFailure.code("INVALID_SPEECH_SEGMENT") }
                return .init(id: id, text: text, chapterId: chapterId, chapterTitle: chapterTitle, anchor: anchor)
            }
            let scope = try Self.scope(call)
            try engine.load(scope: scope, title: call.getString("title") ?? "", segments: segments, fromId: call.getString("fromId"),
                rate: call.getDouble("rate") ?? 1, voice: call.getString("voiceId") ?? "", endChapter: call.getBool("stopAtChapterEnd") ?? false, minutes: call.getInt("sleepMinutes") ?? 0)
            return try engine.state(scope: scope)
        }
    }
    @objc func play(_ call: CAPPluginCall) { execute(call) { let scope = try Self.scope(call); _ = try $0.state(scope: scope); try $0.play(); return try $0.state(scope: scope) } }
    @objc func pause(_ call: CAPPluginCall) { execute(call) { let scope = try Self.scope(call); _ = try $0.state(scope: scope); $0.pause(reason: "user-paused"); return try $0.state(scope: scope) } }
    @objc func skip(_ call: CAPPluginCall) { execute(call) { let scope = try Self.scope(call); _ = try $0.state(scope: scope); try $0.skip(call.getInt("direction") ?? 0); return try $0.state(scope: scope) } }
    @objc func stop(_ call: CAPPluginCall) { execute(call) { try $0.stop(clearPosition: call.getBool("clearPosition") ?? false); return [:] } }
    @objc func configure(_ call: CAPPluginCall) {
        execute(call) { engine in
            let scope = try Self.scope(call); _ = try engine.state(scope: scope)
            try engine.configure(rate: call.getDouble("rate") ?? 1, voice: call.getString("voiceId") ?? "", endChapter: call.getBool("stopAtChapterEnd") ?? false, minutes: call.getInt("sleepMinutes") ?? 0)
            return try engine.state(scope: scope)
        }
    }
    @objc func openSettings(_ call: CAPPluginCall) {
        execute(call) { [self] _ in
            guard let url = URL(string: UIApplication.openSettingsURLString) else { throw ReaderSpeechFailure.code("VOICE_SETTINGS_UNAVAILABLE") }
            let captured = gate().value
            UIApplication.shared.open(url, options: [:]) { opened in
                guard let captured = captured,
                      gate().accepts(captured, epoch: call.getInt("epoch"), nonce: call.getString("documentNonce")) else { return }
                resolve(call, ["opened": opened, "instructions": "在系统设置的辅助功能 → 朗读内容 → 声音中安装中文语音"])
            }
            // Completion replies asynchronously; do not resolve twice.
            return nil
        }
    }
}
