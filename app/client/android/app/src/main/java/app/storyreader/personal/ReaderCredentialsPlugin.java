package app.storyreader.personal;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Refresh credentials have no plaintext fallback when AndroidKeyStore fails. */
@CapacitorPlugin(name = "ReaderCredentials")
public class ReaderCredentialsPlugin extends Plugin {
    private static final String ENTRY = "reader-refresh";
    private SharedPreferences preferences() {
        return getContext().getSharedPreferences("reader_credentials", Context.MODE_PRIVATE);
    }
    private String alias() { return getContext().getPackageName() + ".refresh.v1"; }
    private synchronized SecretKey key(boolean create) throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (store.containsAlias(alias())) return (SecretKey) store.getKey(alias(), null);
        if (!create) throw new IllegalStateException("Credential key unavailable");
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(alias(), KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
            .setKeySize(256).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setRandomizedEncryptionRequired(true).build());
        return generator.generateKey();
    }
    private void validate(PluginCall call) {
        if (!ENTRY.equals(call.getString("key"))) throw new IllegalArgumentException("Unsupported credential entry");
    }
    private byte[] associatedData() { return (alias() + ":" + ENTRY).getBytes(StandardCharsets.UTF_8); }
    @PluginMethod
    public void set(PluginCall call) {
        try {
            validate(call);
            String value = call.getString("value");
            if (value == null || value.isEmpty() || value.length() > 4096) throw new IllegalArgumentException("Invalid credential");
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key(true));
            cipher.updateAAD(associatedData());
            String encoded = "v1:" + Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP) + ":"
                + Base64.encodeToString(cipher.doFinal(value.getBytes(StandardCharsets.UTF_8)), Base64.NO_WRAP);
            if (!preferences().edit().putString(ENTRY, encoded).commit()) throw new IllegalStateException("Credential write unavailable");
            if (!getContext().getSharedPreferences("cap_sec", Context.MODE_PRIVATE).edit().remove(ENTRY).commit()) throw new IllegalStateException("Legacy credential removal unavailable");
            call.resolve();
        } catch (Exception ignored) { call.reject("Secure credential storage unavailable", "CREDENTIAL_STORAGE_UNAVAILABLE"); }
    }
    @PluginMethod
    public void get(PluginCall call) {
        try {
            validate(call);
            String encoded = preferences().getString(ENTRY, null);
            String value = "";
            if (encoded != null) {
                String[] parts = encoded.split(":", -1);
                if (parts.length != 3 || !"v1".equals(parts[0])) throw new IllegalStateException("Invalid credential record");
                byte[] iv = Base64.decode(parts[1], Base64.NO_WRAP);
                if (iv.length != 12) throw new IllegalStateException("Invalid credential IV");
                Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
                cipher.init(Cipher.DECRYPT_MODE, key(false), new GCMParameterSpec(128, iv));
                cipher.updateAAD(associatedData());
                value = new String(cipher.doFinal(Base64.decode(parts[2], Base64.NO_WRAP)), StandardCharsets.UTF_8);
            }
            JSObject result = new JSObject(); result.put("value", value); call.resolve(result);
        } catch (Exception ignored) { call.reject("Secure credential unavailable", "CREDENTIAL_STORAGE_UNAVAILABLE"); }
    }
    @PluginMethod
    public void remove(PluginCall call) {
        try {
            validate(call);
            if (!preferences().edit().remove(ENTRY).commit()) throw new IllegalStateException("Credential removal unavailable");
            if (!getContext().getSharedPreferences("cap_sec", Context.MODE_PRIVATE).edit().remove(ENTRY).commit()) throw new IllegalStateException("Legacy credential removal unavailable");
            call.resolve();
        } catch (Exception ignored) { call.reject("Secure credential removal unavailable", "CREDENTIAL_STORAGE_UNAVAILABLE"); }
    }
}
