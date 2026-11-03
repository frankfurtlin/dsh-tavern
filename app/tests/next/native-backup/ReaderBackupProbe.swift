import Foundation

var cases = 0
func check(_ result: Bool, _ message: String) { if !result { fatalError(message) } }
func rejected(_ expected: String, _ action: () throws -> Void) {
    do { try action(); fatalError("expected " + expected) }
    catch { check(ReaderBackupPolicy.code(error, fallback: "unexpected") == expected, "wrong error code") }
}
final class MeteredInput: InputStream {
    let base: InputStream
    var largest = 0
    var total: Int64 = 0
    var closeCount = 0
    init(_ base: InputStream) { self.base = base; super.init(data: Data()) }
    override func open() { base.open() }
    override func close() { closeCount += 1; base.close() }
    override func read(_ buffer: UnsafeMutablePointer<UInt8>, maxLength len: Int) -> Int {
        largest = max(largest, len); let count = base.read(buffer, maxLength: len)
        if count > 0 { total += Int64(count) }; return count
    }
}
func drain(_ stream: ReaderMultipartStream, size: Int = 17) throws -> Data {
    var result = Data(); var buffer = [UInt8](repeating: 0, count: size)
    while true {
        let count = try buffer.withUnsafeMutableBufferPointer { try stream.read($0.baseAddress!, maxLength: $0.count) }
        if count == 0 { break }; result.append(contentsOf: buffer.prefix(count))
    }
    return result
}

let source = Data([0, 255, 0, 13, 10, 42, 240, 159, 152, 128])
let meter = MeteredInput(InputStream(data: source))
let multipart = try ReaderMultipartStream(source: meter, size: Int64(source.count), password: "密码😀\r\n二行", boundary: "reader-test-boundary")
let output = try drain(multipart, size: 3)
let prefix = Data("--reader-test-boundary\r\nContent-Disposition: form-data; name=\"password\"\r\n\r\n密码😀\r\n二行\r\n--reader-test-boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"private.srbackup\"\r\nContent-Type: application/octet-stream\r\n\r\n".utf8)
let suffix = Data("\r\n--reader-test-boundary--\r\n".utf8)
check(output == prefix + source + suffix && Int64(output.count) == multipart.length, "multipart binary or unicode framing corrupted")
check(meter.total == Int64(source.count) && meter.largest <= 3 && meter.closeCount >= 1, "encoder must read the file once")
check(try drain(multipart).isEmpty, "EOF cannot silently replay a request")
multipart.close(); rejected("BACKUP_CANCELLED") { _ = try drain(multipart) }; cases += 1

for difference in [-1, 1] {
    let stream = try ReaderMultipartStream(source: InputStream(data: source), size: Int64(source.count + difference), password: "one")
    rejected("BACKUP_SOURCE_CHANGED") { _ = try drain(stream) }
    rejected("BACKUP_SOURCE_CHANGED") { _ = try drain(stream) }; stream.close()
}; cases += 1

let unstarted = try ReaderMultipartStream(source: InputStream(data: source), size: Int64(source.count), password: "one")
unstarted.close(); rejected("BACKUP_CANCELLED") { _ = try drain(unstarted) }; cases += 1
rejected("BACKUP_REQUEST_INVALID") { _ = try ReaderMultipartStream(source: InputStream(data: source), size: -1, password: "x") }
rejected("BACKUP_REQUEST_INVALID") { _ = try ReaderMultipartStream(source: InputStream(data: source), size: Int64(source.count), password: "", boundary: "safe-boundary") }
rejected("BACKUP_REQUEST_INVALID") { _ = try ReaderMultipartStream(source: InputStream(data: source), size: Int64(source.count), password: "x", boundary: "evil\r\nname") }
rejected("BACKUP_REQUEST_INVALID") { _ = try ReaderMultipartStream(source: InputStream(data: source), size: ReaderBackupPolicy.maxFileBytes + 1, password: "x") }; cases += 1

for value in ["http://example.com", "https://user:secret@example.com", "https://example.com/file", "https://example.com?url=other", "https://example.com#hidden", "https://example.com:0", "https://example.com:65536"] {
    rejected("BACKUP_SERVER_INVALID") { _ = try ReaderBackupPolicy.origin(value, debug: false) }
}
for value in ["http://localhost", "http://127.0.0.1:18849", "http://10.0.2.2:18849", "http://[::1]:18849"] {
    _ = try ReaderBackupPolicy.origin(value, debug: true)
    rejected("BACKUP_SERVER_INVALID") { _ = try ReaderBackupPolicy.origin(value, debug: false) }
}
rejected("BACKUP_SERVER_INVALID") { _ = try ReaderBackupPolicy.origin("http://localhost.evil.example", debug: true) }; cases += 1
let origin = try ReaderBackupPolicy.origin("https://example.com:8443", debug: false)
let identifier = String(repeating: "a", count: 32)
check(try ReaderBackupPolicy.endpoint(origin).absoluteString == "https://example.com:8443/api/reader/v1/backups/prepare", "route must remain on configured origin")
check(try ReaderBackupPolicy.endpoint(origin, operationID: identifier).absoluteString == "https://example.com:8443/api/reader/v1/backups/" + identifier + "/download", "download endpoint")
rejected("BACKUP_REQUEST_INVALID") { _ = try ReaderBackupPolicy.endpoint(origin, operationID: "../private") }; cases += 1
try ReaderBackupPolicy.credentials(password: "unicode😀", requestID: "import-key:123", token: "test.jwt.value")
for invalid in ["x\r\nAuthorization: stolen", "x y", ""] {
    rejected("BACKUP_REQUEST_INVALID") { try ReaderBackupPolicy.credentials(password: "p", requestID: "test", token: invalid) }
}
rejected("BACKUP_REQUEST_INVALID") { try ReaderBackupPolicy.credentials(password: String(repeating: "😀", count: 257), requestID: "test", token: "t") }
rejected("BACKUP_REQUEST_INVALID") { try ReaderBackupPolicy.credentials(password: "p", requestID: "test\r\n", token: "t") }; cases += 1
let context = ReaderBackupContext(origin: origin, owner: identifier, epoch: 1)
let claims = try JSONSerialization.data(withJSONObject: ["sub": identifier])
let syntheticToken = "fixture." + claims.base64EncodedString().replacingOccurrences(of: "=", with: "").replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_") + ".unsigned"
try ReaderBackupPolicy.authorizeToken(syntheticToken, owner: identifier)
rejected("BACKUP_CONTEXT_CHANGED") { try ReaderBackupPolicy.authorizeToken(syntheticToken, owner: String(repeating: "b", count: 32)) }
rejected("BACKUP_CONTEXT_CHANGED") { try ReaderBackupPolicy.authorizeToken("fixture.not-json.signature", owner: identifier) }; cases += 1
check(context != ReaderBackupContext(origin: origin, owner: String(repeating: "b", count: 32), epoch: 1), "owner switch invalidates ticket")
check(context != ReaderBackupContext(origin: origin, owner: identifier, epoch: 2), "logout/relogin invalidates ticket")
check(context != ReaderBackupContext(origin: try ReaderBackupPolicy.origin("https://other.example", debug: false), owner: identifier, epoch: 1), "backend switch invalidates ticket")
check(context == ReaderBackupContext(origin: origin, owner: identifier, epoch: 1), "same context must be idempotent"); cases += 1
var gate = ReaderBackupContextGate()
try gate.replace(context, epoch: 1); try gate.replace(context, epoch: 1)
rejected("BACKUP_CONTEXT_CHANGED") { try gate.replace(ReaderBackupContext(origin: origin, owner: identifier, epoch: 0), epoch: 0) }
check(gate.value == context && gate.epoch == 1, "stale context cannot mutate the active epoch"); cases += 1
rejected("BACKUP_CONTEXT_CHANGED") { try gate.replace(ReaderBackupContext(origin: origin, owner: String(repeating: "b", count: 32), epoch: 1), epoch: 1) }
try gate.replace(nil, epoch: 2)
rejected("BACKUP_CONTEXT_CHANGED") { try gate.replace(ReaderBackupContext(origin: origin, owner: identifier, epoch: 2), epoch: 2) }
check(gate.value == nil && gate.epoch == 2, "clear context cannot be undone with the same or an earlier epoch"); cases += 1
gate.resetDocument()
try gate.replace(ReaderBackupContext(origin: origin, owner: identifier, epoch: 0), epoch: 0)
check(gate.epoch == 0, "new WebView document can bootstrap its new auth counter"); cases += 1
let currentDocument = String(repeating: "b", count: 32)
let previousDocument = String(repeating: "a", count: 32)
check(ReaderBackupPolicy.sameDocument(claimed: currentDocument, current: currentDocument), "same document may enter the native operation")
check(!ReaderBackupPolicy.sameDocument(claimed: previousDocument, current: currentDocument), "late old-document calls cannot enter the new document")
check(!ReaderBackupPolicy.sameDocument(claimed: nil, current: currentDocument), "missing nonce is not a current-document claim")
check(!ReaderBackupPolicy.sameDocument(claimed: currentDocument, current: nil), "new document without a wrapper cannot inherit the old nonce")
check(!ReaderBackupPolicy.sameDocument(claimed: currentDocument, current: 42), "non-string bridge values cannot authorize a document")
check(!ReaderBackupPolicy.sameDocument(claimed: currentDocument.uppercased(), current: currentDocument), "document nonce must use the exact canonical format"); cases += 1
let boundReply = ReaderBackupPolicy.bindDocument(["ready": true, "documentNonce": previousDocument, "documentProtocol": "old"], nonce: currentDocument)
check(boundReply["documentNonce"] as? String == currentDocument && boundReply["documentProtocol"] as? String == "reader-document-v1", "reply binding replaces stale or untrusted envelope fields")
check(boundReply["ready"] as? Bool == true, "reply binding preserves the allowlisted business payload")
let serializedReply = try JSONSerialization.data(withJSONObject: boundReply)
check(!serializedReply.isEmpty, "bound success, error and progress envelopes remain JSON serializable"); cases += 1

func header(_ length: UInt64 = 16) -> Data {
    var bytes = [UInt8](repeating: 0, count: 58); bytes.replaceSubrange(0..<8, with: Array("SRBKUP01".utf8))
    func number(_ value: UInt64, _ start: Int, _ count: Int) { for i in 0..<count { bytes[start + i] = UInt8(truncatingIfNeeded: value >> (8 * (count - 1 - i))) } }
    number(1, 8, 2); number(32768, 10, 4); number(8, 14, 4); number(1, 18, 4); number(length, 50, 8); return Data(bytes)
}
try ReaderBackupPolicy.inspectHeader(header(), size: 74)
rejected("BACKUP_LENGTH_INVALID") { try ReaderBackupPolicy.inspectHeader(header(UInt64.max), size: 74) }
rejected("BACKUP_LENGTH_INVALID") { try ReaderBackupPolicy.inspectHeader(header(), size: 75) }
rejected("BACKUP_SIZE_LIMIT") { try ReaderBackupPolicy.inspectHeader(header(), size: ReaderBackupPolicy.maxFileBytes + 1) }
var altered = header(); altered[9] = 2
rejected("BACKUP_VERSION_UNSUPPORTED") { try ReaderBackupPolicy.inspectHeader(altered, size: 74) }
altered = header(); altered[17] = 9
rejected("BACKUP_KDF_UNSUPPORTED") { try ReaderBackupPolicy.inspectHeader(altered, size: 74) }
rejected("BACKUP_FORMAT_INVALID") { try ReaderBackupPolicy.inspectHeader(Data(), size: 74) }; cases += 1

let exportHash = String(repeating: "a", count: 64)
for format in ["txt", "epub"] {
    let metadata = try ReaderReadingExport(operationID: identifier, format: format, size: 128, sha256: exportHash, fileName: "灯下的故事." + format)
    check(metadata.endpoint(origin).absoluteString == "https://example.com:8443/api/reader/v1/exports/" + identifier + "/download", "public route must stay on active origin")
    try metadata.inspectResponse(mime: metadata.mime, declaredBytes: 128)
    try metadata.inspectResponse(mime: metadata.mime.uppercased(), declaredBytes: -1)
    rejected("EXPORT_FILE_INVALID") { try metadata.inspectResponse(mime: "application/octet-stream", declaredBytes: 128) }
    rejected("EXPORT_FILE_INVALID") { try metadata.inspectResponse(mime: metadata.mime, declaredBytes: 129) }
    rejected("EXPORT_FILE_INVALID") { try metadata.inspectResponse(mime: nil, declaredBytes: 128) }
}; cases += 1
for name in ["../正文.txt", "路径/正文.txt", "路径\\正文.txt", "危险\n.txt", ".hidden.txt", "书.epub ", "书.srbackup", ""] {
    rejected("EXPORT_FILE_INVALID") { _ = try ReaderReadingExport(operationID: identifier, format: "txt", size: 1, sha256: exportHash, fileName: name) }
}
for size in [Int64(0), -1, ReaderReadingExport.maxBytes + 1] {
    rejected("EXPORT_FILE_INVALID") { _ = try ReaderReadingExport(operationID: identifier, format: "txt", size: size, sha256: exportHash, fileName: "正文.txt") }
}; cases += 1
for hash in ["", exportHash.uppercased(), String(repeating: "g", count: 64)] {
    rejected("EXPORT_FILE_INVALID") { _ = try ReaderReadingExport(operationID: identifier, format: "txt", size: 1, sha256: hash, fileName: "正文.txt") }
}
rejected("EXPORT_FILE_INVALID") { _ = try ReaderReadingExport(operationID: "../private", format: "txt", size: 1, sha256: exportHash, fileName: "正文.txt") }
rejected("EXPORT_FILE_INVALID") { _ = try ReaderReadingExport(operationID: identifier, format: "epub", size: 1, sha256: exportHash, fileName: "正文.txt") }; cases += 1

let root = FileManager.default.temporaryDirectory.appendingPathComponent("reader-backup-probe-" + UUID().uuidString, isDirectory: true)
try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
defer { try? FileManager.default.removeItem(at: root) }
let file = root.appendingPathComponent("synthetic.srbackup")
let size: Int64 = 128 * 1024 * 1024
check(FileManager.default.createFile(atPath: file.path, contents: nil), "allocate owned test file")
let handle = try FileHandle(forWritingTo: file)
try handle.write(contentsOf: header(UInt64(size - 58))); try handle.truncate(atOffset: UInt64(size)); try handle.seek(toOffset: UInt64(size - 1)); try handle.write(contentsOf: Data([42])); try handle.close()
check(try ReaderBackupPolicy.inspectFile(file) == size, "native preflight should read only a fixed header")
let fileMeter = MeteredInput(InputStream(url: file)!)
let large = try ReaderMultipartStream(source: fileMeter, size: size, password: "private-test")
var buffer = [UInt8](repeating: 0, count: 512 * 1024); var sent: Int64 = 0; var maximum = 0
while true {
    let count = try buffer.withUnsafeMutableBufferPointer { try large.read($0.baseAddress!, maxLength: $0.count) }
    if count == 0 { break }; sent += Int64(count); maximum = max(maximum, count)
}
check(sent == large.length && fileMeter.total == size, "actual 128MiB file must be streamed completely once")
check(maximum <= ReaderBackupPolicy.chunkBytes && fileMeter.largest <= ReaderBackupPolicy.chunkBytes, "caller buffer cannot expand native read bound")
large.close(); cases += 1
let rss = (try? String(contentsOfFile: "/proc/self/status", encoding: .utf8))?.split(separator: "\n").first { $0.hasPrefix("VmHWM:") }.map(String.init) ?? "unavailable"
let observation: [String: Any] = ["cases": cases, "failures": 0, "type": "actual-swift-foundation-native-backup", "actualFileBytes": size,
    "largestFileRead": fileMeter.largest, "rss": rss, "iOSFrameworksTypechecked": false, "realProviderCalls": 0]
print(String(data: try JSONSerialization.data(withJSONObject: observation, options: [.sortedKeys]), encoding: .utf8)!)
