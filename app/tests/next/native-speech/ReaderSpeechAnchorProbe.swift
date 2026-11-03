import Foundation

// Exercise the actual queue boundary. No AVSpeech, Keychain or Apple SDK runs.
let good: [String: Any] = ["bookId": "book", "branchId": "line", "revisionId": "rev", "chapterId": "chapter", "blockId": "block", "start": 0, "end": 4, "offsetUnit": "utf16", "quoteHash": String(repeating: "a", count: 64)]
func load(_ queue: ReaderSpeechQueue, _ anchor: [String: Any]) throws {
    try queue.load(scope: "owned", title: "Original fixture", values: [.init(id: "paragraph", text: "甲😀乙", chapterId: "chapter", chapterTitle: "Chapter", anchor: anchor)], fromId: nil, rate: 1, voice: "", endChapter: false)
}
var outcomes: [[String: Any]] = []
func reject(_ name: String, _ anchor: [String: Any]) {
    let queue = ReaderSpeechQueue()
    do {
        try load(queue, good)
        try queue.play()
        let ticket = try queue.beginUtterance()!.ticket
        let beforeVersion = queue.eventVersion
        var refused = false
        do { try load(queue, anchor) } catch let error as ReaderSpeechFailure { refused = error.code == "INVALID_SPEECH_SEGMENT" } catch {}
        outcomes.append(["name": name, "passed": refused && queue.eventVersion == beforeVersion && queue.accepts(ticket) && queue.current?.anchor["blockId"] as? String == "block"])
    } catch { outcomes.append(["name": name, "passed": false]) }
}
var privateText = good; privateText["text"] = "Private paragraph must not enter a position marker"
reject("paragraph-text-in-anchor", privateText)
var privateState = good; privateState["privateState"] = ["identity": "GM secret"]
reject("private-world-state-in-anchor", privateState)
var nested = good; nested["blockId"] = ["text": "Hidden payload"]
reject("nested-known-id", nested)
var wrongUnit = good; wrongUnit["offsetUnit"] = "bytes"
reject("non-utf16-unit", wrongUnit)
var missing = good; missing.removeValue(forKey: "start")
reject("missing-start", missing)
var boolean = good; boolean["start"] = true
reject("boolean-start", boolean)
var fractional = good; fractional["end"] = 4.5
reject("fractional-end", fractional)
var unsafe = good; unsafe["end"] = 9_007_199_254_740_992 as Int
reject("unsafe-javascript-integer", unsafe)
var negative = good; negative["start"] = -1
reject("negative-start", negative)
var inverted = good; inverted["start"] = 5
reject("end-before-start", inverted)
var null = good; null["chapterId"] = NSNull()
reject("null-known-id", null)
var invalidDraft = good; invalidDraft["draft"] = "true"
reject("string-draft-marker", invalidDraft)
var paragraph = good; paragraph["paragraph"] = false
reject("boolean-paragraph-index", paragraph)
var oversized = good; oversized["blockId"] = String(repeating: "b", count: 201)
reject("oversized-id", oversized)
let anchors: [(String, [String: Any])] = [
    ("reading-anchor", good),
    ("world-public-anchor", ["worldId": "world", "worldLineId": "line", "checkpointId": "cp", "paragraph": 0, "start": 0, "end": 4, "offsetUnit": "utf16"]),
    ("accepted-draft-anchor", ["bookId": "book", "generationId": "generation", "contentId": "content", "candidateVersion": 1, "start": 0, "end": 4, "offsetUnit": "utf16", "draft": true])
]
for (name, anchor) in anchors {
    do { let queue = ReaderSpeechQueue(); try load(queue, anchor); outcomes.append(["name": name, "passed": queue.current != nil]) }
    catch { outcomes.append(["name": name, "passed": false]) }
}
let failures = outcomes.filter { $0["passed"] as? Bool != true }.count
let report: [String: Any] = ["cases": outcomes.count, "failures": failures, "results": outcomes, "type": "actual-Swift-Foundation-queue-anchor-boundary", "audioPlaybackCertified": false, "keychainExecuted": false, "appleFrameworksTypechecked": false]
let data = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
print(String(data: data, encoding: .utf8)!)
if failures > 0 { exit(1) }
