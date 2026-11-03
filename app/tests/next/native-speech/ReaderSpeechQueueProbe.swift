import Foundation

var cases = 0
func check(_ result: Bool, _ message: String) { if !result { fatalError(message) } }
func queue(_ endChapter: Bool = false) throws -> ReaderSpeechQueue {
    let value = ReaderSpeechQueue()
    try value.load(scope: "owned-scope", title: "原创样本", values: [
        .init(id: "p1", text: "甲😀乙。", chapterId: "c1", chapterTitle: "第一章", anchor: ["blockId": "b1", "start": 0, "end": 5, "offsetUnit": "utf16"]),
        .init(id: "p2", text: "夜色深了。", chapterId: "c1", chapterTitle: "第一章", anchor: ["blockId": "b2", "start": 0, "end": 5, "offsetUnit": "utf16"]),
        .init(id: "p3", text: "船靠岸了。", chapterId: "c2", chapterTitle: "第二章", anchor: ["blockId": "b3", "start": 0, "end": 5, "offsetUnit": "utf16"])
    ], fromId: nil, rate: 1, voice: "", endChapter: endChapter)
    return value
}

var q = try queue(); try q.play()
var first = try q.beginUtterance(limit: 2)!
check(first.text == "甲", "chunk may not cut an emoji surrogate pair")
q.started(first.ticket); check(!q.finished(first.ticket), "chunk does not mean completed paragraph")
check(q.lastCompleted == nil && q.index == 0, "chunk cannot advance completed marker")
let emoji = try q.beginUtterance(limit: 2)!
check(emoji.text == "😀" && emoji.startUtf16 == 1, "native anchors use UTF16")
q.started(emoji.ticket); q.finished(emoji.ticket)
let end = try q.beginUtterance(limit: 2)!; q.started(end.ticket)
check(q.finished(end.ticket) && q.index == 1 && q.lastCompleted?.id == "p1", "only full completion advances marker"); cases += 1

q = try queue(); try q.play(); first = try q.beginUtterance()!; q.pause("headphones-disconnected")
check(!q.finished(first.ticket) && q.index == 0 && q.lastCompleted == nil, "late interruption callback cannot unlock prose")
check(try q.beginUtterance() == nil, "focus gain cannot restart playback"); cases += 1

try q.play(); first = try q.beginUtterance()!; q.pause("interrupted"); try q.play()
let replay = try q.beginUtterance()!
check(replay.text == "甲😀乙。" && replay.startUtf16 == 0 && replay.ticket != first.ticket, "explicit resume replays at most current paragraph")
check(!q.started(first.ticket), "stale start event is ignored"); cases += 1

q = try queue(true); try q.play(); first = try q.beginUtterance()!; q.finished(first.ticket)
first = try q.beginUtterance()!; q.finished(first.ticket)
check(q.status == "paused" && q.reason == "chapter-ended" && q.index == 2 && q.lastCompleted?.id == "p2", "chapter end stops before speaking the next chapter"); cases += 1

q = try queue(); try q.play(); first = try q.beginUtterance()!; try q.move(1)
check(!q.finished(first.ticket) && q.lastCompleted == nil && q.current?.id == "p2", "manual jump cannot prove heard text"); cases += 1

q = try queue(); var rejected = false
do { try q.load(scope: "other", title: "非法样本", values: [.init(id: "bad", text: "文字", chapterId: "c", chapterTitle: "", anchor: [:])], fromId: nil, rate: Double.nan, voice: "", endChapter: false) }
catch { rejected = true }
check(rejected && q.scopeKey == "owned-scope" && q.segments.count == 3, "invalid replacement cannot mutate owner"); cases += 1

try q.play(); first = try q.beginUtterance()!; q.clear()
check(q.scopeKey.isEmpty && q.segments.isEmpty && !q.finished(first.ticket), "logout invalidates old callback"); cases += 1

for value in [0, 5, 15, 30, 60] { try ReaderSpeechQueue.checkSleep(value) }
rejected = false; do { try ReaderSpeechQueue.checkSleep(-1) } catch { rejected = true }
check(rejected, "invalid timer is rejected before mutation"); cases += 1

try ReaderSpeechQueue.checkRate(0.75); try ReaderSpeechQueue.checkRate(2)
for value in [0.749, 2.001, Double.infinity, Double.nan] {
    rejected = false; do { try ReaderSpeechQueue.checkRate(value) } catch { rejected = true }
    check(rejected, "speed must stay within the finite supported interval")
}
cases += 1
print("{\"cases\":\(cases),\"failures\":0,\"type\":\"actual-swift-foundation-native-queue\",\"audioPlaybackCertified\":false}")
