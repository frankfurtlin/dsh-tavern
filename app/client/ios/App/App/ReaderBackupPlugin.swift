import Foundation
import UIKit
import UniformTypeIdentifiers
import WebKit
import Capacitor
import CryptoKit

/// A bounded producer feeds an ordinary Foundation bound stream, rather than
/// exposing a custom InputStream to CFNetwork. The encoder is never replayed.
private final class ReaderBackupBodyProducer: NSObject, StreamDelegate {
    let input: InputStream
    private let output: OutputStream
    private let encoder: ReaderMultipartStream
    private let failure: () -> Void
    private let stateLock = NSLock()
    private var stopped = false
    private var buffer = [UInt8](repeating: 0, count: ReaderBackupPolicy.chunkBytes)
    private var offset = 0
    private var remaining = 0
    private var done = false

    init(encoder: ReaderMultipartStream, failure: @escaping () -> Void) throws {
        var reader: InputStream?; var writer: OutputStream?
        Stream.getBoundStreams(withBufferSize: ReaderBackupPolicy.chunkBytes, inputStream: &reader, outputStream: &writer)
        guard let reader = reader, let writer = writer else { throw ReaderBackupFailure.code("BACKUP_STREAM_UNAVAILABLE") }
        self.input = reader; self.output = writer; self.encoder = encoder; self.failure = failure
    }
    func start() {
        Thread.detachNewThread { [self] in
            let loop = RunLoop.current
            output.delegate = self; output.schedule(in: loop, forMode: .default); output.open()
            let deadline = Date().addingTimeInterval(3600)
            while !done && !isStopped() && Date() < deadline { _ = loop.run(mode: .default, before: Date().addingTimeInterval(0.2)) }
            if !done && !isStopped() { failure() }
            output.close(); output.remove(from: loop, forMode: .default); output.delegate = nil; encoder.close()
        }
    }
    private func isStopped() -> Bool { stateLock.lock(); defer { stateLock.unlock() }; return stopped }
    func cancel() { stateLock.lock(); stopped = true; stateLock.unlock(); input.close() }
    func stream(_ aStream: Stream, handle eventCode: Stream.Event) {
        guard !isStopped(), !done else { return }
        if eventCode.contains(.errorOccurred) { done = true; failure(); return }
        guard eventCode.contains(.hasSpaceAvailable) else { return }
        do {
            while output.hasSpaceAvailable && !isStopped() {
                if remaining == 0 {
                    remaining = try buffer.withUnsafeMutableBufferPointer { try encoder.read($0.baseAddress!, maxLength: $0.count) }; offset = 0
                    if remaining == 0 { done = true; output.close(); return }
                }
                let count = buffer.withUnsafeBufferPointer { output.write($0.baseAddress!.advanced(by: offset), maxLength: remaining) }
                guard count >= 0 else { throw ReaderBackupFailure.code("BACKUP_STREAM_UNAVAILABLE") }
                if count == 0 { return }
                offset += count; remaining -= count
            }
        } catch { done = true; failure() }
    }
}

@objc(ReaderBackupPlugin)
public final class ReaderBackupPlugin: CAPPlugin, CAPBridgedPlugin, UIDocumentPickerDelegate, URLSessionDataDelegate, URLSessionDownloadDelegate {
    public let identifier = "ReaderBackupPlugin"
    public let jsName = "ReaderBackup"
    public let pluginMethods: [CAPPluginMethod] = ["setContext", "getCapabilities", "pickBackup", "prepareBackup", "downloadBackup", "downloadReadingExport", "cancel"].map {
        CAPPluginMethod(name: $0, returnType: CAPPluginReturnPromise)
    }
    private struct Selection { let id: String; let url: URL; let size: Int64; let scoped: Bool; let context: ReaderBackupContext }
    private final class Transfer {
        let call: CAPPluginCall
        let context: ReaderBackupContext
        let download: Bool
        var task: URLSessionTask?
        var session: URLSession?
        var producer: ReaderBackupBodyProducer?
        var streamProvided = false
        var response = Data()
        var code: Int?
        var privateURL: URL?
        var awaitingSave = false
        var verifyingExport = false
        var readingExport: ReaderReadingExport?
        init(call: CAPPluginCall, context: ReaderBackupContext, download: Bool) { self.call = call; self.context = context; self.download = download }
    }
    private var context: ReaderBackupContext?
    private var selection: Selection?
    private var transfer: Transfer?
    private var pickCall: CAPPluginCall?
    private var pickContext: ReaderBackupContext?
    private var picker: UIDocumentPickerViewController?
    private var initializationFailed = false
    private var contextGate = ReaderBackupContextGate()
    private let documentLock = NSLock()
    private var documentGeneration = 0
    private var navigationObservation: NSKeyValueObservation?

    public override func load() {
        DispatchQueue.main.async { [weak self] in
            do { try self?.clearTemporaryFiles() } catch { self?.initializationFailed = true }
            self?.navigationObservation = self?.webView?.observe(\.isLoading, options: [.new]) { [weak self] _, change in
                guard change.newValue == true else { return }
                // An actual document load resets the bridge's callback IDs.
                // No old reply may be delivered to a new document's promise.
                if Thread.isMainThread { self?.resetDocument() }
                else { DispatchQueue.main.async { self?.resetDocument() } }
            }
        }
    }
    deinit { navigationObservation?.invalidate() }
    private func documentStamp() -> Int { documentLock.lock(); defer { documentLock.unlock() }; return documentGeneration }
    private func resetDocument() {
        documentLock.lock(); documentGeneration += 1; documentLock.unlock()
        context = nil; contextGate.resetDocument()
        if let item = transfer {
            item.producer?.cancel(); item.session?.invalidateAndCancel()
            item.response.resetBytes(in: 0..<item.response.count); item.response.removeAll()
        }
        transfer = nil; pickCall = nil; pickContext = nil; picker?.dismiss(animated: false); picker = nil; releaseSelection()
        do { try clearTemporaryFiles(); initializationFailed = false } catch { initializationFailed = true }
    }
    private func main(_ call: CAPPluginCall, _ action: @escaping () throws -> Void) {
        let stamp = documentStamp()
        let claimedDocument = call.getString("documentNonce")
        DispatchQueue.main.async { [weak self] in
            guard let self = self, stamp == self.documentStamp(), let webView = self.webView else { return }
            // Capacitor can dispatch an old JS message after a navigation has
            // already reset callback IDs. A stamp captured at plugin entry is
            // insufficient for that queued-call case. Bind every operation to
            // the nonce installed by the current document's JS wrapper, before
            // touching context, file handles, network requests or callbacks.
            webView.evaluateJavaScript("window.__readerBackupDocumentNonce") { [weak self] current, error in
                guard let self = self, error == nil, stamp == self.documentStamp(),
                      ReaderBackupPolicy.sameDocument(claimed: claimedDocument, current: current) else { return }
                do { try action() } catch { self.reject(call, ReaderBackupPolicy.code(error, fallback: "BACKUP_FILE_UNAVAILABLE")) }
            }
        }
    }
    private func replyData(_ call: CAPPluginCall, _ payload: [String: Any]) -> [String: Any]? {
        guard let nonce = call.getString("documentNonce"), ReaderBackupPolicy.validID(nonce) else { return nil }
        return ReaderBackupPolicy.bindDocument(payload, nonce: nonce)
    }
    private func resolve(_ call: CAPPluginCall, _ payload: [String: Any]) {
        guard let payload = replyData(call, payload) else { return }
        call.resolve(payload)
    }
    private func reject(_ call: CAPPluginCall, _ code: String) {
        let message: String
        switch code {
        case "BACKUP_RESPONSE_UNKNOWN": message = "备份请求的结果尚未确认，请查询已有请求"
        case "BACKUP_CANCELLED": message = "已取消文件操作"
        case "BACKUP_CONTEXT_CHANGED": message = "账号或服务器已变化，请重新打开备份"
        case "BACKUP_TRANSFER_BUSY": message = "请先完成当前文件操作"
        case "BACKUP_SIZE_LIMIT": message = "备份超过文件通道支持的大小"
        default: message = "备份文件暂时无法处理，请检查文件或查询已有请求"
        }
        guard let data = replyData(call, [:]) else { return }
        call.reject(message, code, nil, data)
    }
    private func current(_ call: CAPPluginCall) throws -> ReaderBackupContext {
        guard !initializationFailed, let value = context else { throw ReaderBackupFailure.code("BACKUP_CONTEXT_CHANGED") }
        return value
    }
    private func available() throws {
        guard transfer == nil, pickCall == nil, picker == nil else { throw ReaderBackupFailure.code("BACKUP_TRANSFER_BUSY") }
    }
    private func temporaryRoot() throws -> URL {
        let cache = try FileManager.default.url(for: .cachesDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        let root = cache.resolvingSymlinksInPath().appendingPathComponent("ReaderBackup-v1", isDirectory: true)
        guard root.standardizedFileURL.resolvingSymlinksInPath() == root.standardizedFileURL else { throw ReaderBackupFailure.code("BACKUP_STAGING_INVALID") }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true, attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
        var protected = root; var values = URLResourceValues(); values.isExcludedFromBackup = true; try protected.setResourceValues(values)
        return root
    }
    private func removeTemporary(_ file: URL) throws {
        let root = try temporaryRoot()
        let folder = file.deletingLastPathComponent()
        if folder.lastPathComponent.hasPrefix("reader-export-") {
            try validateExportFolder(folder, root: root)
            guard ReaderReadingExport.validFileName(file.lastPathComponent), file.standardizedFileURL.resolvingSymlinksInPath() == file.standardizedFileURL else { throw ReaderBackupFailure.code("BACKUP_STAGING_INVALID") }
            if FileManager.default.fileExists(atPath: file.path) { try FileManager.default.removeItem(at: file) }
            if try FileManager.default.contentsOfDirectory(atPath: folder.path).isEmpty { try FileManager.default.removeItem(at: folder) }
            return
        }
        let name = file.lastPathComponent
        guard file.standardizedFileURL.deletingLastPathComponent() == root.standardizedFileURL,
              file.standardizedFileURL.resolvingSymlinksInPath() == file.standardizedFileURL,
              name.hasPrefix("reader-backup-"), name.hasSuffix(".srbackup"),
              ReaderBackupPolicy.validID(String(name.dropFirst(14).dropLast(9))) else { throw ReaderBackupFailure.code("BACKUP_STAGING_INVALID") }
        if FileManager.default.fileExists(atPath: file.path) { try FileManager.default.removeItem(at: file) }
    }
    private func clearTemporaryFiles() throws {
        let root = try temporaryRoot()
        for file in try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil) {
            if file.lastPathComponent.hasPrefix("reader-export-") {
                try validateExportFolder(file, root: root)
                let children = try FileManager.default.contentsOfDirectory(at: file, includingPropertiesForKeys: nil)
                if children.isEmpty { try FileManager.default.removeItem(at: file) }
                else { for child in children { try removeTemporary(child) } }
            } else { try removeTemporary(file) }
        }
    }
    private func validateExportFolder(_ folder: URL, root: URL) throws {
        guard folder.standardizedFileURL.deletingLastPathComponent() == root.standardizedFileURL,
              folder.standardizedFileURL.resolvingSymlinksInPath() == folder.standardizedFileURL,
              ReaderBackupPolicy.validID(String(folder.lastPathComponent.dropFirst(14))) else { throw ReaderBackupFailure.code("BACKUP_STAGING_INVALID") }
    }
    private func verifyReadingFile(_ file: URL, metadata: ReaderReadingExport) throws {
        let values = try file.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true, Int64(values.fileSize ?? -1) == metadata.size else { throw ReaderBackupFailure.code("EXPORT_FILE_INVALID") }
        let handle = try FileHandle(forReadingFrom: file); defer { try? handle.close() }
        var hash = SHA256(); var count: Int64 = 0
        while let bytes = try handle.read(upToCount: ReaderBackupPolicy.chunkBytes), !bytes.isEmpty {
            count += Int64(bytes.count)
            guard count <= metadata.size else { throw ReaderBackupFailure.code("EXPORT_FILE_INVALID") }
            hash.update(data: bytes)
        }
        let digest = hash.finalize().map { String(format: "%02x", $0) }.joined()
        guard count == metadata.size, digest == metadata.sha256 else { throw ReaderBackupFailure.code("EXPORT_FILE_INVALID") }
    }
    private func releaseSelection() {
        if let value = selection, value.scoped { value.url.stopAccessingSecurityScopedResource() }
        selection = nil
    }
    private func finish(_ item: Transfer, result: [String: Any]? = nil, code: String? = nil) {
        guard transfer === item else { return }
        item.producer?.cancel(); item.producer = nil; item.session?.invalidateAndCancel()
        item.response.resetBytes(in: 0..<item.response.count); item.response.removeAll()
        var errorCode = code
        if let file = item.privateURL { do { try removeTemporary(file) } catch { initializationFailed = true; errorCode = "BACKUP_CLEANUP_FAILED" } }
        transfer = nil
        if context != item.context { reject(item.call, "BACKUP_CONTEXT_CHANGED") }
        else if let code = errorCode { reject(item.call, code) }
        else { resolve(item.call, result ?? [:]) }
    }
    @objc func setContext(_ call: CAPPluginCall) {
        main(call) { [self] in
            guard let owner = call.getString("ownerId"), let epoch = call.getInt("epoch"), epoch >= 0 else { throw ReaderBackupFailure.code("BACKUP_CONTEXT_CHANGED") }
            let next: ReaderBackupContext?
            if owner.isEmpty { next = nil }
            else {
                guard ReaderBackupPolicy.validID(owner), let backend = call.getString("backend") else { throw ReaderBackupFailure.code("BACKUP_CONTEXT_CHANGED") }
                #if DEBUG
                let debug = true
                #else
                let debug = false
                #endif
                next = ReaderBackupContext(origin: try ReaderBackupPolicy.origin(backend, debug: debug), owner: owner, epoch: epoch)
            }
            try contextGate.replace(next, epoch: epoch)
            if context == next && !initializationFailed { resolve(call, ["ready": true]); return }
            context = nil
            if let item = transfer { finish(item, code: "BACKUP_CONTEXT_CHANGED") }
            if let pending = pickCall { reject(pending, "BACKUP_CONTEXT_CHANGED") }
            pickCall = nil; pickContext = nil; picker?.dismiss(animated: false); picker = nil; releaseSelection()
            do { try clearTemporaryFiles(); initializationFailed = false } catch { initializationFailed = true; throw ReaderBackupFailure.code("BACKUP_CLEANUP_FAILED") }
            context = next; resolve(call, ["ready": true])
        }
    }
    @objc func getCapabilities(_ call: CAPPluginCall) {
        main(call) { [self] in resolve(call, ["ready": !initializationFailed, "streaming": true, "maxFileBytes": ReaderBackupPolicy.maxFileBytes, "readingExport": true, "maxReadingBytes": ReaderReadingExport.maxBytes]) }
    }
    @objc func cancel(_ call: CAPPluginCall) {
        main(call) { [self] in
            let _ = try current(call)
            guard call.getInt("epoch") == context?.epoch else { throw ReaderBackupFailure.code("BACKUP_CONTEXT_CHANGED") }
            if let item = transfer { finish(item, code: "BACKUP_CANCELLED") }
            if let pending = pickCall { reject(pending, "BACKUP_CANCELLED") }
            pickCall = nil; pickContext = nil; picker?.dismiss(animated: false); picker = nil; releaseSelection()
            resolve(call, [:])
        }
    }
    @objc func pickBackup(_ call: CAPPluginCall) {
        main(call) { [self] in
            let value = try current(call); try available()
            guard let controller = bridge?.viewController, controller.presentedViewController == nil else { throw ReaderBackupFailure.code("BACKUP_PICKER_UNAVAILABLE") }
            let content = UTType(filenameExtension: "srbackup") ?? .data
            let dialog = UIDocumentPickerViewController(forOpeningContentTypes: [content], asCopy: false)
            dialog.allowsMultipleSelection = false; dialog.delegate = self
            pickCall = call; pickContext = value; picker = dialog; controller.present(dialog, animated: true)
        }
    }
    public func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) {
        guard picker === controller else { return }; picker = nil
        if let call = pickCall { pickCall = nil; pickContext = nil; reject(call, "BACKUP_CANCELLED") }
        if let item = transfer, item.awaitingSave { finish(item, code: "BACKUP_CANCELLED") }
    }
    public func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
        guard picker === controller else { return }; picker = nil
        if let item = transfer, item.awaitingSave { finish(item, result: ["saved": !urls.isEmpty], code: urls.isEmpty ? "BACKUP_CANCELLED" : nil); return }
        guard let call = pickCall, let captured = pickContext else { return }
        guard let url = urls.first else {
            pickCall = nil; pickContext = nil; releaseSelection()
            reject(call, "BACKUP_CANCELLED"); return
        }
        guard context == captured else {
            pickCall = nil; pickContext = nil
            reject(call, "BACKUP_CONTEXT_CHANGED"); return
        }
        let scoped = url.startAccessingSecurityScopedResource()
        // Only the 58-byte header and file metadata are read for native preflight.
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let inspected = Result { try ReaderBackupPolicy.inspectFile(url) }
            DispatchQueue.main.async {
                guard let self = self else { if scoped { url.stopAccessingSecurityScopedResource() }; return }
                guard self.pickCall === call else { if scoped { url.stopAccessingSecurityScopedResource() }; return }
                self.pickCall = nil; self.pickContext = nil
                guard self.context == captured, self.transfer == nil else { if scoped { url.stopAccessingSecurityScopedResource() }; self.reject(call, "BACKUP_CONTEXT_CHANGED"); return }
                switch inspected {
                case .failure(let error): if scoped { url.stopAccessingSecurityScopedResource() }; self.reject(call, ReaderBackupPolicy.code(error, fallback: "BACKUP_FILE_UNAVAILABLE"))
                case .success(let size):
                    self.releaseSelection()
                    let id = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
                    self.selection = Selection(id: id, url: url, size: size, scoped: scoped, context: captured)
                    let display = String(String.UnicodeScalarView(url.lastPathComponent.unicodeScalars.filter { !CharacterSet.controlCharacters.contains($0) }.prefix(240)))
                    self.resolve(call, ["selectionId": id, "size": size, "displayName": display])
                }
            }
        }
    }
    private func session(_ item: Transfer) -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil; configuration.httpCookieStorage = nil; configuration.urlCredentialStorage = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.timeoutIntervalForRequest = 120; configuration.timeoutIntervalForResource = 3600
        configuration.waitsForConnectivity = false; configuration.httpMaximumConnectionsPerHost = 1
        return URLSession(configuration: configuration, delegate: self, delegateQueue: .main)
    }
    @objc func prepareBackup(_ call: CAPPluginCall) {
        main(call) { [self] in
            let value = try current(call); try available()
            guard let selected = selection, selected.context == value, selected.id == call.getString("selectionId"),
                  let password = call.getString("password"), let requestID = call.getString("requestId"), let token = call.getString("accessToken"),
                  call.getInt("epoch") == value.epoch else { throw ReaderBackupFailure.code("BACKUP_REQUEST_INVALID") }
            try ReaderBackupPolicy.credentials(password: password, requestID: requestID, token: token)
            try ReaderBackupPolicy.authorizeToken(token, owner: value.owner)
            guard try ReaderBackupPolicy.inspectFile(selected.url) == selected.size, let source = InputStream(url: selected.url) else { throw ReaderBackupFailure.code("BACKUP_SOURCE_CHANGED") }
            let encoder = try ReaderMultipartStream(source: source, size: selected.size, password: password)
            let item = Transfer(call: call, context: value, download: false)
            item.producer = try ReaderBackupBodyProducer(encoder: encoder) { [weak self, weak item] in
                DispatchQueue.main.async { guard let self = self, let item = item else { return }; self.finish(item, code: "BACKUP_RESPONSE_UNKNOWN") }
            }
            var request = URLRequest(url: try ReaderBackupPolicy.endpoint(value.origin))
            request.httpMethod = "POST"; request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
            request.setValue(requestID, forHTTPHeaderField: "Idempotency-Key")
            request.setValue("multipart/form-data; boundary=" + encoder.boundary, forHTTPHeaderField: "Content-Type")
            request.setValue(String(encoder.length), forHTTPHeaderField: "Content-Length")
            request.setValue("application/json", forHTTPHeaderField: "Accept")
            transfer = item; item.session = session(item); item.task = item.session!.uploadTask(withStreamedRequest: request); item.task!.resume()
        }
    }
    @objc func downloadBackup(_ call: CAPPluginCall) {
        main(call) { [self] in
            let value = try current(call); try available()
            guard let identifier = call.getString("operationId"), ReaderBackupPolicy.validID(identifier), let token = call.getString("accessToken"),
                  ReaderBackupPolicy.validToken(token), call.getInt("epoch") == value.epoch else { throw ReaderBackupFailure.code("BACKUP_REQUEST_INVALID") }
            try ReaderBackupPolicy.authorizeToken(token, owner: value.owner)
            var request = URLRequest(url: try ReaderBackupPolicy.endpoint(value.origin, operationID: identifier))
            request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
            request.setValue("application/octet-stream", forHTTPHeaderField: "Accept")
            let item = Transfer(call: call, context: value, download: true)
            transfer = item; item.session = session(item); item.task = item.session!.downloadTask(with: request); item.task!.resume()
        }
    }
    @objc func downloadReadingExport(_ call: CAPPluginCall) {
        main(call) { [self] in
            let value = try current(call); try available()
            guard let identifier = call.getString("operationId"), let format = call.getString("format"),
                  let size = call.getInt("size"), let hash = call.getString("sha256"), let name = call.getString("fileName"),
                  let token = call.getString("accessToken"), call.getInt("epoch") == value.epoch else { throw ReaderBackupFailure.code("EXPORT_FILE_INVALID") }
            try ReaderBackupPolicy.authorizeToken(token, owner: value.owner)
            let metadata = try ReaderReadingExport(operationID: identifier, format: format, size: Int64(size), sha256: hash, fileName: name)
            var request = URLRequest(url: metadata.endpoint(value.origin))
            request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization"); request.setValue(metadata.mime, forHTTPHeaderField: "Accept")
            let item = Transfer(call: call, context: value, download: true); item.readingExport = metadata
            transfer = item; item.session = session(item); item.task = item.session!.downloadTask(with: request); item.task!.resume()
        }
    }
    private func active(_ task: URLSessionTask) -> Transfer? {
        guard let item = transfer, item.task === task, context == item.context else { return nil }; return item
    }
    public func urlSession(_ session: URLSession, task: URLSessionTask, needNewBodyStream completionHandler: @escaping (InputStream?) -> Void) {
        guard let item = active(task), !item.download, !item.streamProvided, let producer = item.producer else { completionHandler(nil); return }
        item.streamProvided = true; completionHandler(producer.input); producer.start()
    }
    public func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil) // Never resend credentials or a multipart POST to a redirect target.
    }
    public func urlSession(_ session: URLSession, task: URLSessionTask, didReceive challenge: URLAuthenticationChallenge, completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        if challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust { completionHandler(.performDefaultHandling, nil) }
        else { completionHandler(.cancelAuthenticationChallenge, nil) }
    }
    public func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse, completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let item = active(dataTask), let response = response as? HTTPURLResponse,
              response.expectedContentLength <= Int64(ReaderBackupPolicy.maxResponseBytes) else { completionHandler(.cancel); return }
        item.code = response.statusCode; completionHandler(.allow)
    }
    public func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard let item = active(dataTask), item.response.count + data.count <= ReaderBackupPolicy.maxResponseBytes else {
            if let item = active(dataTask) { finish(item, code: "BACKUP_RESPONSE_UNKNOWN") }; return
        }
        item.response.append(data)
    }
    public func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64, totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        guard let item = active(task), let data = replyData(item.call, ["phase": "upload", "bytes": totalBytesSent, "totalBytes": max(0, totalBytesExpectedToSend)]) else { return }
        notifyListeners("backupProgress", data: data)
    }
    public func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64, totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        guard let item = active(downloadTask) else { return }
        let maximum = item.readingExport?.size ?? ReaderBackupPolicy.maxFileBytes
        guard totalBytesWritten <= maximum, totalBytesExpectedToWrite <= maximum else { finish(item, code: item.readingExport == nil ? "BACKUP_SIZE_LIMIT" : "EXPORT_FILE_INVALID"); return }
        guard let data = replyData(item.call, ["phase": "download", "bytes": totalBytesWritten, "totalBytes": max(0, totalBytesExpectedToWrite)]) else { return }
        notifyListeners("backupProgress", data: data)
    }
    public func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        guard let item = active(downloadTask), let response = downloadTask.response as? HTTPURLResponse, response.statusCode == 200 else {
            if let item = active(downloadTask) { finish(item, code: "BACKUP_DOWNLOAD_FAILED") }; return
        }
        do {
            if let metadata = item.readingExport {
                try metadata.inspectResponse(mime: response.mimeType, declaredBytes: response.expectedContentLength)
                let root = try temporaryRoot()
                let folder = root.appendingPathComponent("reader-export-" + UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased(), isDirectory: true)
                try validateExportFolder(folder, root: root)
                try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: false, attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
                let file = folder.appendingPathComponent(metadata.fileName); item.privateURL = file
                try FileManager.default.moveItem(at: location, to: file)
                try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: file.path)
                item.verifyingExport = true
                DispatchQueue.global(qos: .utility).async { [weak self, weak item] in
                    guard let self = self, let item = item else { return }
                    let failure: Error?
                    do { try self.verifyReadingFile(file, metadata: metadata); failure = nil } catch { failure = error }
                    DispatchQueue.main.async {
                        guard self.transfer === item, self.context == item.context else { return }
                        item.verifyingExport = false
                        if let error = failure { self.finish(item, code: ReaderBackupPolicy.code(error, fallback: "EXPORT_FILE_INVALID")); return }
                        guard let controller = self.bridge?.viewController, controller.presentedViewController == nil else { self.finish(item, code: "BACKUP_PICKER_UNAVAILABLE"); return }
                        let dialog = UIDocumentPickerViewController(forExporting: [file], asCopy: true)
                        dialog.delegate = self; item.awaitingSave = true; self.picker = dialog; controller.present(dialog, animated: true)
                    }
                }
                return
            }
            _ = try ReaderBackupPolicy.inspectFile(location)
            let file = try temporaryRoot().appendingPathComponent("reader-backup-" + UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased() + ".srbackup")
            item.privateURL = file
            try FileManager.default.moveItem(at: location, to: file)
            try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: file.path)
            guard let controller = bridge?.viewController, controller.presentedViewController == nil else { throw ReaderBackupFailure.code("BACKUP_PICKER_UNAVAILABLE") }
            let dialog = UIDocumentPickerViewController(forExporting: [file], asCopy: true)
            dialog.delegate = self; item.awaitingSave = true; picker = dialog; controller.present(dialog, animated: true)
        } catch { finish(item, code: ReaderBackupPolicy.code(error, fallback: "BACKUP_DOWNLOAD_FAILED")) }
    }
    public func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard let item = active(task) else { return }
        if (item.awaitingSave || item.verifyingExport) && error == nil { item.session?.finishTasksAndInvalidate(); return }
        guard error == nil, !item.download, let status = item.code,
              let object = try? JSONSerialization.jsonObject(with: item.response) as? [String: Any] else {
            finish(item, code: item.download ? "BACKUP_DOWNLOAD_FAILED" : "BACKUP_RESPONSE_UNKNOWN"); return
        }
        if (400..<500).contains(status), let code = object["code"] as? String,
           (1...100).contains(code.utf8.count), code.utf8.allSatisfy({ (65...90).contains($0) || (48...57).contains($0) || $0 == 95 }) { finish(item, code: code); return }
        guard (200..<300).contains(status), object["protocol"] as? String == "reader-portable-v1",
              let id = object["id"] as? String, ReaderBackupPolicy.validID(id), object["kind"] as? String == "restore",
              let counts = object["counts"] as? [String: NSNumber], counts.count <= 100,
              counts.allSatisfy({ $0.key.utf8.count <= 80 && $0.value.doubleValue >= 0 && $0.value.doubleValue.isFinite && $0.value.doubleValue.rounded() == $0.value.doubleValue }),
              let version = object["version"] as? Int, version >= 1, let expires = object["expiresAt"] as? String, expires.utf8.count <= 100,
              let state = object["status"] as? String, ["preparing", "ready", "restored", "failed"].contains(state) else { finish(item, code: "BACKUP_RESPONSE_UNKNOWN"); return }
        // Preparing/failed receipts may precede a validated manifest and have
        // no target owner yet. Ready previews must carry this account; an
        // explicitly different owner is never accepted in any state.
        let owner = object["targetOwnerId"] as? String
        guard (owner == nil || owner == item.context.owner), state != "ready" || owner == item.context.owner else {
            finish(item, code: "BACKUP_RESPONSE_UNKNOWN"); return
        }
        // Return the public contract only, without a provider error body or arbitrary nested data.
        var result: [String: Any] = ["id": id, "kind": "restore", "status": state, "version": version, "counts": counts, "expiresAt": expires, "protocol": "reader-portable-v1"]
        if let owner = owner { result["targetOwnerId"] = owner }
        if let type = object["objectType"] as? String, ["story", "book", "world"].contains(type) { result["objectType"] = type }
        if let code = object["errorCode"] as? String, code.utf8.count <= 100 { result["errorCode"] = code }
        // A prepared operation cannot already be restored without a separately confirmed receipt;
        // query such a durable record through the ordinary typed API instead of transporting it here.
        guard state != "restored" else { finish(item, code: "BACKUP_RESPONSE_UNKNOWN"); return }
        finish(item, result: result)
    }
}
