package app.storyreader.personal;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.pm.ApplicationInfo;
import android.os.IBinder;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.webkit.WebView;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.WebViewListener;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Set;
import org.json.JSONObject;
import org.json.JSONTokener;

/** Commands and delivery stay bound to the authenticated main-frame document. */
@CapacitorPlugin(name = "ReaderSpeech")
public class ReaderSpeechPlugin extends Plugin {
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ReaderSpeechContextPolicy policy = new ReaderSpeechContextPolicy();
    private volatile ReaderSpeechService service;
    private boolean bound, destroyed;
    private volatile long documentGeneration, dispatchGeneration;
    private volatile long serviceLease;
    private String documentNonce = "";
    private WebViewListener navigation;
    private final ArrayList<PluginCall> listeners = new ArrayList<>();
    private final ReaderSpeechService.Listener listener = this::publish;
    private final ServiceConnection connection = new ServiceConnection() {
        @Override public void onServiceConnected(ComponentName name, IBinder binder) {
            if (destroyed) return;
            service = ((ReaderSpeechService.LocalBinder) binder).service();
            if (policy.revoked()) { try { service.revokeAccount(); } catch (Exception ignored) { } }
            service.addListener(listener);
        }
        @Override public void onServiceDisconnected(ComponentName name) { service = null; serviceLease = 0; }
    };
    @Override public void load() {
        navigation = new WebViewListener() { @Override public void onPageStarted(WebView view) { resetDocument(); } };
        main.post(() -> { if (!destroyed) getBridge().addWebViewListener(navigation); });
        bound = getContext().bindService(new Intent(getContext(), ReaderSpeechService.class), connection, Context.BIND_AUTO_CREATE);
    }
    private interface DocumentWork { void run() throws Exception; }
    private interface Operation { JSObject run(ReaderSpeechService target) throws Exception; }
    private ReaderSpeechService speech() {
        ReaderSpeechService target = service == null ? ReaderSpeechService.live() : service;
        if (target == null) throw new IllegalStateException("VOICE_ENGINE_INITIALIZING");
        return target;
    }
    private void inDocument(PluginCall call, boolean captureListener, DocumentWork work) {
        long generation = dispatchGeneration; String claimed = call.getString("documentNonce");
        main.post(() -> {
            if (destroyed || generation != documentGeneration) return;
            getBridge().getWebView().evaluateJavascript("window.__readerSpeechDocumentNonce", value -> {
                if (destroyed || generation != documentGeneration) return;
                Object current;
                try { current = new JSONTokener(value).nextValue(); } catch (Exception ignored) { return; }
                String nonce = claimed;
                if (nonce == null && captureListener && current instanceof String) nonce = (String) current;
                if (!ReaderSpeechContextPolicy.sameDocument(nonce, current)) return;
                if (claimed == null) call.getData().put("documentNonce", nonce);
                documentNonce = nonce;
                try { work.run(); } catch (Exception error) { reject(call, ReaderSpeechService.safeCode(error)); }
            });
        });
    }
    private void resetDocument() {
        long generation = ++documentGeneration;
        getBridge().execute(() -> dispatchGeneration = generation);
        policy.resetDocument(); serviceLease = 0; documentNonce = ""; clearListeners();
        ReaderSpeechService target = service == null ? ReaderSpeechService.live() : service;
        // Navigation pauses and requires another handshake. Background/lockscreen
        // alone does not reset this document or erase its account's marker.
        if (target != null) target.pause("document-changed");
    }
    private JSObject envelope(PluginCall call, JSObject value) {
        String nonce = call.getString("documentNonce");
        if (!ReaderSpeechContextPolicy.validNonce(nonce)) return null;
        JSObject result = value == null ? new JSObject() : value;
        result.put("documentProtocol", ReaderSpeechContextPolicy.DOCUMENT_PROTOCOL); result.put("documentNonce", nonce);
        try { result.put("contextEpoch", ReaderSpeechContextPolicy.epoch(call.getData().opt("epoch"))); }
        catch (Exception ignored) { if (!"capabilities".equals(call.getMethodName()) && policy.currentEpoch() >= 0) result.put("contextEpoch", policy.currentEpoch()); }
        return result;
    }
    private void resolve(PluginCall call, JSObject value) { JSObject data = envelope(call, value); if (data != null) call.resolve(data); }
    private void reject(PluginCall call, String code) { JSObject data = envelope(call, null); if (data != null) call.reject(message(code), code, data); }
    private static JSObject ready(boolean value) { JSObject result = new JSObject(); result.put("ready", value); return result; }
    private boolean debug() { return (getContext().getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0; }
    private void execute(PluginCall call, Operation operation) {
        ReaderSpeechContextPolicy.Ticket captured = policy.capture(); long lease = serviceLease;
        ReaderSpeechService queuedService = service == null ? ReaderSpeechService.live() : service;
        long reset = queuedService == null ? -1 : queuedService.resetGeneration();
        inDocument(call, false, () -> {
            if (!policy.valid(captured, ReaderSpeechContextPolicy.epoch(call.getData().opt("epoch")))) throw new IllegalStateException("SPEECH_CONTEXT_CHANGED");
            ReaderSpeechService target = speech(); target.requireAccount(captured.owner, captured.origin, lease);
            target.requireResetGeneration(reset);
            resolve(call, operation.run(target));
        });
    }
    private void publish(JSObject state, String owner, String origin) {
        ReaderSpeechContextPolicy.Ticket captured = policy.capture();
        if (destroyed || captured == null || !captured.owner.equals(owner) || !captured.origin.equals(origin)) return;
        final long generation = documentGeneration, lease = serviceLease; final String nonce = documentNonce; final JSObject snapshot;
        try { snapshot = new JSObject(state.toString()); } catch (Exception ignored) { return; }
        getBridge().getWebView().evaluateJavascript("window.__readerSpeechDocumentNonce", value -> {
            if (destroyed || generation != documentGeneration || !policy.valid(captured, captured.epoch)) return;
            try {
                if (!ReaderSpeechContextPolicy.sameDocument(nonce, new JSONTokener(value).nextValue())) return;
                speech().requireAccount(captured.owner, captured.origin, lease);
                snapshot.put("documentProtocol", ReaderSpeechContextPolicy.DOCUMENT_PROTOCOL); snapshot.put("documentNonce", nonce); snapshot.put("contextEpoch", captured.epoch);
                notifyListeners("speechState", snapshot);
            } catch (Exception ignored) { /* A retired context cannot publish into its replacement. */ }
        });
    }
    @PluginMethod public void capabilities(PluginCall call) {
        inDocument(call, false, () -> { JSObject value = ready(true); value.put("contextRequired", true); resolve(call, value); });
    }
    @PluginMethod public void setContext(PluginCall call) {
        inDocument(call, false, () -> {
            boolean changed = policy.select(call.getString("owner"), call.getString("origin"), ReaderSpeechContextPolicy.epoch(call.getData().opt("epoch")), debug());
            ReaderSpeechContextPolicy.Ticket previous = policy.capture();
            if (changed) clearListeners();
            ReaderSpeechService target = speech();
            if (!changed && previous != null) target.requireAccount(previous.owner, previous.origin, serviceLease);
            serviceLease = target.bindAccount(call.getString("owner"), ReaderSpeechContextPolicy.origin(call.getString("origin"), debug()));
            policy.markReady(); resolve(call, ready(true));
        });
    }
    @PluginMethod public void revokeContext(PluginCall call) {
        inDocument(call, false, () -> {
            policy.revoke(ReaderSpeechContextPolicy.epoch(call.getData().opt("epoch"))); clearListeners(); serviceLease = 0;
            ReaderSpeechService target = service == null ? ReaderSpeechService.live() : service;
            if (target != null) target.revokeAccount(); else new ReaderSpeechStateStore(getContext()).clear();
            resolve(call, ready(false));
        });
    }
    @Override @PluginMethod(returnType = PluginMethod.RETURN_NONE) public void addListener(PluginCall call) {
        ReaderSpeechContextPolicy.Ticket captured = policy.capture(); long lease = serviceLease;
        inDocument(call, true, () -> {
            if (captured == null || !policy.valid(captured, captured.epoch)) throw new IllegalStateException("SPEECH_CONTEXT_CHANGED");
            call.getData().put("epoch", captured.epoch); speech().requireAccount(captured.owner, captured.origin, lease);
            if (!"speechState".equals(call.getString("eventName"))) throw new IllegalArgumentException("INVALID_SPEECH_EVENT");
            super.addListener(call); getBridge().saveCall(call); listeners.add(call);
        });
    }
    @Override @PluginMethod(returnType = PluginMethod.RETURN_NONE) public void removeListener(PluginCall call) {
        inDocument(call, true, () -> {
            for (int index = listeners.size() - 1; index >= 0; index--) {
                if (listeners.get(index).getCallbackId().equals(call.getString("callbackId"))) { super.removeListener(call); listeners.remove(index); break; }
            }
        });
    }
    @Override @PluginMethod public void removeAllListeners(PluginCall call) { inDocument(call, true, () -> { clearListeners(); resolve(call, null); }); }
    private void clearListeners() { super.removeAllListeners(); for (PluginCall call : listeners) getBridge().releaseCall(call); listeners.clear(); }
    private String scope(PluginCall call) {
        String value = call.getString("scopeKey");
        if (value == null || value.isEmpty() || value.length() > 512) throw new IllegalArgumentException("INVALID_SPEECH_CONTEXT");
        return value;
    }
    private static String message(String code) {
        if (code.equals("SPEECH_CONTEXT_CHANGED") || code.equals("INVALID_SPEECH_CONTEXT")) return "账号或服务器已变化，请重新打开正文后播放";
        if (code.contains("VOICE")) return "设备暂时没有可用的中文离线语音，请在系统语音设置中安装后重试";
        if (code.contains("POSITION")) return "听书位置暂时无法保存，已暂停播放；请检查本机存储后重试";
        if (code.equals("AUDIO_FOCUS_UNAVAILABLE")) return "其他应用正在使用音频，请稍后点击继续播放";
        if (code.equals("SLEEP_TIMER_EXPIRED")) return "听书定时已结束，请重新设置后继续";
        return "系统听书暂时无法继续，请重新打开正文后重试";
    }
    private static final Set<String> ANCHOR_FIELDS = new HashSet<>(Arrays.asList("bookId", "branchId", "revisionId", "chapterId", "blockId", "start", "end", "offsetUnit", "quoteHash", "worldId", "worldLineId", "checkpointId", "paragraph", "generationId", "contentId", "candidateVersion", "draft"));
    private static long anchorInteger(Object value) {
        try { return ReaderSpeechContextPolicy.epoch(value); }
        catch (IllegalArgumentException ignored) { throw new IllegalArgumentException("INVALID_SPEECH_SEGMENT"); }
    }
    static String publicAnchor(JSONObject anchor) throws Exception {
        if (!"utf16".equals(anchor.opt("offsetUnit"))) throw new IllegalArgumentException("INVALID_SPEECH_SEGMENT");
        long start = anchorInteger(anchor.opt("start")), end = anchorInteger(anchor.opt("end"));
        if (start > end) throw new IllegalArgumentException("INVALID_SPEECH_SEGMENT");
        Iterator<String> keys = anchor.keys();
        while (keys.hasNext()) {
            String key = keys.next(); Object value = anchor.opt(key);
            if (!ANCHOR_FIELDS.contains(key) || value == null || value == JSONObject.NULL) throw new IllegalArgumentException("INVALID_SPEECH_SEGMENT");
            if (Arrays.asList("start", "end", "paragraph", "candidateVersion").contains(key)) anchorInteger(value);
            else if (key.equals("draft")) { if (!(value instanceof Boolean)) throw new IllegalArgumentException("INVALID_SPEECH_SEGMENT"); }
            else if (!(value instanceof String) || ((String) value).isEmpty() || ((String) value).length() > 200 || !ReaderSpeechQueue.validUtf16((String) value)) throw new IllegalArgumentException("INVALID_SPEECH_SEGMENT");
            if (key.equals("quoteHash") && !((String) value).matches("[a-f0-9]{64}")) throw new IllegalArgumentException("INVALID_SPEECH_SEGMENT");
        }
        return anchor.toString();
    }
    @PluginMethod public void voices(PluginCall call) { execute(call, target -> { if (call.getBoolean("retry", false)) target.retryEngine(); return target.voices(); }); }
    @PluginMethod public void status(PluginCall call) { execute(call, target -> target.state(scope(call))); }
    @PluginMethod public void loadQueue(PluginCall call) {
        execute(call, target -> {
            JSArray input = call.getArray("segments"); if (input == null) throw new IllegalArgumentException("INVALID_SPEECH_QUEUE");
            if (input.length() > 20000) throw new IllegalArgumentException("SPEECH_QUEUE_TOO_LARGE");
            ArrayList<ReaderSpeechQueue.Segment> values = new ArrayList<>();
            for (int index = 0; index < input.length(); index++) {
                JSONObject item = input.getJSONObject(index);
                values.add(new ReaderSpeechQueue.Segment(item.getString("id"), item.getString("text"), item.getString("chapterId"), item.getString("chapterTitle"), publicAnchor(item.getJSONObject("anchor"))));
            }
            String context = scope(call);
            target.load(context, call.getString("title", ""), values, call.getString("fromId"), call.getDouble("rate", 1d), call.getString("voiceId", ""), call.getBoolean("stopAtChapterEnd", false), call.getInt("sleepMinutes", 0));
            return target.state(context);
        });
    }
    @PluginMethod public void play(PluginCall call) { execute(call, target -> { String context = scope(call); target.state(context); target.play(); return target.state(context); }); }
    @PluginMethod public void pause(PluginCall call) { execute(call, target -> { String context = scope(call); target.state(context); target.interruptCommands(); target.pause("user-paused"); return target.state(context); }); }
    @PluginMethod public void skip(PluginCall call) { execute(call, target -> { String context = scope(call); target.state(context); int direction = call.getInt("direction", 0); if (direction != -1 && direction != 1) throw new IllegalArgumentException("INVALID_SPEECH_DIRECTION"); target.skip(direction); return target.state(context); }); }
    @PluginMethod public void stop(PluginCall call) { execute(call, target -> { target.stop(call.getBoolean("clearPosition", false)); return new JSObject(); }); }
    @PluginMethod public void configure(PluginCall call) { execute(call, target -> { String context = scope(call); target.state(context); target.configure(call.getDouble("rate", 1d), call.getString("voiceId", ""), call.getBoolean("stopAtChapterEnd", false), call.getInt("sleepMinutes", 0)); return target.state(context); }); }
    @PluginMethod public void openSettings(PluginCall call) {
        execute(call, target -> {
            if (getActivity() == null) throw new IllegalStateException("VOICE_SETTINGS_UNAVAILABLE");
            Intent intent = new Intent("com.android.settings.TTS_SETTINGS");
            if (intent.resolveActivity(getContext().getPackageManager()) == null) intent = new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS);
            try { getActivity().startActivity(intent); JSObject result = new JSObject(); result.put("opened", true); return result; }
            catch (Exception ignored) { throw new IllegalStateException("VOICE_SETTINGS_UNAVAILABLE"); }
        });
    }
    @Override protected void handleOnDestroy() {
        destroyed = true; documentGeneration++; policy.resetDocument(); clearListeners();
        if (navigation != null) getBridge().removeWebViewListener(navigation);
        if (service != null) service.removeListener(listener);
        if (bound) getContext().unbindService(connection);
        bound = false; service = null; serviceLease = 0;
    }
}
