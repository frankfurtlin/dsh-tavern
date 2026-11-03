import Foundation

enum ReaderBackupFailure: Error { case code(String) }

/// The native channel accepts file handles and metadata; file bytes never enter JS.
enum ReaderBackupPolicy {
    static let maxFileBytes: Int64 = 2 * 1024 * 1024 * 1024 + 64 * 1024 * 1024 + 128
    static let chunkBytes = 64 * 1024
    static let maxResponseBytes = 256 * 1024

    static func validID(_ value: String) -> Bool {
        value.utf8.count == 32 && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }
    static func sameDocument(claimed: String?, current: Any?) -> Bool {
        guard let claimed = claimed, let current = current as? String,
              validID(claimed), validID(current) else { return false }
        return claimed == current
    }
    static func bindDocument(_ payload: [String: Any], nonce: String) -> [String: Any] {
        var result = payload
        result["documentProtocol"] = "reader-document-v1"
        result["documentNonce"] = nonce
        return result
    }
    static func origin(_ value: String, debug: Bool) throws -> URL {
        guard value.utf8.count <= 2048, let parts = URLComponents(string: value),
              let host = parts.host, !host.isEmpty, parts.user == nil, parts.password == nil,
              parts.query == nil, parts.fragment == nil, parts.path.isEmpty || parts.path == "/",
              parts.scheme == "https" || (debug && parts.scheme == "http" && ["localhost", "127.0.0.1", "10.0.2.2", "::1", "[::1]"].contains(host.lowercased())),
              parts.port == nil || (1...65535).contains(parts.port!), let url = parts.url else {
            throw ReaderBackupFailure.code("BACKUP_SERVER_INVALID")
        }
        return url
    }
    static func endpoint(_ origin: URL, operationID: String? = nil) throws -> URL {
        if let identifier = operationID, !validID(identifier) { throw ReaderBackupFailure.code("BACKUP_REQUEST_INVALID") }
        let path = operationID.map { "api/reader/v1/backups/\($0)/download" } ?? "api/reader/v1/backups/prepare"
        return origin.appendingPathComponent(path)
    }
    static func credentials(password: String, requestID: String, token: String) throws {
        guard !password.isEmpty, password.utf8.count <= 1024,
              (1...160).contains(requestID.utf8.count), requestID.utf8.allSatisfy({
                  (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || [45, 58, 95].contains($0)
              }), validToken(token) else { throw ReaderBackupFailure.code("BACKUP_REQUEST_INVALID") }
    }
    static func validToken(_ value: String) -> Bool {
        (1...8192).contains(value.utf8.count) && value.utf8.allSatisfy { (33...126).contains($0) }
    }
    static func authorizeToken(_ value: String, owner: String) throws {
        // Accident protection only: the server still verifies the signature,
        // account state and device session. Never use this as authorization.
        let pieces = value.split(separator: ".", omittingEmptySubsequences: false)
        guard validToken(value), pieces.count == 3, validID(owner) else { throw ReaderBackupFailure.code("BACKUP_CONTEXT_CHANGED") }
        var encoded = String(pieces[1]).replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        encoded += String(repeating: "=", count: (4 - encoded.utf8.count % 4) % 4)
        guard let bytes = Data(base64Encoded: encoded), bytes.count <= 6144,
              let claims = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any], claims["sub"] as? String == owner else {
            throw ReaderBackupFailure.code("BACKUP_CONTEXT_CHANGED")
        }
    }
    static func fileSize(_ size: Int64) throws {
        guard size >= 74, size <= maxFileBytes else { throw ReaderBackupFailure.code("BACKUP_SIZE_LIMIT") }
    }
    static func inspectHeader(_ data: Data, size: Int64) throws {
        try fileSize(size)
        guard data.count == 58, Array(data.prefix(8)) == Array("SRBKUP01".utf8) else {
            throw ReaderBackupFailure.code("BACKUP_FORMAT_INVALID")
        }
        func number(_ start: Int, _ count: Int) -> UInt64 {
            data[start..<(start + count)].reduce(UInt64(0)) { ($0 << 8) | UInt64($1) }
        }
        guard number(8, 2) == 1 else { throw ReaderBackupFailure.code("BACKUP_VERSION_UNSUPPORTED") }
        guard number(10, 4) == 32768, number(14, 4) == 8, number(18, 4) == 1 else {
            throw ReaderBackupFailure.code("BACKUP_KDF_UNSUPPORTED")
        }
        guard number(50, 8) >= 16, number(50, 8) == UInt64(size - 58) else {
            throw ReaderBackupFailure.code("BACKUP_LENGTH_INVALID")
        }
    }
    static func inspectFile(_ url: URL) throws -> Int64 {
        let values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true, let size = values.fileSize else {
            throw ReaderBackupFailure.code("BACKUP_FILE_UNAVAILABLE")
        }
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        try inspectHeader(try handle.read(upToCount: 58) ?? Data(), size: Int64(size))
        return Int64(size)
    }
    static func code(_ error: Error, fallback: String) -> String {
        if case ReaderBackupFailure.code(let code) = error { return code }
        return fallback
    }
}

/// Completion tickets remain bound to the account, backend and explicit epoch.
struct ReaderBackupContext: Equatable {
    let origin: URL
    let owner: String
    let epoch: Int
}

struct ReaderBackupContextGate {
    private(set) var epoch = -1
    private(set) var value: ReaderBackupContext?
    mutating func replace(_ next: ReaderBackupContext?, epoch: Int) throws {
        guard epoch >= 0, epoch >= self.epoch, next == nil || next!.epoch == epoch else { throw ReaderBackupFailure.code("BACKUP_CONTEXT_CHANGED") }
        if epoch == self.epoch && value != next { throw ReaderBackupFailure.code("BACKUP_CONTEXT_CHANGED") }
        self.epoch = epoch; value = next
    }
    mutating func resetDocument() { epoch = -1; value = nil }
}

/// Public reading files use their own format and metadata, never SRBKUP framing.
struct ReaderReadingExport {
    static let maxBytes: Int64 = 2 * 1024 * 1024 * 1024
    let operationID: String
    let format: String
    let size: Int64
    let sha256: String
    let fileName: String
    var mime: String { format == "txt" ? "text/plain" : "application/epub+zip" }
    init(operationID: String, format: String, size: Int64, sha256: String, fileName: String) throws {
        guard ReaderBackupPolicy.validID(operationID), ["txt", "epub"].contains(format),
              size > 0, size <= Self.maxBytes, sha256.utf8.count == 64,
              sha256.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }),
              Self.validFileName(fileName), fileName.hasSuffix("." + format) else {
            throw ReaderBackupFailure.code("EXPORT_FILE_INVALID")
        }
        self.operationID = operationID; self.format = format; self.size = size; self.sha256 = sha256; self.fileName = fileName
    }
    static func validFileName(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.count <= 512 && !value.hasPrefix(".") && !value.hasSuffix(" ") &&
        !value.unicodeScalars.contains { $0.value < 32 || $0.value == 127 || "\\/:*?\"<>|".unicodeScalars.contains($0) } &&
        (value.hasSuffix(".txt") || value.hasSuffix(".epub"))
    }
    func endpoint(_ origin: URL) -> URL { origin.appendingPathComponent("api/reader/v1/exports/" + operationID + "/download") }
    func inspectResponse(mime: String?, declaredBytes: Int64) throws {
        guard mime?.lowercased() == self.mime, declaredBytes < 0 || declaredBytes == size else {
            throw ReaderBackupFailure.code("EXPORT_FILE_INVALID")
        }
    }
}
