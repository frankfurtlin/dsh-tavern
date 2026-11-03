import Foundation
import Security

/// System Keychain encryption, available during locked playback after first unlock.
final class ReaderSpeechStateStore {
    private let service = "app.storyreader.personal.system-speech-marker.v1"
    private let account = "completed-paragraph"
    private var query: [String: Any] { [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account] }

    func save(_ marker: [String: Any]) throws {
        guard ReaderSpeechContextGate.validMarker(marker) else { throw ReaderSpeechFailure.code("SPEECH_POSITION_SAVE_FAILED") }
        let data = try JSONSerialization.data(withJSONObject: marker)
        guard data.count <= 50000 else { throw ReaderSpeechFailure.code("SPEECH_POSITION_SAVE_FAILED") }
        let update = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if update == errSecSuccess { return }
        guard update == errSecItemNotFound else { throw ReaderSpeechFailure.code("SPEECH_POSITION_SAVE_FAILED") }
        var item = query; item[kSecValueData as String] = data
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else { throw ReaderSpeechFailure.code("SPEECH_POSITION_SAVE_FAILED") }
    }
    private func marker() throws -> [String: Any]? {
        var request = query; request[kSecReturnData as String] = true; request[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else { throw ReaderSpeechFailure.code("SPEECH_POSITION_READ_FAILED") }
        guard data.count <= 50000,
              let marker = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              ReaderSpeechContextGate.validMarker(marker) else { try clear(); return nil }
        return marker
    }
    func authorize(owner: String, origin: String) throws {
        let stored = try marker()
        if stored != nil && !ReaderSpeechContextGate.markerMatches(stored, owner: owner, origin: origin) { try clear() }
    }
    func read(scope: String) throws -> [String: Any]? {
        guard let stored = try marker() else { return nil }
        return stored["scopeKey"] as? String == scope ? stored : nil
    }
    func clear() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw ReaderSpeechFailure.code("SPEECH_POSITION_CLEAR_FAILED") }
    }
}
