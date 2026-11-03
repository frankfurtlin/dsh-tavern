package app.storyreader.personal;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.os.IBinder;
import android.util.Base64;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import com.getcapacitor.JSObject;
import java.util.Arrays;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

/** Actual Android platform checks. No synthetic speech engine or audible-playback claim. */
@RunWith(AndroidJUnit4.class)
public class ReaderSpeechPlatformTest {
    private static final String OWNER = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", ORIGIN = "https://owned-speech.invalid";
    private Context context() { return InstrumentationRegistry.getInstrumentation().getTargetContext(); }
    private static <T> T ui(Callable<T> operation) throws Exception {
        AtomicReference<T> result = new AtomicReference<>(); AtomicReference<Throwable> failure = new AtomicReference<>();
        InstrumentationRegistry.getInstrumentation().runOnMainSync(() -> {
            try { result.set(operation.call()); } catch (Throwable error) { failure.set(error); }
        });
        if (failure.get() instanceof Exception) throw (Exception) failure.get();
        if (failure.get() instanceof Error) throw (Error) failure.get();
        return result.get();
    }
    @Test public void systemKeystoreMarkerIsAuthenticatedAndOwnerScoped() throws Exception {
        ReaderSpeechStateStore store = new ReaderSpeechStateStore(context()); store.clear();
        try {
            JSONObject marker = new JSONObject().put("scopeKey", "owned-next-native-scope-A")
                .put("currentId", "owned-next-paragraph-one").put("title", "owned-original-private-test-marker");
            store.save(marker, OWNER, ORIGIN);
            String raw = context().getSharedPreferences("reader-speech-marker", Context.MODE_PRIVATE).getString("marker", "");
            assertTrue(raw.startsWith("v1:")); assertFalse(raw.contains("owned-original-private-test-marker"));
            JSONObject publicMarker = store.read("owned-next-native-scope-A", OWNER, ORIGIN);
            assertEquals("owned-next-paragraph-one", publicMarker.getString("currentId"));
            assertFalse(publicMarker.has("speechOwner")); assertFalse(publicMarker.has("speechOrigin"));
            assertNull(store.read("owned-next-native-scope-B", OWNER, ORIGIN));
            String[] parts = raw.split(":"); byte[] body = Base64.decode(parts[2], Base64.NO_WRAP); body[0] ^= 1;
            assertTrue(context().getSharedPreferences("reader-speech-marker", Context.MODE_PRIVATE).edit()
                .putString("marker", parts[0] + ":" + parts[1] + ":" + Base64.encodeToString(body, Base64.NO_WRAP)).commit());
            boolean rejected = false; try { store.read("owned-next-native-scope-A", OWNER, ORIGIN); } catch (Exception expected) { rejected = true; }
            assertTrue("authenticated ciphertext must reject tampering", rejected);
        } finally { store.clear(); }
    }
    @Test public void boundServiceDoesNotAutoplayOrLeakOtherScope() throws Exception {
        Context target = context(); AtomicReference<ReaderSpeechService> connected = new AtomicReference<>();
        CountDownLatch ready = new CountDownLatch(1);
        ServiceConnection connection = new ServiceConnection() {
            @Override public void onServiceConnected(ComponentName name, IBinder binder) {
                connected.set(((ReaderSpeechService.LocalBinder) binder).service()); ready.countDown();
            }
            @Override public void onServiceDisconnected(ComponentName name) { connected.set(null); }
        };
        assertTrue(target.bindService(new Intent(target, ReaderSpeechService.class), connection, Context.BIND_AUTO_CREATE));
        try {
            assertTrue("actual Android service must bind", ready.await(10, TimeUnit.SECONDS));
            ui(() -> {
                ReaderSpeechService service = connected.get(); service.stop(true); service.bindAccount(OWNER, ORIGIN);
                String text = "这是一份原创的听书测试。";
                ReaderSpeechQueue.Segment segment = new ReaderSpeechQueue.Segment("owned-p1", text, "owned-c1", "原创第一章", new JSONObject().put("blockId", "owned-b1").put("start", 0).put("end", text.length()).put("offsetUnit", "utf16").toString());
                service.load("owned-scope-A", "原创平台测试", Arrays.asList(segment), null, 1.25, "", true, 0);
                JSObject initial = service.state("owned-scope-A");
                assertTrue(initial.getBoolean("queueLoaded")); assertEquals("owned-p1", initial.getString("currentId"));
                assertFalse(initial.has("lastCompletedAnchor")); assertNotEquals("playing", initial.getString("status")); assertNotEquals("starting", initial.getString("status"));
                boolean rejected = false;
                try { service.load("owned-scope-B", "无效替换", Arrays.asList(segment), null, 1, "", false, 2); }
                catch (IllegalArgumentException expected) { rejected = true; }
                assertTrue(rejected); assertEquals("owned-scope-A", service.state("owned-scope-A").getString("scopeKey"));
                service.stop(false);
                JSObject restored = service.state("owned-scope-A");
                assertFalse(restored.getBoolean("queueLoaded")); assertEquals("process-restored", restored.getString("reason"));
                assertEquals("owned-p1", restored.getString("currentId")); assertFalse(restored.has("lastCompletedAnchor"));
                service.load("owned-scope-A", "原创平台测试", Arrays.asList(segment), null, 1, "", false, 0);
                assertEquals("idle", service.state("owned-scope-B").getString("status"));
                assertEquals("idle", service.state("owned-scope-A").getString("status"));
                service.stop(true); return null;
            });
        } finally {
            if (connected.get() != null) ui(() -> { connected.get().stop(true); return null; });
            target.unbindService(connection);
        }
    }
}
