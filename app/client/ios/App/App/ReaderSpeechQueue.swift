import Foundation
import CoreFoundation

enum ReaderSpeechFailure: Error {
    case code(String)
    var code: String { switch self { case .code(let value): return value } }
}

/// A paragraph is heard only after every engine utterance actually finishes.
final class ReaderSpeechQueue {
    struct Segment {
        let id: String
        let text: String
        let chapterId: String
        let chapterTitle: String
        let anchor: [String: Any]
    }
    struct Utterance { let ticket: String; let text: String; let startUtf16: Int }
    private(set) var segments: [Segment] = []
    private(set) var scopeKey = ""
    private(set) var title = ""
    private(set) var status = "idle"
    private(set) var reason = ""
    private(set) var index = 0
    private(set) var lastCompletedIndex: Int?
    private(set) var eventVersion = 0
    var rate = 1.0
    var voiceId = ""
    var stopAtChapterEnd = false
    private var generation = 0
    private var ordinal = 0
    private var offset = 0
    private var activeEnd = 0
    private var activeTicket: String?
    var current: Segment? { segments.indices.contains(index) ? segments[index] : nil }
    var lastCompleted: Segment? {
        guard let position = lastCompletedIndex, segments.indices.contains(position) else { return nil }
        return segments[position]
    }
    var wantsPlayback: Bool { status == "starting" || status == "playing" }

    static func checkRate(_ value: Double) throws {
        guard value.isFinite, value >= 0.75, value <= 2 else { throw ReaderSpeechFailure.code("INVALID_SPEECH_RATE") }
    }
    static func checkSleep(_ minutes: Int) throws {
        guard [0, 5, 15, 30, 60].contains(minutes) else { throw ReaderSpeechFailure.code("INVALID_SLEEP_TIMER") }
    }
    /// Position markers may contain coordinates, never paragraph text or GM state.
    static func checkAnchor(_ value: [String: Any]) throws -> Int {
        let identifiers: Set<String> = ["bookId", "branchId", "revisionId", "chapterId", "blockId", "worldId", "worldLineId", "checkpointId", "generationId", "contentId"]
        let numbers: Set<String> = ["start", "end", "paragraph", "candidateVersion"]
        let fields = identifiers.union(numbers).union(["offsetUnit", "quoteHash", "draft"])
        func integer(_ value: Any?) -> Double? {
            guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
            let result = number.doubleValue
            return result.isFinite && result >= 0 && result <= 9_007_199_254_740_991 && result.rounded(.towardZero) == result ? result : nil
        }
        guard Set(value.keys).isSubset(of: fields), value["offsetUnit"] as? String == "utf16",
              let start = integer(value["start"]), let end = integer(value["end"]), start <= end,
              JSONSerialization.isValidJSONObject(value) else { throw ReaderSpeechFailure.code("INVALID_SPEECH_SEGMENT") }
        for (key, item) in value {
            if identifiers.contains(key) {
                guard let identifier = item as? String, !identifier.isEmpty, identifier.utf16.count <= 200 else { throw ReaderSpeechFailure.code("INVALID_SPEECH_SEGMENT") }
            } else if numbers.contains(key) {
                guard integer(item) != nil else { throw ReaderSpeechFailure.code("INVALID_SPEECH_SEGMENT") }
            } else if key == "draft" {
                guard let flag = item as? NSNumber, CFGetTypeID(flag) == CFBooleanGetTypeID(), flag.boolValue else { throw ReaderSpeechFailure.code("INVALID_SPEECH_SEGMENT") }
            } else if key == "quoteHash" {
                guard let hash = item as? String, hash.utf8.count == 64, hash.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) else { throw ReaderSpeechFailure.code("INVALID_SPEECH_SEGMENT") }
            }
        }
        let bytes = try JSONSerialization.data(withJSONObject: value).count
        guard bytes <= 8192 else { throw ReaderSpeechFailure.code("INVALID_SPEECH_SEGMENT") }
        return bytes
    }
    func load(scope: String, title: String, values: [Segment], fromId: String?, rate: Double, voice: String, endChapter: Bool) throws {
        guard !scope.isEmpty, scope.utf16.count <= 512, title.utf16.count <= 500 else { throw ReaderSpeechFailure.code("INVALID_SPEECH_CONTEXT") }
        try Self.checkRate(rate)
        guard voice.utf16.count <= 500 else { throw ReaderSpeechFailure.code("INVALID_SPEECH_VOICE") }
        guard !values.isEmpty, values.count <= 20000 else { throw ReaderSpeechFailure.code("INVALID_SPEECH_QUEUE") }
        var ids = Set<String>(), total = 0, anchorTotal = 0
        var selected: Int? = fromId == nil || fromId == "" ? 0 : nil
        for (position, segment) in values.enumerated() {
            guard !segment.id.isEmpty, segment.id.utf16.count <= 200, ids.insert(segment.id).inserted,
                  !segment.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, segment.text.utf16.count <= 200000,
                  !segment.chapterId.isEmpty, segment.chapterId.utf16.count <= 200, segment.chapterTitle.utf16.count <= 500
            else { throw ReaderSpeechFailure.code("INVALID_SPEECH_SEGMENT") }
            total += segment.text.utf16.count
            anchorTotal += try Self.checkAnchor(segment.anchor)
            guard total <= 2000000, anchorTotal <= 8000000 else { throw ReaderSpeechFailure.code("SPEECH_QUEUE_TOO_LARGE") }
            if segment.id == fromId { selected = position }
        }
        guard let selected = selected else { throw ReaderSpeechFailure.code("SPEECH_POSITION_NOT_FOUND") }
        invalidate(); segments = values; scopeKey = scope; self.title = title
        index = selected; lastCompletedIndex = nil; self.rate = rate; voiceId = voice; stopAtChapterEnd = endChapter
        status = "ready"; reason = ""; eventVersion += 1
    }
    func play() throws {
        guard !segments.isEmpty else { throw ReaderSpeechFailure.code("SPEECH_QUEUE_EMPTY") }
        guard current != nil else { throw ReaderSpeechFailure.code("SPEECH_ENDED") }
        invalidate(); status = "starting"; reason = ""; eventVersion += 1
    }
    func beginUtterance(limit: Int = 4000) throws -> Utterance? {
        guard wantsPlayback, activeTicket == nil, let current = current else { return nil }
        guard limit >= 2 else { throw ReaderSpeechFailure.code("INVALID_SPEECH_CHUNK_SIZE") }
        let text = current.text as NSString
        var end = min(text.length, offset + limit)
        if end < text.length, (0xD800...0xDBFF).contains(text.character(at: end - 1)), (0xDC00...0xDFFF).contains(text.character(at: end)) { end -= 1 }
        activeEnd = end; ordinal += 1
        let ticket = "\(generation):\(ordinal):\(index):\(offset)"; activeTicket = ticket
        return Utterance(ticket: ticket, text: text.substring(with: NSRange(location: offset, length: end - offset)), startUtf16: offset)
    }
    func accepts(_ ticket: String) -> Bool { wantsPlayback && activeTicket == ticket }
    @discardableResult func started(_ ticket: String) -> Bool {
        guard accepts(ticket) else { return false }
        status = "playing"; eventVersion += 1; return true
    }
    @discardableResult func finished(_ ticket: String) -> Bool {
        guard accepts(ticket), let previous = current else { return false }
        offset = activeEnd; activeTicket = nil
        guard offset >= previous.text.utf16.count else { return false }
        lastCompletedIndex = index; index += 1; offset = 0; eventVersion += 1
        if current == nil { status = "ended"; reason = "completed" }
        else if stopAtChapterEnd, current?.chapterId != previous.chapterId { status = "paused"; reason = "chapter-ended" }
        return true
    }
    func pause(_ reason: String) {
        invalidate(); status = segments.isEmpty ? "idle" : "paused"; self.reason = reason; eventVersion += 1
    }
    func fail(_ code: String) { pause(code); status = "error" }
    func move(_ direction: Int) throws {
        guard direction == -1 || direction == 1 else { throw ReaderSpeechFailure.code("INVALID_SPEECH_DIRECTION") }
        guard !segments.isEmpty else { throw ReaderSpeechFailure.code("SPEECH_QUEUE_EMPTY") }
        invalidate(); index = max(0, min(segments.count - 1, index + direction)); status = "paused"; reason = "selected"; eventVersion += 1
    }
    func clear() {
        invalidate(); segments.removeAll(); scopeKey = ""; title = ""; index = 0; lastCompletedIndex = nil
        status = "idle"; reason = "stopped"; eventVersion += 1
    }
    private func invalidate() { generation += 1; activeTicket = nil; offset = 0; activeEnd = 0 }
}
