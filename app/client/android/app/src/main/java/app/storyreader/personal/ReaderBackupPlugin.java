package app.storyreader.personal;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.res.AssetFileDescriptor;
import android.database.Cursor;
import android.net.Uri;
import android.os.CancellationSignal;
import android.os.Handler;
import android.os.Looper;
import android.provider.OpenableColumns;
import android.util.Base64;
import android.webkit.WebView;
import androidx.activity.result.ActivityResult;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.WebViewListener;
import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.io.ByteArrayOutputStream;
import java.io.Closeable;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.PushbackInputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.nio.ByteBuffer;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.UUID;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.Future;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import org.json.JSONObject;
import org.json.JSONTokener;

/** Private backup and verified public export streams. File bytes/URIs never cross to JS. */
@CapacitorPlugin(name = "ReaderBackup")
public class ReaderBackupPlugin extends Plugin {
    private final ReaderBackupTransferPolicy policy = new ReaderBackupTransferPolicy();
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ThreadPoolExecutor io = new ThreadPoolExecutor(2, 2, 0, TimeUnit.MILLISECONDS, new ArrayBlockingQueue<>(4));
    private final ThreadPoolExecutor cleanup = new ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS, new ArrayBlockingQueue<>(4));
    private Selection selection;
    private Job active;
    // Capacitor associates results with the last activity call. Keep this slot
    // until its actual result arrives even when its scope was cancelled.
    private Job picker;
    private boolean changing, cleanupPending, destroyed;
    private volatile long documentGeneration;
    private volatile long dispatchGeneration;
    private WebViewListener navigation;
    private final ArrayList<PluginCall> progressListeners = new ArrayList<>();
    private final java.util.Set<File> instanceFiles = java.util.concurrent.ConcurrentHashMap.newKeySet();

    @Override public void load() {
        navigation = new WebViewListener() {
            @Override public void onPageStarted(WebView view) { resetDocument(); }
        };
        // Bridge.Builder installs its own listener list after plugin.load().
        // Register once construction has completed so that list cannot discard
        // our main-frame invalidation listener.
        main.post(() -> { if (!destroyed) getBridge().addWebViewListener(navigation); });
    }
    private void inDocument(PluginCall call, Runnable work) {
        inDocument(call, false, work);
    }
    private void inDocument(PluginCall call, boolean captureListenerDocument, Runnable work) {
        long generation = dispatchGeneration;
        String claimed = call.getString("documentNonce");
        main.post(() -> {
            // Callback IDs can be reused by a replacement document. Its old
            // promises no longer exist, so never deliver an old reply there.
            if (destroyed || generation != documentGeneration) return;
            getBridge().getWebView().evaluateJavascript("window.__readerBackupDocumentNonce", value -> {
                if (destroyed || generation != documentGeneration) return;
                try {
                    Object current = new JSONTokener(value).nextValue();
                    String nonce = claimed;
                    // Capacitor's builtin add/removeListener has no custom options.
                    // Capture its current document only after the native dispatch gate.
                    if (nonce == null && captureListenerDocument && current instanceof String) nonce = (String) current;
                    if (!ReaderBackupTransferPolicy.sameDocument(nonce, current)) return;
                    if (claimed == null) call.getData().put("documentNonce", nonce);
                    work.run();
                } catch (Exception ignored) { /* Invalid/obsolete document: no reply into another page. */ }
            });
        });
    }
    private void resetDocument() {
        long generation = ++documentGeneration;
        // Calls already queued on Capacitor's worker belong to the old page.
        // A worker marker separates them from calls sent by the new document;
        // capturing the main-thread generation at method entry is too late.
        getBridge().execute(() -> dispatchGeneration = generation);
        clearProgressListeners();
        policy.resetForNavigation();
        Job previous = active; active = null; selection = null;
        if (previous != null) previous.settled = true;
        cleanupContext(null, previous, policy.current().generation);
    }

    private static final class Selection {
        final String id, name; final Uri uri; final long size, generation;
        Selection(Uri uri, String name, long size, long generation) {
            this.id = uid(); this.uri = uri; this.name = name; this.size = size; this.generation = generation;
        }
    }
    private static final class Job {
        final ReaderBackupTransferPolicy.Ticket ticket; final PluginCall call;
        final CancellationSignal signal = new CancellationSignal();
        final ArrayList<Closeable> resources = new ArrayList<>();
        volatile HttpURLConnection connection;
        volatile Future<?> future;
        volatile File cipher;
        ReaderBackupTransferPolicy.ReadingExport reading;
        private byte[] secret;
        volatile boolean requestStarted;
        boolean settled;
        long lastProgress;
        Job(ReaderBackupTransferPolicy.Ticket ticket, PluginCall call) { this.ticket = ticket; this.call = call; }
        synchronized <T extends Closeable> T hold(T resource) throws IOException {
            if (resource == null) throw new ReaderBackupTransferPolicy.Failure("BACKUP_DOCUMENT_UNAVAILABLE");
            if (ticket.cancelled) { resource.close(); throw new ReaderBackupTransferPolicy.Failure("BACKUP_CONTEXT_CHANGED"); }
            resources.add(resource); return resource;
        }
        synchronized void unhold(Closeable resource) { resources.remove(resource); }
        synchronized void closeOne(Closeable resource) throws IOException {
            if (resources.remove(resource)) resource.close();
        }
        synchronized void secret(byte[] value) throws IOException {
            if (ticket.cancelled) { Arrays.fill(value, (byte) 0); throw new ReaderBackupTransferPolicy.Failure("BACKUP_CONTEXT_CHANGED"); }
            secret = value;
        }
        synchronized void clearSecret() { if (secret != null) { Arrays.fill(secret, (byte) 0); secret = null; } }
        synchronized void attach(File file) throws IOException {
            cipher = file;
            if (ticket.cancelled) { removeCipher(); throw new ReaderBackupTransferPolicy.Failure("BACKUP_CONTEXT_CHANGED"); }
        }
        synchronized FileOutputStream openCipherOutput() throws IOException {
            ticket.check();
            if (cipher == null) throw new ReaderBackupTransferPolicy.Failure("BACKUP_TEMP_UNAVAILABLE");
            // Opening creates the file if cancellation has deleted it. Keep
            // open and registration under the same monitor as cipher cleanup.
            FileOutputStream target = new FileOutputStream(cipher);
            resources.add(target); return target;
        }
        synchronized void closeResources() throws IOException {
            IOException failed = null;
            for (int index = resources.size() - 1; index >= 0; index--) {
                try { resources.get(index).close(); } catch (IOException error) { failed = error; }
            }
            resources.clear(); if (failed != null) throw failed;
        }
        synchronized void removeCipher() throws IOException {
            if (cipher != null) { deleteCipher(cipher); cipher = null; }
        }
        void cancelIo() throws IOException {
            clearSecret();
            signal.cancel(); if (future != null) future.cancel(true);
            if (connection != null) connection.disconnect();
            IOException failed = null;
            try { closeResources(); } catch (IOException error) { failed = error; }
            try { removeCipher(); } catch (IOException error) { failed = error; }
            if (failed != null) throw failed;
        }
    }
    private static String uid() { return UUID.randomUUID().toString().replace("-", ""); }
    private static JSObject ready() { JSObject result = new JSObject(); result.put("ready", true); return result; }
    private long epoch(PluginCall call) throws ReaderBackupTransferPolicy.Failure {
        Object raw = call.getData().opt("epoch");
        if (!(raw instanceof Number)) throw new ReaderBackupTransferPolicy.Failure("BACKUP_CONTEXT_INVALID");
        double number = ((Number) raw).doubleValue();
        if (!Double.isFinite(number) || number < 0 || number > ReaderBackupTransferPolicy.MAX_EPOCH || Math.rint(number) != number) throw new ReaderBackupTransferPolicy.Failure("BACKUP_CONTEXT_INVALID");
        return (long) number;
    }
    private boolean debug() { return (getContext().getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0; }
    private Job begin(PluginCall call, long epoch) throws IOException {
        if (destroyed || changing || cleanupPending || picker != null) throw new ReaderBackupTransferPolicy.Failure("BACKUP_TRANSFER_BUSY");
        Job job = new Job(policy.begin(epoch), call); active = job; return job;
    }
    private void submit(Job job, Runnable work) {
        try { job.future = io.submit(work); }
        catch (RuntimeException error) { job.clearSecret(); finish(job, null, "BACKUP_TRANSFER_BUSY", 0); }
    }
    private boolean current(Job job) { return !destroyed && policy.valid(job.ticket) && active == job; }
    private static String safeCode(Exception error, boolean unknownPost) {
        if (error instanceof ReaderBackupTransferPolicy.Failure) return ((ReaderBackupTransferPolicy.Failure) error).code;
        return unknownPost ? "BACKUP_RESPONSE_UNKNOWN" : "BACKUP_TRANSFER_FAILED";
    }
    private static String message(String code) {
        if ("BACKUP_RESPONSE_UNKNOWN".equals(code)) return "备份请求结果尚未确认，请查询已有请求，不要重复发送";
        if ("BACKUP_CANCELLED".equals(code)) return "已取消备份操作";
        if ("BACKUP_CONTEXT_CHANGED".equals(code)) return "账号或服务已变化，请重新选择备份";
        if ("BACKUP_TRANSFER_BUSY".equals(code)) return "请先完成或取消当前文件操作";
        if ("BACKUP_SAVE_FAILED".equals(code)) return "保存未完成，请检查所选位置；可能已有部分文件";
        return "备份操作未完成，请保留原文件并重试";
    }
    private static JSObject replyData(PluginCall call, JSObject value) {
        String nonce = call.getString("documentNonce");
        if (nonce == null || !nonce.matches("[a-f0-9]{32}")) return null;
        JSObject data = value == null ? new JSObject() : value;
        data.put("documentProtocol", ReaderBackupTransferPolicy.DOCUMENT_PROTOCOL); data.put("documentNonce", nonce); return data;
    }
    private void resolve(PluginCall call, JSObject value) {
        JSObject data = replyData(call, value); if (data != null) call.resolve(data);
    }
    private void reject(PluginCall call, String code) {
        if (call != null) { JSObject data = replyData(call, null); if (data != null) call.reject(message(code), code, data); }
    }
    private void finish(Job job, JSObject result, String code, int status) {
        main.post(() -> {
            if (job.settled) return;
            job.settled = true;
            String outcome = current(job) ? code : "BACKUP_CONTEXT_CHANGED";
            if ("BACKUP_TEMP_CLEANUP_FAILED".equals(code)) cleanupPending = true;
            policy.end(job.ticket); if (active == job) active = null;
            if (outcome != null) {
                JSObject details = new JSObject(); if (status > 0) details.put("status", status);
                JSObject data = replyData(job.call, details); if (data != null) job.call.reject(message(outcome), outcome, data);
            } else resolve(job.call, result);
        });
    }
    private void progress(Job job, String phase, long bytes, long total) throws IOException {
        job.ticket.check(); long now = android.os.SystemClock.elapsedRealtime();
        if (bytes != total && now - job.lastProgress < 150) return;
        job.lastProgress = now;
        main.post(() -> {
            if (!current(job)) return;
            JSObject data = new JSObject(); data.put("phase", phase); data.put("bytes", bytes);
            if (total >= 0) data.put("totalBytes", total);
            JSObject signed = replyData(job.call, data); if (signed != null) notifyListeners("backupProgress", signed);
        });
    }
    @PluginMethod public void getCapabilities(PluginCall call) {
        inDocument(call, () -> {
            JSObject value = ready(); value.put("streaming", true); value.put("maxFileBytes", ReaderBackupTransferPolicy.MAX_FILE_BYTES);
            value.put("readingExport", true); value.put("maxReadingBytes", ReaderBackupTransferPolicy.MAX_READING_BYTES); resolve(call, value);
        });
    }
    @Override @PluginMethod(returnType = PluginMethod.RETURN_NONE) public void addListener(PluginCall call) {
        inDocument(call, true, () -> {
            if (!"backupProgress".equals(call.getString("eventName"))) { reject(call, "BACKUP_REQUEST_INVALID"); return; }
            super.addListener(call); getBridge().saveCall(call); progressListeners.add(call);
        });
    }
    @Override @PluginMethod(returnType = PluginMethod.RETURN_NONE) public void removeListener(PluginCall call) {
        inDocument(call, true, () -> {
            String id = call.getString("callbackId");
            for (int i = progressListeners.size() - 1; i >= 0; i--) {
                PluginCall listener = progressListeners.get(i);
                if (listener.getCallbackId().equals(id)) { progressListeners.remove(i); super.removeListener(call); break; }
            }
        });
    }
    private void clearProgressListeners() {
        super.removeAllListeners();
        for (PluginCall listener : progressListeners) getBridge().releaseCall(listener);
        progressListeners.clear();
    }
    @Override @PluginMethod public void removeAllListeners(PluginCall call) {
        inDocument(call, true, () -> { clearProgressListeners(); resolve(call, null); });
    }
    @PluginMethod public void setContext(PluginCall call) {
        inDocument(call, () -> {
            try {
                boolean changed = policy.setContext(call.getString("backend"), call.getString("ownerId"), epoch(call), debug());
                if (!changed) {
                    if (cleanupPending && !changing) cleanupContext(call, null, policy.current().generation);
                    else resolve(call, ready());
                    return;
                }
                Job previous = active; active = null; selection = null; changing = true;
                if (previous != null && !previous.settled) { previous.settled = true; reject(previous.call, "BACKUP_CONTEXT_CHANGED"); }
                cleanupContext(call, previous, policy.current().generation);
            } catch (Exception error) { reject(call, safeCode(error, false)); }
        });
    }
    private void cleanupContext(PluginCall call, Job previous, long generation) {
        changing = true; cleanupPending = true;
        long document = documentGeneration;
        try {
            cleanup.execute(() -> {
                String failure = null;
                try { if (previous != null) previous.cancelIo(); } catch (Exception error) { failure = "BACKUP_TEMP_CLEANUP_FAILED"; }
                try { clearOwnCipherFiles(); } catch (Exception error) { failure = "BACKUP_TEMP_CLEANUP_FAILED"; }
                String code = failure;
                main.post(() -> {
                    if (document != documentGeneration) return;
                    if (destroyed || policy.current().generation != generation) { reject(call, "BACKUP_CONTEXT_CHANGED"); return; }
                    changing = false; cleanupPending = code != null;
                    if (code == null) { if (call != null) resolve(call, ready()); } else reject(call, code);
                });
            });
        } catch (RuntimeException error) {
            changing = false; reject(call, "BACKUP_TEMP_CLEANUP_FAILED");
        }
    }
    @PluginMethod public void cancel(PluginCall call) {
        inDocument(call, () -> {
            try {
                long generation = policy.require(epoch(call)).generation; Job job = active; active = null; selection = null;
                policy.cancel(job == null ? null : job.ticket);
                if (job != null && !job.settled) { job.settled = true; reject(job.call, "BACKUP_CANCELLED"); }
                long document = documentGeneration;
                cleanup.execute(() -> {
                    String code = null; try { if (job != null) job.cancelIo(); } catch (Exception error) { code = "BACKUP_TEMP_CLEANUP_FAILED"; }
                    String failure = code; main.post(() -> {
                        if (document != documentGeneration) return;
                        if (destroyed || policy.current().generation != generation) { reject(call, "BACKUP_CONTEXT_CHANGED"); return; }
                        if (failure == null) resolve(call, null); else { cleanupPending = true; reject(call, failure); }
                    });
                });
            } catch (Exception error) { reject(call, safeCode(error, false)); }
        });
    }
    @PluginMethod public void pickBackup(PluginCall call) {
        inDocument(call, () -> {
            Job job = null;
            try {
                job = begin(call, policy.current().epoch); selection = null;
                Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*");
                intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                picker = job; startActivityForResult(call, intent, "backupPicked");
            } catch (Exception error) {
                if (picker == job) picker = null;
                if (job == null) reject(call, safeCode(error, false)); else finish(job, null, "BACKUP_PICKER_UNAVAILABLE", 0);
            }
        });
    }
    @ActivityCallback private void backupPicked(PluginCall call, ActivityResult result) {
        Job job = picker; picker = null;
        if (job == null) return;
        if (call == null || !job.call.getCallbackId().equals(call.getCallbackId())) { finish(job, null, "BACKUP_PICKER_UNAVAILABLE", 0); return; }
        if (!current(job)) { finish(job, null, "BACKUP_CONTEXT_CHANGED", 0); return; }
        Intent data = result.getData();
        if (result.getResultCode() != Activity.RESULT_OK || data == null || data.getData() == null) { finish(job, null, "BACKUP_CANCELLED", 0); return; }
        Uri uri = data.getData();
        if (!"content".equals(uri.getScheme())) { finish(job, null, "BACKUP_DOCUMENT_UNAVAILABLE", 0); return; }
        submit(job, () -> {
            try {
                String name = "Private backup.srbackup"; long size = -1;
                try (Cursor cursor = getContext().getContentResolver().query(uri, new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE}, null, null, null, job.signal)) {
                    if (cursor != null && cursor.moveToFirst()) {
                        int n = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME), s = cursor.getColumnIndex(OpenableColumns.SIZE);
                        if (n >= 0 && !cursor.isNull(n)) name = displayName(cursor.getString(n));
                        if (s >= 0 && !cursor.isNull(s)) size = cursor.getLong(s);
                    }
                }
                if (size < -1 || size > ReaderBackupTransferPolicy.MAX_FILE_BYTES || (size >= 0 && size < 74)) throw new ReaderBackupTransferPolicy.Failure("BACKUP_SIZE_LIMIT");
                Selection picked = new Selection(uri, name, size, job.ticket.context.generation);
                main.post(() -> {
                    if (!current(job)) { finish(job, null, "BACKUP_CONTEXT_CHANGED", 0); return; }
                    selection = picked;
                    JSObject value = new JSObject(); value.put("selectionId", picked.id); value.put("size", picked.size < 0 ? JSONObject.NULL : picked.size); value.put("displayName", picked.name);
                    finish(job, value, null, 0);
                });
            } catch (Exception error) { finish(job, null, safeCode(error, false), 0); }
        });
    }
    private static String displayName(String raw) {
        if (raw == null) return "Private backup.srbackup";
        String value = raw.replace('\\', '/'); value = value.substring(value.lastIndexOf('/') + 1).replaceAll("[\\p{Cntrl}]", "");
        if (value.isEmpty()) return "Private backup.srbackup";
        return value.length() > 255 ? value.substring(0, 255) : value;
    }
    @PluginMethod public void prepareBackup(PluginCall call) {
        String password = call.getString("password"), token = call.getString("accessToken");
        call.getData().remove("password"); call.getData().remove("accessToken");
        inDocument(call, () -> {
            Job job = null; byte[] secret = null;
            try {
                long epoch = epoch(call); ReaderBackupTransferPolicy.Context context = policy.require(epoch);
                String selectedId = call.getString("selectionId"), requestId = ReaderBackupTransferPolicy.requestId(call.getString("requestId"));
                String bearer = bearerForContext(token, context.ownerId); secret = ReaderBackupTransferPolicy.password(password);
                if (selection == null || !selection.id.equals(selectedId) || selection.generation != policy.current().generation) throw new ReaderBackupTransferPolicy.Failure("BACKUP_SELECTION_EXPIRED");
                Selection source = selection; job = begin(call, epoch); job.secret(secret); Job transfer = job; byte[] passwordBytes = secret;
                submit(transfer, () -> upload(transfer, source, passwordBytes, bearer, requestId));
            } catch (Exception error) {
                if (secret != null) Arrays.fill(secret, (byte) 0);
                if (job == null) reject(call, safeCode(error, false)); else finish(job, null, safeCode(error, false), 0);
            }
        });
    }
    // A bounded local guard against accidentally handing this context an old
    // account's token. The backend still verifies the JWT signature/session.
    private static String bearerForContext(String token, String owner) throws ReaderBackupTransferPolicy.Failure {
        ReaderBackupTransferPolicy.accessToken(token); byte[] payload = null;
        try {
            String[] parts = token.split("\\.", -1);
            if (parts.length != 3 || !parts[1].matches("[A-Za-z0-9_-]{1,2048}")) throw new Exception();
            payload = Base64.decode(parts[1], Base64.URL_SAFE | Base64.NO_WRAP);
            String json = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(payload)).toString();
            if (!owner.equals(new JSONObject(json).optString("sub", ""))) throw new Exception();
            return token;
        } catch (Exception error) { throw new ReaderBackupTransferPolicy.Failure("BACKUP_AUTH_REQUIRED"); }
        finally { if (payload != null) Arrays.fill(payload, (byte) 0); }
    }
    private HttpURLConnection connection(Job job, String suffix, String method, String token) throws IOException {
        return connection(job, ReaderBackupTransferPolicy.endpoint(job.ticket.context, suffix), method, token);
    }
    private HttpURLConnection connection(Job job, URI endpoint, String method, String token) throws IOException {
        job.ticket.check();
        HttpURLConnection value = (HttpURLConnection) endpoint.toURL().openConnection();
        value.setInstanceFollowRedirects(false); value.setUseCaches(false); value.setConnectTimeout(15000); value.setReadTimeout(60000);
        value.setRequestMethod(method); value.setRequestProperty("Authorization", "Bearer " + token); value.setRequestProperty("Accept-Encoding", "identity");
        job.connection = value; job.ticket.check(); return value;
    }
    private void upload(Job job, Selection selected, byte[] password, String token, String requestId) {
        JSObject result = null; String failure = null; int status = 0;
        try {
            AssetFileDescriptor descriptor = job.hold(getContext().getContentResolver().openAssetFileDescriptor(selected.uri, "r", job.signal));
            PushbackInputStream source = job.hold(new PushbackInputStream(descriptor.createInputStream(), ReaderBackupTransferPolicy.HEADER_BYTES));
            job.unhold(descriptor); // createInputStream transfers descriptor ownership to its auto-close stream.
            byte[] raw = ReaderMultipartStream.readHeader(source, job.ticket::check);
            ReaderBackupTransferPolicy.Header header = ReaderBackupTransferPolicy.inspectHeader(raw, selected.size); source.unread(raw);
            String boundary = "readerbackup" + uid();
            try (ReaderMultipartStream body = new ReaderMultipartStream(boundary, password, header.totalBytes)) {
                job.clearSecret();
                HttpURLConnection connection = connection(job, "prepare", "POST", token);
                connection.setDoOutput(true); connection.setFixedLengthStreamingMode(body.contentLength());
                connection.setRequestProperty("Idempotency-Key", requestId); connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
                job.requestStarted = true;
                OutputStream target = job.hold(connection.getOutputStream());
                body.writeTo(source, target, job.ticket::check, count -> progress(job, "upload", count, header.totalBytes));
                job.closeResources();
                status = connection.getResponseCode();
                if (status >= 300 && status < 400) throw new ReaderBackupTransferPolicy.Failure("BACKUP_REDIRECT_REFUSED");
                JSObject response = response(connection, status, job);
                if (status >= 200 && status < 300) {
                    result = selectedResult(response, job.ticket.context.ownerId);
                } else failure = serverCode(response);
            }
        } catch (Exception error) { failure = safeCode(error, job.requestStarted); }
        finally {
            Arrays.fill(password, (byte) 0);
            job.clearSecret();
            try { job.closeResources(); } catch (Exception error) { result = null; failure = job.requestStarted ? "BACKUP_RESPONSE_UNKNOWN" : "BACKUP_TRANSFER_FAILED"; }
            if (job.connection != null) job.connection.disconnect();
        }
        finish(job, result, failure, status);
    }
    private static JSObject selectedResult(JSObject value, String owner) throws ReaderBackupTransferPolicy.Failure {
        String id = value.optString("id", ""), status = value.optString("status", ""); Object version = value.opt("version");
        Object target = value.opt("targetOwnerId"), expires = value.opt("expiresAt"); JSONObject counts = value.optJSONObject("counts");
        if (!id.matches("[a-f0-9]{32}") || !"restore".equals(value.opt("kind")) || !"reader-portable-v1".equals(value.opt("protocol"))
            || !(version instanceof Number) || ((Number) version).doubleValue() <= 0 || ((Number) version).doubleValue() > ReaderBackupTransferPolicy.MAX_EPOCH
            || Math.rint(((Number) version).doubleValue()) != ((Number) version).doubleValue() || !status.matches("preparing|ready|failed")
            || (target != null && target != JSONObject.NULL && !owner.equals(target)) || ("ready".equals(status) && !owner.equals(target))
            || !(expires instanceof String) || ((String) expires).isEmpty() || ((String) expires).length() > 100 || counts == null || counts.length() > 128)
            throw new ReaderBackupTransferPolicy.Failure("BACKUP_RESPONSE_UNKNOWN");
        JSObject safeCounts = new JSObject(); java.util.Iterator<String> keys = counts.keys();
        while (keys.hasNext()) {
            String key = keys.next(); Object count = counts.opt(key);
            if (!key.matches("[A-Za-z0-9_]{1,80}") || !(count instanceof Number) || !Double.isFinite(((Number) count).doubleValue())
                || ((Number) count).doubleValue() < 0 || ((Number) count).doubleValue() > ReaderBackupTransferPolicy.MAX_EPOCH
                || Math.rint(((Number) count).doubleValue()) != ((Number) count).doubleValue()) throw new ReaderBackupTransferPolicy.Failure("BACKUP_RESPONSE_UNKNOWN");
            safeCounts.put(key, count);
        }
        JSObject result = new JSObject(); result.put("id", id); result.put("kind", "restore"); result.put("status", status);
        result.put("version", version); result.put("counts", safeCounts); result.put("expiresAt", expires); result.put("protocol", "reader-portable-v1");
        if (owner.equals(target)) result.put("targetOwnerId", owner);
        Object type = value.opt("objectType"); if (type instanceof String && ((String) type).matches("story|book|world")) result.put("objectType", type);
        Object error = value.opt("errorCode"); if (error instanceof String && ((String) error).matches("[A-Z][A-Z0-9_]{1,99}")) result.put("errorCode", error);
        return result;
    }
    private static String serverCode(JSObject response) {
        String value = response.optString("code", ""); return value.matches("[A-Z][A-Z0-9_]{1,100}") ? value : "BACKUP_HTTP_ERROR";
    }
    private JSObject response(HttpURLConnection connection, int status, Job job) throws Exception {
        InputStream stream = status >= 200 && status < 300 ? connection.getInputStream() : connection.getErrorStream();
        if (stream == null) throw new ReaderBackupTransferPolicy.Failure("BACKUP_RESPONSE_UNKNOWN");
        stream = job.hold(stream); ByteArrayOutputStream value = new ByteArrayOutputStream(); byte[] bytes = new byte[ReaderBackupTransferPolicy.CHUNK_BYTES];
        try {
            int count;
            while (true) {
                job.ticket.check(); count = stream.read(bytes);
                if (count < 0) break;
                if (count == 0) { int one = stream.read(); if (one < 0) break; bytes[0] = (byte) one; count = 1; }
                if (value.size() + count > ReaderBackupTransferPolicy.MAX_RESPONSE_BYTES) throw new ReaderBackupTransferPolicy.Failure("BACKUP_RESPONSE_UNKNOWN");
                value.write(bytes, 0, count);
            }
            job.closeOne(stream);
        } finally { Arrays.fill(bytes, (byte) 0); }
        String json = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(value.toByteArray())).toString();
        return new JSObject(json);
    }
    @PluginMethod public void downloadBackup(PluginCall call) {
        String token = call.getString("accessToken"); call.getData().remove("accessToken");
        inDocument(call, () -> {
            Job job = null;
            try {
                ReaderBackupTransferPolicy.Context context = policy.require(epoch(call));
                String id = call.getString("operationId"), bearer = bearerForContext(token, context.ownerId);
                if (id == null || !id.matches("[a-f0-9]{32}")) throw new ReaderBackupTransferPolicy.Failure("BACKUP_OPERATION_INVALID");
                job = begin(call, epoch(call)); Job transfer = job; submit(transfer, () -> download(transfer, id, bearer));
            } catch (Exception error) { if (job == null) reject(call, safeCode(error, false)); else finish(job, null, safeCode(error, false), 0); }
        });
    }
    @PluginMethod public void downloadReadingExport(PluginCall call) {
        String token = call.getString("accessToken"); call.getData().remove("accessToken");
        inDocument(call, () -> {
            Job job = null;
            try {
                ReaderBackupTransferPolicy.Context context = policy.require(epoch(call));
                ReaderBackupTransferPolicy.ReadingExport metadata = ReaderBackupTransferPolicy.readingExport(call.getString("operationId"), call.getString("format"), call.getData().opt("size"), call.getString("sha256"), call.getString("fileName"));
                String bearer = bearerForContext(token, context.ownerId);
                job = begin(call, epoch(call)); job.reading = metadata; Job transfer = job;
                submit(transfer, () -> downloadReading(transfer, bearer));
            } catch (Exception error) { if (job == null) reject(call, safeCode(error, false)); else finish(job, null, safeCode(error, false), 0); }
        });
    }
    private void downloadReading(Job job, String token) {
        String failure = null; int status = 0; boolean handoff = false;
        ReaderBackupTransferPolicy.ReadingExport metadata = job.reading;
        try {
            HttpURLConnection connection = connection(job, ReaderBackupTransferPolicy.readingEndpoint(job.ticket.context, metadata.operationId), "GET", token);
            status = connection.getResponseCode();
            if (status >= 300 && status < 400) throw new ReaderBackupTransferPolicy.Failure("BACKUP_REDIRECT_REFUSED");
            if (status < 200 || status >= 300) throw new ReaderBackupTransferPolicy.Failure(serverCode(response(connection, status, job)));
            metadata.checkResponse(connection.getContentType(), connection.getContentEncoding(), connection.getContentLengthLong());
            InputStream source = job.hold(connection.getInputStream()); File file = newReadingFile(); job.attach(file);
            FileOutputStream target = job.openCipherOutput();
            ReaderMultipartStream.copyVerified(source, target, metadata.size, metadata.sha256, job.ticket::check, bytes -> progress(job, "download", bytes, metadata.size));
            target.flush(); target.getFD().sync(); job.closeResources(); job.ticket.check(); handoff = true;
        } catch (Exception error) { failure = safeCode(error, false); }
        finally {
            try { job.closeResources(); } catch (Exception error) { handoff = false; failure = "BACKUP_TRANSFER_FAILED"; }
            if (job.connection != null) job.connection.disconnect();
            if (!handoff) { try { job.removeCipher(); } catch (Exception error) { failure = "BACKUP_TEMP_CLEANUP_FAILED"; } }
        }
        if (handoff) main.post(() -> savePicker(job, metadata.operationId)); else finish(job, null, failure, status);
    }
    private void download(Job job, String operationId, String token) {
        String failure = null; int status = 0; boolean handoff = false;
        try {
            HttpURLConnection connection = connection(job, operationId + "/download", "GET", token); status = connection.getResponseCode();
            if (status >= 300 && status < 400) throw new ReaderBackupTransferPolicy.Failure("BACKUP_REDIRECT_REFUSED");
            if (status < 200 || status >= 300) throw new ReaderBackupTransferPolicy.Failure(serverCode(response(connection, status, job)));
            String encoding = connection.getContentEncoding();
            if (encoding != null && !"identity".equalsIgnoreCase(encoding)) throw new ReaderBackupTransferPolicy.Failure("BACKUP_FORMAT_INVALID");
            InputStream source = job.hold(connection.getInputStream());
            byte[] raw = ReaderMultipartStream.readHeader(source, job.ticket::check);
            ReaderBackupTransferPolicy.Header header = ReaderBackupTransferPolicy.inspectHeader(raw, connection.getContentLengthLong());
            File file = newCipherFile(); job.attach(file);
            FileOutputStream target = job.openCipherOutput(); target.write(raw);
            ReaderMultipartStream.copyExact(source, target, header.totalBytes - raw.length, job.ticket::check, count -> progress(job, "download", count + raw.length, header.totalBytes));
            target.flush(); target.getFD().sync(); job.closeResources();
            job.ticket.check(); handoff = true;
        } catch (Exception error) { failure = safeCode(error, false); }
        finally {
            try { job.closeResources(); } catch (Exception error) { handoff = false; failure = "BACKUP_TRANSFER_FAILED"; }
            if (job.connection != null) job.connection.disconnect();
            if (!handoff) { try { job.removeCipher(); } catch (Exception error) { failure = "BACKUP_TEMP_CLEANUP_FAILED"; } }
        }
        if (handoff) main.post(() -> savePicker(job, operationId));
        else finish(job, null, failure, status);
    }
    private void savePicker(Job job, String operationId) {
        if (!current(job)) { cleanupJob(job, "BACKUP_CONTEXT_CHANGED"); return; }
        try {
            if (picker != null) throw new ReaderBackupTransferPolicy.Failure("BACKUP_TRANSFER_BUSY");
            Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType(job.reading == null ? "application/octet-stream" : job.reading.mime);
            intent.putExtra(Intent.EXTRA_TITLE, job.reading == null ? "story-" + operationId + ".srbackup" : job.reading.fileName); intent.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
            picker = job; startActivityForResult(job.call, intent, "backupSaved");
        } catch (Exception error) { if (picker == job) picker = null; cleanupJob(job, "BACKUP_PICKER_UNAVAILABLE"); }
    }
    @ActivityCallback private void backupSaved(PluginCall call, ActivityResult result) {
        Job job = picker; picker = null;
        if (job == null) return;
        if (call == null || !job.call.getCallbackId().equals(call.getCallbackId())) { cleanupJob(job, "BACKUP_PICKER_UNAVAILABLE"); return; }
        if (!current(job)) { cleanupJob(job, "BACKUP_CONTEXT_CHANGED"); return; }
        Intent data = result.getData();
        if (result.getResultCode() != Activity.RESULT_OK || data == null || data.getData() == null) { cleanupJob(job, "BACKUP_CANCELLED"); return; }
        Uri destination = data.getData();
        if (!"content".equals(destination.getScheme())) { cleanupJob(job, "BACKUP_DOCUMENT_UNAVAILABLE"); return; }
        submit(job, () -> {
            String failure = null;
            try {
                job.ticket.check(); File file = job.cipher;
                if (file == null) throw new ReaderBackupTransferPolicy.Failure("BACKUP_SAVE_FAILED");
                FileInputStream source = job.hold(new FileInputStream(file));
                OutputStream target = job.hold(getContext().getContentResolver().openOutputStream(destination, "w"));
                if (job.reading == null) ReaderMultipartStream.copyExact(source, target, file.length(), job.ticket::check, bytes -> {});
                else ReaderMultipartStream.copyVerified(source, target, job.reading.size, job.reading.sha256, job.ticket::check, bytes -> {});
                target.flush(); job.closeResources();
            } catch (Exception error) { failure = "BACKUP_SAVE_FAILED"; }
            finally {
                try { job.closeResources(); } catch (Exception error) { failure = "BACKUP_SAVE_FAILED"; }
                try { job.removeCipher(); } catch (Exception error) { failure = "BACKUP_TEMP_CLEANUP_FAILED"; }
            }
            JSObject saved = new JSObject(); saved.put("saved", true); finish(job, saved, failure, 0);
        });
    }
    private void cleanupJob(Job job, String code) {
        try {
            cleanup.execute(() -> {
                String failure = code;
                try { job.closeResources(); } catch (Exception error) { failure = "BACKUP_TEMP_CLEANUP_FAILED"; }
                try { job.removeCipher(); } catch (Exception error) { failure = "BACKUP_TEMP_CLEANUP_FAILED"; }
                finish(job, null, failure, 0);
            });
        } catch (RuntimeException error) { cleanupPending = true; finish(job, null, "BACKUP_TEMP_CLEANUP_FAILED", 0); }
    }
    private File cipherDirectory() throws IOException {
        return privateDirectory("reader-backup-private");
    }
    private File privateDirectory(String name) throws IOException {
        File cache = getContext().getCacheDir().getCanonicalFile(); File directory = new File(cache, name);
        if (!directory.getCanonicalFile().equals(directory.getAbsoluteFile())) throw new ReaderBackupTransferPolicy.Failure("BACKUP_TEMP_UNAVAILABLE");
        if (!directory.isDirectory() && !directory.mkdir()) throw new ReaderBackupTransferPolicy.Failure("BACKUP_TEMP_UNAVAILABLE");
        return directory;
    }
    private File newReadingFile() throws IOException {
        return newOwnFile(privateDirectory("reader-export-private"), "reader-export-" + uid() + ".public.part");
    }
    private File newCipherFile() throws IOException {
        return newOwnFile(cipherDirectory(), "reader-backup-" + uid() + ".cipher.part");
    }
    private File newOwnFile(File parent, String name) throws IOException {
        File file = new File(parent, name);
        if (!file.createNewFile()) throw new ReaderBackupTransferPolicy.Failure("BACKUP_TEMP_UNAVAILABLE");
        instanceFiles.add(file);
        file.setReadable(false, false); file.setWritable(false, false); file.setReadable(true, true); file.setWritable(true, true); return file;
    }
    private static void deleteCipher(File file) throws IOException {
        File parent = file.getParentFile();
        boolean owned = parent != null && (("reader-backup-private".equals(parent.getName()) && file.getName().matches("reader-backup-[a-f0-9]{32}\\.cipher\\.part"))
            || ("reader-export-private".equals(parent.getName()) && file.getName().matches("reader-export-[a-f0-9]{32}\\.public\\.part")));
        if (!owned || !file.getCanonicalFile().getParentFile().equals(parent.getCanonicalFile()) || !file.getCanonicalFile().equals(file.getAbsoluteFile())) throw new ReaderBackupTransferPolicy.Failure("BACKUP_TEMP_UNAVAILABLE");
        if (file.exists() && !file.delete()) throw new ReaderBackupTransferPolicy.Failure("BACKUP_TEMP_CLEANUP_FAILED");
    }
    private void clearOwnCipherFiles() throws IOException {
        IOException failed = null;
        for (String name : new String[]{"reader-backup-private", "reader-export-private"}) {
            try {
                File[] files = privateDirectory(name).listFiles(); if (files == null) throw new ReaderBackupTransferPolicy.Failure("BACKUP_TEMP_UNAVAILABLE");
                for (File file : files) if (file.getName().matches("reader-backup-[a-f0-9]{32}\\.cipher\\.part|reader-export-[a-f0-9]{32}\\.public\\.part")) {
                    try { deleteCipher(file); } catch (IOException error) { failed = error; }
                }
            } catch (IOException error) { failed = error; }
        }
        if (failed != null) throw failed;
    }
    @Override protected void handleOnDestroy() {
        if (navigation != null) { getBridge().removeWebViewListener(navigation); navigation = null; }
        documentGeneration++;
        clearProgressListeners();
        destroyed = true; selection = null; Job job = active; active = null; policy.cancel(job == null ? null : job.ticket);
        if (job != null && !job.settled) { job.settled = true; reject(job.call, "BACKUP_CONTEXT_CHANGED"); }
        try { cleanup.execute(() -> {
            try { if (job != null) job.cancelIo(); } catch (Exception ignored) {}
            // A replacement Activity may already be using the same cache root.
            // Delayed destruction owns this instance's generated paths only.
            for (File file : instanceFiles) { try { deleteCipher(file); } catch (Exception ignored) {} }
            instanceFiles.clear();
        }); }
        catch (RuntimeException ignored) { if (job != null) job.clearSecret(); }
        io.shutdown(); cleanup.shutdown();
    }
}
