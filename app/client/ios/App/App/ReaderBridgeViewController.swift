import Capacitor

@objc(ReaderBridgeViewController)
final class ReaderBridgeViewController: CAPBridgeViewController {
    override func capacitorDidLoad() {
        bridge?.registerPluginInstance(ReaderSpeechPlugin())
        bridge?.registerPluginInstance(ReaderBackupPlugin())
    }
}
