package app.storyreader.personal;

import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import org.json.JSONObject;

/** No prose is persisted here. The encrypted marker is scoped to the owner/context. */
final class ReaderSpeechStateStore {
    private final Context context;
    ReaderSpeechStateStore(Context context) { this.context = context.getApplicationContext(); }
    private String alias() { return context.getPackageName() + ".speech-marker.v1"; }
    private SecretKey key(boolean create) throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore"); store.load(null);
        if (store.containsAlias(alias())) return (SecretKey) store.getKey(alias(), null);
        if (!create) throw new IllegalStateException("SPEECH_POSITION_KEY_UNAVAILABLE");
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(alias(), KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
            .setKeySize(256).setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setRandomizedEncryptionRequired(true).build());
        return generator.generateKey();
    }
    private static final Set<String> PUBLIC_FIELDS = new HashSet<>(Arrays.asList("scopeKey", "title", "status", "reason", "index", "count", "eventVersion", "rate", "voiceId", "resolvedVoiceId", "voiceFallback", "stopAtChapterEnd", "queueLoaded", "sleepRemainingMs", "currentId", "chapterId", "chapterTitle", "currentAnchor", "lastCompletedId", "lastCompletedAnchor"));
    private JSONObject publicMarker(JSONObject marker) throws Exception {
        JSONObject result = new JSONObject();
        for (String name : PUBLIC_FIELDS) if (marker.has(name)) {
            Object value = marker.get(name);
            if (name.equals("currentAnchor") || name.equals("lastCompletedAnchor")) {
                if (!(value instanceof JSONObject)) throw new IllegalStateException("SPEECH_POSITION_INVALID");
                ReaderSpeechPlugin.publicAnchor((JSONObject) value);
            }
            result.put(name, value);
        }
        return result;
    }
    synchronized void save(JSONObject marker, String owner, String origin) throws Exception {
        if (!ReaderSpeechContextPolicy.validNonce(owner) || origin == null || origin.isEmpty()) throw new IllegalStateException("SPEECH_CONTEXT_CHANGED");
        JSONObject stored = publicMarker(marker); stored.put("speechOwner", owner); stored.put("speechOrigin", origin);
        byte[] plain = stored.toString().getBytes(StandardCharsets.UTF_8);
        if (plain.length > 50000) throw new IllegalStateException("SPEECH_POSITION_SAVE_FAILED");
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding"); cipher.init(Cipher.ENCRYPT_MODE, key(true));
        cipher.updateAAD(alias().getBytes(StandardCharsets.UTF_8));
        String encoded = "v1:" + Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP) + ":"
            + Base64.encodeToString(cipher.doFinal(plain), Base64.NO_WRAP);
        if (!context.getSharedPreferences("reader-speech-marker", Context.MODE_PRIVATE).edit().putString("marker", encoded).commit())
            throw new IllegalStateException("SPEECH_POSITION_SAVE_FAILED");
    }
    private JSONObject readStored() throws Exception {
        String encoded = context.getSharedPreferences("reader-speech-marker", Context.MODE_PRIVATE).getString("marker", null);
        if (encoded == null) return null;
        if (encoded.length() > 100000) throw new IllegalStateException("SPEECH_POSITION_INVALID");
        String[] parts = encoded.split(":", -1);
        if (parts.length != 3 || !"v1".equals(parts[0])) throw new IllegalStateException("SPEECH_POSITION_INVALID");
        byte[] iv = Base64.decode(parts[1], Base64.NO_WRAP);
        if (iv.length != 12) throw new IllegalStateException("SPEECH_POSITION_INVALID");
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding"); cipher.init(Cipher.DECRYPT_MODE, key(false), new GCMParameterSpec(128, iv));
        cipher.updateAAD(alias().getBytes(StandardCharsets.UTF_8));
        JSONObject marker = new JSONObject(new String(cipher.doFinal(Base64.decode(parts[2], Base64.NO_WRAP)), StandardCharsets.UTF_8));
        return marker;
    }
    private boolean matches(JSONObject marker, String owner, String origin) {
        return marker != null && owner.equals(marker.opt("speechOwner")) && origin.equals(marker.opt("speechOrigin"));
    }
    synchronized void ensureBinding(String owner, String origin) throws Exception {
        JSONObject marker = readStored();
        if (marker != null && !matches(marker, owner, origin)) clear();
    }
    synchronized JSONObject read(String scope, String owner, String origin) throws Exception {
        JSONObject marker = readStored();
        if (marker == null) return null;
        if (!matches(marker, owner, origin)) { clear(); return null; }
        return scope.equals(marker.optString("scopeKey")) ? publicMarker(marker) : null;
    }
    synchronized void clear() throws Exception {
        if (!context.getSharedPreferences("reader-speech-marker", Context.MODE_PRIVATE).edit().clear().commit())
            throw new IllegalStateException("SPEECH_POSITION_CLEAR_FAILED");
    }
}
