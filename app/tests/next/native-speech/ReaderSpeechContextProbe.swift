import Foundation

let owner = String(repeating: "a", count: 32), other = String(repeating: "b", count: 32)
let nonce = String(repeating: "c", count: 32), nextNonce = String(repeating: "d", count: 32)
var cases = 0
func check(_ condition: Bool, _ message: String) { if !condition { fatalError(message) } }
func rejects(_ operation: () throws -> Void) -> Bool { do { try operation(); return false } catch { return true } }
func context(_ epoch: Int = 1, _ who: String = owner, _ doc: String = nonce) throws -> ReaderSpeechContext {
    try ReaderSpeechContext(owner: who, origin: "https://API.example.test:443/", epoch: epoch, documentNonce: doc, debug: false)
}

let first = try context()
check(first.origin == "https://api.example.test", "equivalent HTTPS origins must share the same private marker scope"); cases += 1
for bad in ["https://u:p@api.example.test", "https://api.example.test/path", "https://api.example.test?key=value", "https://api.example.test#fragment", "http://api.example.test", "http://127.0.0.1", "https://api.example.test:0"] {
    check(rejects { _ = try ReaderSpeechContext(owner: owner, origin: bad, epoch: 1, documentNonce: nonce, debug: false) }, "production origin must reject credentials, paths, plaintext and invalid ports")
}
cases += 1
_ = try ReaderSpeechContext(owner: owner, origin: "http://127.0.0.1:18890", epoch: 1, documentNonce: nonce, debug: true)
check(rejects { _ = try ReaderSpeechContext(owner: owner, origin: "http://private.example.test", epoch: 1, documentNonce: nonce, debug: true) }, "debug does not permit arbitrary plaintext servers"); cases += 1
for bad in ["", "a", String(repeating: "A", count: 32), String(repeating: "é", count: 32), nonce + "\n"] {
    check(rejects { _ = try context(1, bad) }, "owner must be a full stable account ID")
    check(rejects { _ = try context(1, owner, bad) }, "document nonce must be exact lower-case 32 hex")
}
cases += 1
for bad in [-1, 9_007_199_254_740_992] { check(rejects { _ = try context(bad) }, "bridge epoch must be a non-negative JavaScript safe integer") }
cases += 1

var gate = ReaderSpeechContextGate(); try gate.replace(first, epoch: 1)
try gate.replace(first, epoch: 1)
check(gate.accepts(first, epoch: 1, nonce: nonce), "idempotent handshake retains current authorization"); cases += 1
check(!gate.accepts(first, epoch: nil, nonce: nonce) && !gate.accepts(first, epoch: 1, nonce: nil), "unsigned legacy calls cannot access a private queue"); cases += 1
check(!gate.accepts(first, epoch: 0, nonce: nonce) && !gate.accepts(first, epoch: 1, nonce: nextNonce), "stale epoch or a new document cannot finish an old command"); cases += 1
check(rejects { try gate.replace(try context(1, other), epoch: 1) }, "same epoch cannot replace account identity")
check(gate.value == first, "failed handshake must preserve the current account"); cases += 1
try gate.replace(nil, epoch: 2)
check(!gate.accepts(first, epoch: 1, nonce: nonce), "revocation invalidates queued old-account commands")
check(rejects { try gate.replace(first, epoch: 1) }, "late setContext cannot resurrect a revoked account")
check(rejects { try gate.replace(try context(2), epoch: 2) }, "a revoked epoch cannot be reactivated"); cases += 1
let replacement = try context(3, other); try gate.replace(replacement, epoch: 3)
check(gate.accepts(replacement, epoch: 3, nonce: nonce) && !gate.accepts(first, epoch: 1, nonce: nonce), "a new owner must never accept old-owner completion tickets"); cases += 1
gate.resetDocument()
check(gate.value == nil && !gate.accepts(replacement, epoch: 3, nonce: nonce), "document navigation invalidates account capability")
let newDocument = try context(0, owner, nextNonce); try gate.replace(newDocument, epoch: 0)
check(!gate.accepts(newDocument, epoch: 0, nonce: nonce), "epoch reuse after navigation still requires the new nonce"); cases += 1
check(!ReaderSpeechContextGate.sameDocument(claimed: nonce, current: nextNonce) && !ReaderSpeechContextGate.sameDocument(claimed: nonce, current: 7), "old JS messages and invalid WK results must be dropped")
check(ReaderSpeechContextGate.sameDocument(claimed: nonce, current: nonce), "current document can finish its own handshake"); cases += 1
let marker: [String: Any] = ["speechOwner": owner, "speechOrigin": first.origin, "scopeKey": "owned-revision", "status": "paused", "queueLoaded": false, "lastCompletedId": "p3", "lastCompletedAnchor": ["blockId": "b3", "start": 0, "end": 5, "offsetUnit": "utf16"]]
check(ReaderSpeechContextGate.markerMatches(marker, owner: owner, origin: first.origin), "same-owner cold start preserves its completed paragraph")
check(!ReaderSpeechContextGate.markerMatches(marker, owner: other, origin: first.origin) && !ReaderSpeechContextGate.markerMatches(marker, owner: owner, origin: "https://elsewhere.test"), "another owner or server cannot inherit a private marker")
check(!ReaderSpeechContextGate.markerMatches(["scopeKey": "legacy"], owner: owner, origin: first.origin), "legacy unbound marker requires safe removal"); cases += 1
let reply = ReaderSpeechContextGate.bind(["documentNonce": "forged", "contextEpoch": -1, "ready": true], nonce: nonce, epoch: 3)
check(reply["documentNonce"] as? String == nonce && reply["contextEpoch"] as? Int == 3 && reply["documentProtocol"] as? String == "reader-document-v1", "native success/error/event envelopes use the captured capability, not payload fields"); cases += 1
print("{\"cases\":\(cases),\"failed\":0,\"testType\":\"actual-Swift-Foundation-context-policy\",\"nativePickerExecuted\":false,\"audioPlaybackCertified\":false}")
