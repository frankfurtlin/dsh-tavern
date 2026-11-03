import Foundation
import CoreFoundation

/// Native commands remain bound to one authenticated document and account.
/// This is an accident/race guard; server authentication remains authoritative.
struct ReaderSpeechContext: Equatable {
    let owner: String
    let origin: String
    let epoch: Int
    let documentNonce: String

    init(owner: String, origin: String, epoch: Int, documentNonce: String, debug: Bool) throws {
        guard ReaderSpeechContextGate.validNonce(owner), ReaderSpeechContextGate.validNonce(documentNonce),
              epoch >= 0, epoch <= 9_007_199_254_740_991,
              origin.utf8.count <= 2048, var parts = URLComponents(string: origin),
              let host = parts.host, !host.isEmpty, parts.user == nil, parts.password == nil,
              parts.query == nil, parts.fragment == nil, parts.path.isEmpty || parts.path == "/",
              parts.scheme == "https" || (debug && parts.scheme == "http" && ["localhost", "127.0.0.1", "10.0.2.2", "::1", "[::1]"].contains(host.lowercased())),
              parts.port == nil || (1...65535).contains(parts.port!) else {
            throw ReaderSpeechFailure.code("INVALID_SPEECH_CONTEXT")
        }
        parts.host = host.lowercased(); parts.path = ""
        if parts.scheme == "https" && parts.port == 443 { parts.port = nil }
        guard let normalized = parts.url?.absoluteString else { throw ReaderSpeechFailure.code("INVALID_SPEECH_CONTEXT") }
        self.owner = owner; self.origin = normalized; self.epoch = epoch; self.documentNonce = documentNonce
    }
}

struct ReaderSpeechContextGate {
    private(set) var epoch = -1
    private(set) var value: ReaderSpeechContext?

    static func validNonce(_ value: String) -> Bool {
        value.utf8.count == 32 && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }
    static func sameDocument(claimed: String?, current: Any?) -> Bool {
        guard let claimed = claimed, let current = current as? String,
              validNonce(claimed), validNonce(current) else { return false }
        return claimed == current
    }
    static func bind(_ payload: [String: Any], nonce: String, epoch: Int? = nil) -> [String: Any] {
        var result = payload
        result["documentProtocol"] = "reader-document-v1"; result["documentNonce"] = nonce
        if let epoch = epoch { result["contextEpoch"] = epoch }
        return result
    }
    static func markerMatches(_ marker: [String: Any]?, owner: String, origin: String) -> Bool {
        guard let marker = marker, validMarker(marker) else { return false }
        return marker["speechOwner"] as? String == owner && marker["speechOrigin"] as? String == origin
    }
    private static let markerFields: Set<String> = ["speechOwner", "speechOrigin", "scopeKey", "title", "status", "reason", "index", "count", "eventVersion", "rate", "voiceId", "stopAtChapterEnd", "queueLoaded", "resolvedVoiceId", "voiceFallback", "sleepRemainingMs", "currentId", "chapterId", "chapterTitle", "currentAnchor", "lastCompletedId", "lastCompletedAnchor"]
    static func validMarker(_ marker: [String: Any]) -> Bool {
        func number(_ value: Any?) -> Double? {
            guard let value = value as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID(), value.doubleValue.isFinite else { return nil }
            return value.doubleValue
        }
        func flag(_ value: Any?) -> Bool? {
            guard let value = value as? NSNumber, CFGetTypeID(value) == CFBooleanGetTypeID() else { return nil }
            return value.boolValue
        }
        guard Set(marker.keys).isSubset(of: markerFields),
              let owner = marker["speechOwner"] as? String, validNonce(owner),
              let origin = marker["speechOrigin"] as? String, !origin.isEmpty, origin.utf8.count <= 2048,
              let scope = marker["scopeKey"] as? String, !scope.isEmpty, scope.utf16.count <= 512,
              let status = marker["status"] as? String, ["idle", "ready", "starting", "playing", "paused", "ended", "error"].contains(status),
              flag(marker["queueLoaded"]) == false,
              JSONSerialization.isValidJSONObject(marker),
              let bytes = try? JSONSerialization.data(withJSONObject: marker), bytes.count <= 50000 else { return false }
        let lengths = ["title": 500, "reason": 200, "voiceId": 500, "resolvedVoiceId": 500, "currentId": 200, "chapterId": 200, "chapterTitle": 500, "lastCompletedId": 200]
        for (key, limit) in lengths where marker[key] != nil {
            guard let text = marker[key] as? String, text.utf16.count <= limit,
                  !["currentId", "chapterId", "lastCompletedId"].contains(key) || !text.isEmpty else { return false }
        }
        for key in ["index", "count", "eventVersion"] where marker[key] != nil {
            guard let value = number(marker[key]), value >= 0, value <= 9_007_199_254_740_991, value.rounded(.towardZero) == value else { return false }
            if key != "eventVersion" && value > 20000 { return false }
        }
        if let index = number(marker["index"]), let count = number(marker["count"]), index > count { return false }
        if marker["rate"] != nil { guard let rate = number(marker["rate"]), (try? ReaderSpeechQueue.checkRate(rate)) != nil else { return false } }
        if marker["sleepRemainingMs"] != nil { guard let time = number(marker["sleepRemainingMs"]), time >= 0, time <= 3_600_000 else { return false } }
        for key in ["stopAtChapterEnd", "voiceFallback"] where marker[key] != nil { if flag(marker[key]) == nil { return false } }
        for (key, identifier) in [("currentAnchor", "currentId"), ("lastCompletedAnchor", "lastCompletedId")] {
            if marker[key] != nil || marker[identifier] != nil {
                guard let anchor = marker[key] as? [String: Any], marker[identifier] is String,
                      (try? ReaderSpeechQueue.checkAnchor(anchor)) != nil else { return false }
            }
        }
        return true
    }
    static func restoredMarker(_ marker: [String: Any], scope: String, owner: String, origin: String) -> [String: Any]? {
        guard markerMatches(marker, owner: owner, origin: origin), marker["scopeKey"] as? String == scope else { return nil }
        // An explicit projection also protects against future private fields.
        var result = marker.filter { markerFields.contains($0.key) && $0.key != "speechOwner" && $0.key != "speechOrigin" }
        if result["status"] as? String != "ended" { result["status"] = "paused" }
        result["reason"] = "process-restored"; result["queueLoaded"] = false
        return result
    }
    mutating func replace(_ next: ReaderSpeechContext?, epoch: Int) throws {
        guard epoch >= 0, epoch <= 9_007_199_254_740_991, epoch >= self.epoch,
              next == nil || next!.epoch == epoch,
              epoch != self.epoch || value == next else { throw ReaderSpeechFailure.code("SPEECH_CONTEXT_CHANGED") }
        self.epoch = epoch; value = next
    }
    mutating func resetDocument() { epoch = -1; value = nil }
    func accepts(_ captured: ReaderSpeechContext, epoch: Int?, nonce: String?) -> Bool {
        value == captured && epoch == captured.epoch && nonce == captured.documentNonce
    }
}
