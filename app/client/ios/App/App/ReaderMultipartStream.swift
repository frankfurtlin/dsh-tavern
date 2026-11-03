import Foundation

/// Sequential encoder for a single multipart request. No seek, replay or full-file copy.
final class ReaderMultipartStream {
    let boundary: String
    let length: Int64
    private let source: InputStream
    private let expectedSize: Int64
    private var prefix: Data
    private var suffix: Data
    private var framingOffset = 0
    private var fileRead: Int64 = 0
    private var phase = 0
    private var opened = false
    private var closed = false
    private var failed: Error?
    private let lock = NSLock()

    init(source: InputStream, size: Int64, password: String, boundary: String = "reader-" + UUID().uuidString) throws {
        guard size >= 0, size <= ReaderBackupPolicy.maxFileBytes,
              !password.isEmpty, password.utf8.count <= 1024,
              (8...80).contains(boundary.utf8.count), boundary.utf8.allSatisfy({
                  (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 45
              }) else { throw ReaderBackupFailure.code("BACKUP_REQUEST_INVALID") }
        self.source = source; self.expectedSize = size; self.boundary = boundary
        self.prefix = Data(("--\(boundary)\r\nContent-Disposition: form-data; name=\"password\"\r\n\r\n" + password +
            "\r\n--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"private.srbackup\"\r\nContent-Type: application/octet-stream\r\n\r\n").utf8)
        self.suffix = Data("\r\n--\(boundary)--\r\n".utf8)
        self.length = size + Int64(prefix.count + suffix.count)
    }
    func read(_ buffer: UnsafeMutablePointer<UInt8>, maxLength: Int) throws -> Int {
        lock.lock(); defer { lock.unlock() }
        if let error = failed { throw error }
        guard !closed else { throw ReaderBackupFailure.code("BACKUP_CANCELLED") }
        guard maxLength > 0 else { return 0 }
        if !opened { source.open(); opened = true }
        let limit = min(maxLength, ReaderBackupPolicy.chunkBytes)
        do {
            while phase < 3 {
                if phase == 1 {
                    if fileRead < expectedSize {
                        let read = source.read(buffer, maxLength: min(limit, Int(expectedSize - fileRead)))
                        guard read >= 0 else { throw ReaderBackupFailure.code("BACKUP_FILE_UNAVAILABLE") }
                        guard read > 0 else { throw ReaderBackupFailure.code("BACKUP_SOURCE_CHANGED") }
                        fileRead += Int64(read); return read
                    }
                    // A provider can replace or grow a file after selection; do not append its new bytes.
                    var extra: UInt8 = 0
                    let count = source.read(&extra, maxLength: 1)
                    guard count == 0 else { throw ReaderBackupFailure.code(count < 0 ? "BACKUP_FILE_UNAVAILABLE" : "BACKUP_SOURCE_CHANGED") }
                    source.close(); phase = 2; framingOffset = 0; continue
                }
                let data = phase == 0 ? prefix : suffix
                let count = min(limit, data.count - framingOffset)
                if count > 0 {
                    data.withUnsafeBytes { raw in buffer.update(from: raw.baseAddress!.assumingMemoryBound(to: UInt8.self).advanced(by: framingOffset), count: count) }
                    framingOffset += count; return count
                }
                if phase == 0 { prefix.resetBytes(in: 0..<prefix.count); prefix.removeAll(keepingCapacity: false) }
                phase += 1; framingOffset = 0
            }
            return 0
        } catch { failed = error; source.close(); prefix.resetBytes(in: 0..<prefix.count); prefix.removeAll(); throw error }
    }
    func close() {
        lock.lock(); defer { lock.unlock() }
        closed = true; source.close()
        prefix.resetBytes(in: 0..<prefix.count); prefix.removeAll(keepingCapacity: false)
        suffix.removeAll(keepingCapacity: false)
    }
    deinit { close() }
}
