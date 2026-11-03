package app.storyreader.personal;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.ApplicationInfo;
import android.content.pm.ServiceInfo;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioManager;
import android.media.MediaMetadata;
import android.media.MediaPlayer;
import android.media.session.MediaSession;
import android.media.session.PlaybackState;
import android.net.Uri;
import android.os.Binder;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.speech.tts.Voice;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import org.json.JSONObject;

/** System TTS and media controls continue independently of the WebView. */
public class ReaderSpeechService extends Service {
    interface Listener { void changed(JSObject state, String owner, String origin); }
    private static volatile ReaderSpeechService instance;
    static ReaderSpeechService live() { return instance; }
    public final class LocalBinder extends Binder { ReaderSpeechService service() { return ReaderSpeechService.this; } }
    private static final String CHANNEL = "reader-system-speech";
    private static final int NOTIFICATION = 47041;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ReaderSpeechQueue queue = new ReaderSpeechQueue();
    private final Set<Listener> listeners = new LinkedHashSet<>();
    private final LocalBinder binder = new LocalBinder();
    private ReaderSpeechStateStore store;
    private String speechOwner = "", speechOrigin = "";
    private long accountLease;
    private volatile long resetGeneration;
    private boolean accountReady, clearPending;
    private TextToSpeech tts;
    private boolean initialized, initializing, foreground, focusHeld;
    private long engineGeneration;
    private AudioAttributes speechAttributes;
    private Runnable startTimeout;
    private AudioAuthority audioAuthority;
    private File audioFile;
    private MediaPlayer player;
    private File audioDirectory;
    private final Set<File> ownedAudioFiles = new LinkedHashSet<>();
    private boolean audioClearPending;
    private static final long MAX_AUDIO_BYTES = 64L * 1024 * 1024;
    /** Captured authority also fences synthesis and player callbacks after reset. */
    static final class AudioAuthority {
        final String ticket, identity, owner, origin;
        final long engine, lease, reset;
        private boolean synthesized, playbackStarted, completed;
        AudioAuthority(String ticket, long engine, long lease, long reset, String identity, String owner, String origin) {
            this.ticket = ticket; this.engine = engine; this.lease = lease; this.reset = reset;
            this.identity = identity; this.owner = owner; this.origin = origin;
        }
        boolean current(String id, long currentEngine, long currentLease, long currentReset, String currentIdentity,
                String currentOwner, String currentOrigin, boolean accountActive, boolean ticketAccepted) {
            return !completed && accountActive && ticketAccepted && ticket.equals(id) && engine == currentEngine
                && lease == currentLease && reset == currentReset && identity.equals(currentIdentity)
                && owner.equals(currentOwner) && origin.equals(currentOrigin);
        }
        boolean synthesized() { if (synthesized || completed) return false; synthesized = true; return true; }
        boolean playbackStarted() { if (!synthesized || playbackStarted || completed) return false; playbackStarted = true; return true; }
        boolean playbackCompleted() { if (!playbackStarted || completed) return false; completed = true; return true; }
    }
    private String resolvedVoiceId = "";
    private boolean voiceFallback;
    private AudioManager audio;
    private AudioFocusRequest focusRequest;
    private MediaSession session;
    private String mediaIdentity = "";
    private PowerManager.WakeLock wakeLock;
    private long sleepDeadline;
    private final Runnable sleep = () -> pause("sleep-timer");
    private AudioManager.OnAudioFocusChangeListener focusChanged;
    private long focusEpoch;
    private AudioManager.OnAudioFocusChangeListener createFocusListener(long requestEpoch) {
        return change -> {
            // The SDK dispatches this listener on main. A second post can apply
            // an old loss to a newly selected story; each request also fences
            // framework callbacks that arrive after its focus was abandoned.
            if (requestEpoch != focusEpoch || !focusHeld || change == AudioManager.AUDIOFOCUS_GAIN) return;
            pause("audio-interruption");
            // Returning focus requires a new explicit play gesture.
        };
    }
    private final BroadcastReceiver noisy = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) { pause("headphones-disconnected"); }
    };
    @Override public void onCreate() {
        super.onCreate(); store = new ReaderSpeechStateStore(this);
        // Private PCM is a transient playback resource, never a cold-start queue.
        // Clean only this service's exact random filenames, with no recursion.
        try {
            audioDirectory = new File(getCacheDir(), "reader-system-speech-audio").getCanonicalFile();
            if (!audioDirectory.getParentFile().equals(getCacheDir().getCanonicalFile())) throw new IOException("Private audio directory escaped");
            if (!audioDirectory.isDirectory() && !audioDirectory.mkdir()) throw new IOException("Private audio directory unavailable");
            File[] residual = audioDirectory.listFiles();
            if (residual == null || residual.length > 128) throw new IOException("Private audio cleanup unavailable");
            for (File file : residual) if (file.getName().matches("speech-[a-f0-9]{32}\\.wav")) ownedAudioFiles.add(file);
            clearOwnedAudio();
        } catch (Exception error) { audioDirectory = null; audioClearPending = true; }
        audio = (AudioManager) getSystemService(AUDIO_SERVICE);
        PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
        wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, getPackageName() + ":system-speech");
        wakeLock.setReferenceCounted(false);
        AudioAttributes attributes = new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build();
        speechAttributes = attributes;
        if (Build.VERSION.SDK_INT >= 26) {
            ((NotificationManager) getSystemService(NOTIFICATION_SERVICE)).createNotificationChannel(
                new NotificationChannel(CHANNEL, "系统听书", NotificationManager.IMPORTANCE_LOW));
        }
        createMediaSession();
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(noisy, new IntentFilter(AudioManager.ACTION_AUDIO_BECOMING_NOISY), RECEIVER_NOT_EXPORTED);
        else registerReceiver(noisy, new IntentFilter(AudioManager.ACTION_AUDIO_BECOMING_NOISY));
        createEngine(); instance = this;
    }
    private boolean currentMedia(String identity, long lease, String owner, String origin) {
        return instance == this && accountReady && !clearPending && mediaIdentity.equals(identity)
            && accountLease == lease && speechOwner.equals(owner) && speechOrigin.equals(origin);
    }
    private void createMediaSession() {
        // Each replacement owns a distinct system token. A previously issued
        // framework command cannot acquire the replacement queue's authority.
        if (session != null) { session.setCallback(null); session.setActive(false); session.setMetadata(null); session.release(); }
        mediaIdentity = UUID.randomUUID().toString().replace("-", "");
        final String identity = mediaIdentity, owner = speechOwner, origin = speechOrigin;
        final long lease = accountLease;
        session = new MediaSession(this, "ReaderSystemSpeech");
        session.setCallback(new MediaSession.Callback() {
            @Override public void onPlay() { if (currentMedia(identity, lease, owner, origin)) guardedPlay(); }
            @Override public void onPause() { if (currentMedia(identity, lease, owner, origin)) pause("user-paused"); }
            @Override public void onStop() { if (currentMedia(identity, lease, owner, origin)) guardedStop(); }
            @Override public void onSkipToPrevious() { if (currentMedia(identity, lease, owner, origin)) skip(-1); }
            @Override public void onSkipToNext() { if (currentMedia(identity, lease, owner, origin)) skip(1); }
        }, main);
        session.setFlags(MediaSession.FLAG_HANDLES_MEDIA_BUTTONS | MediaSession.FLAG_HANDLES_TRANSPORT_CONTROLS);
    }
    long bindAccount(String owner, String origin) throws Exception {
        if (!ReaderSpeechContextPolicy.validNonce(owner)) throw new IllegalArgumentException("INVALID_SPEECH_CONTEXT");
        origin = ReaderSpeechContextPolicy.origin(origin, (getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0);
        if (accountReady && speechOwner.equals(owner) && speechOrigin.equals(origin)) return accountLease;
        // Invalidate before touching storage. A failed clear cannot revive the
        // previous account or leave its lockscreen controls active.
        if (accountReady || clearPending) revokeAccount();
        try { store.ensureBinding(owner, origin); }
        catch (Exception error) { accountReady = false; throw new IllegalStateException("SPEECH_POSITION_READ_FAILED"); }
        speechOwner = owner; speechOrigin = origin; accountReady = true; accountLease++; createMediaSession();
        return accountLease;
    }
    void requireAccount(String owner, String origin, long lease) {
        if (instance != this || !accountReady || clearPending || !speechOwner.equals(owner) || !speechOrigin.equals(origin) || accountLease != lease)
            throw new IllegalStateException("SPEECH_CONTEXT_CHANGED");
    }
    long resetGeneration() { return resetGeneration; }
    void requireResetGeneration(long captured) { if (captured != resetGeneration) throw new IllegalStateException("SPEECH_CONTEXT_CHANGED"); }
    void interruptCommands() { resetGeneration++; }
    private void requireAccount() { if (instance != this || !accountReady || clearPending) throw new IllegalStateException("SPEECH_CONTEXT_CHANGED"); }
    void revokeAccount() throws Exception {
        accountReady = false; speechOwner = ""; speechOrigin = ""; accountLease++; clearPending = true;
        stop(true); clearPending = false;
    }
    private void createEngine() {
        long engine = ++engineGeneration; initialized = false; initializing = true;
        if (tts != null) tts.shutdown();
        tts = new TextToSpeech(this, result -> main.post(() -> {
            if (engine != engineGeneration) return;
            initializing = false;
            initialized = result == TextToSpeech.SUCCESS;
            if (initialized) tts.setAudioAttributes(speechAttributes);
            else queue.error("VOICE_ENGINE_UNAVAILABLE");
            publish();
        }));
        tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
            // File synthesis is not audible playback and cannot advance the queue.
            @Override public void onStart(String id) { }
            @Override public void onDone(String id) { main.post(() -> { if (engine == engineGeneration) synthesized(id); }); }
            @Override public void onError(String id) { onError(id, TextToSpeech.ERROR); }
            @Override public void onError(String id, int code) { main.post(() -> { if (engine == engineGeneration && currentAudio(audioAuthority, id)) fail("SYSTEM_SPEECH_FAILED"); }); }
            @Override public void onStop(String id, boolean interrupted) { main.post(() -> { if (engine == engineGeneration && currentAudio(audioAuthority, id)) pause("system-stopped"); }); }
        });
    }
    void retryEngine() { pause("voice-engine-retry"); createEngine(); }
    @Override public IBinder onBind(Intent intent) { return binder; }
    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null) {
            if (!currentMedia(intent.getStringExtra("speechMediaId"), intent.getLongExtra("speechLease", Long.MIN_VALUE), intent.getStringExtra("speechOwner"), intent.getStringExtra("speechOrigin"))) return START_NOT_STICKY;
            String action = intent.getAction();
            if ("play".equals(action)) guardedPlay();
            else if ("pause".equals(action)) pause("user-paused");
            else if ("next".equals(action)) skip(1);
            else if ("previous".equals(action)) skip(-1);
            else if ("stop".equals(action)) guardedStop();
        }
        return START_NOT_STICKY;
    }
    void addListener(Listener listener) { listeners.add(listener); }
    void removeListener(Listener listener) { listeners.remove(listener); }
    JSObject voices() {
        JSArray values = new JSArray(); boolean available = false;
        if (initialized && tts.getVoices() != null) for (Voice voice : tts.getVoices()) {
            if (!"zh".equals(voice.getLocale().getLanguage())) continue;
            JSObject item = new JSObject(); item.put("id", voice.getName()); item.put("name", voice.getName());
            item.put("language", voice.getLocale().toLanguageTag()); item.put("offline", readyOffline(voice));
            available |= readyOffline(voice); values.put(item);
        }
        JSObject result = new JSObject(); result.put("voices", values); result.put("available", available); result.put("initializing", initializing);
        if (!available) result.put("reason", initialized ? "NO_CHINESE_OFFLINE_VOICE" : "VOICE_ENGINE_UNAVAILABLE");
        return result;
    }
    private static boolean readyOffline(Voice voice) {
        return !voice.isNetworkConnectionRequired()
            && (voice.getFeatures() == null || !voice.getFeatures().contains(TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED));
    }
    private Voice selectedVoice() {
        if (!initialized || tts.getVoices() == null) return null;
        Voice fallback = null;
        for (Voice voice : tts.getVoices()) {
            if (!"zh".equals(voice.getLocale().getLanguage()) || !readyOffline(voice)) continue;
            if (voice.getName().equals(queue.voiceId)) { voiceFallback = false; return voice; }
            if (fallback == null || "CN".equals(voice.getLocale().getCountry())) fallback = voice;
        }
        voiceFallback = !queue.voiceId.isEmpty() && fallback != null;
        return fallback;
    }
    void load(String scope, String title, List<ReaderSpeechQueue.Segment> values, String from, double rate, String voice, boolean endChapter, int minutes) throws Exception {
        requireAccount();
        ReaderSpeechQueue.checkSleep(minutes);
        // Validate the entire replacement before pausing or changing the current owner.
        new ReaderSpeechQueue().load(scope, title, values, from, rate, voice, endChapter);
        for (ReaderSpeechQueue.Segment value : values) ReaderSpeechPlugin.publicAnchor(new JSONObject(value.anchor));
        pause("loading");
        if (audioClearPending) throw new IllegalStateException("SPEECH_AUDIO_CLEAR_FAILED");
        queue.load(scope, title, values, from, rate, voice, endChapter); createMediaSession();
        resolvedVoiceId = ""; voiceFallback = false;
        configureSleep(minutes); persist(); publish();
    }
    void configure(double rate, String voice, boolean endChapter, int minutes) throws Exception {
        requireAccount();
        ReaderSpeechQueue.checkRate(rate);
        ReaderSpeechQueue.checkSleep(minutes);
        if (voice == null || voice.length() > 500) throw new IllegalArgumentException("INVALID_SPEECH_VOICE");
        boolean playing = queue.wantsPlayback(); pause("preferences-changed");
        queue.rate = rate; queue.voiceId = voice; queue.stopAtChapterEnd = endChapter;
        configureSleep(minutes); persist();
        if (playing) play(); else publish();
    }
    private void configureSleep(int minutes) {
        ReaderSpeechQueue.checkSleep(minutes);
        main.removeCallbacks(sleep); sleepDeadline = minutes == 0 ? 0 : SystemClock.elapsedRealtime() + minutes * 60000L;
        if (minutes != 0) main.postDelayed(sleep, minutes * 60000L);
    }
    void play() throws Exception {
        requireAccount();
        if (!initialized) throw new IllegalStateException("VOICE_ENGINE_UNAVAILABLE");
        Voice voice = selectedVoice();
        if (voice == null || tts.setVoice(voice) != TextToSpeech.SUCCESS) throw new IllegalStateException("NO_CHINESE_OFFLINE_VOICE");
        resolvedVoiceId = voice.getName();
        if (sleepDeadline != 0 && sleepDeadline <= SystemClock.elapsedRealtime()) throw new IllegalStateException("SLEEP_TIMER_EXPIRED");
        if (queue.current() == null) throw new IllegalStateException("SPEECH_QUEUE_EMPTY");
        if (tts != null) tts.stop();
        if (!cancelAudio()) throw new IllegalStateException("SPEECH_AUDIO_CLEAR_FAILED");
        try {
            ensureForeground();
            abandonFocus();
            focusChanged = createFocusListener(++focusEpoch);
            if (Build.VERSION.SDK_INT >= 26) {
                focusRequest = new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN).setAudioAttributes(speechAttributes)
                    .setWillPauseWhenDucked(true).setAcceptsDelayedFocusGain(false).setOnAudioFocusChangeListener(focusChanged, main).build();
            }
            int granted = Build.VERSION.SDK_INT >= 26 ? audio.requestAudioFocus(focusRequest)
                : audio.requestAudioFocus(focusChanged, AudioManager.STREAM_MUSIC, AudioManager.AUDIOFOCUS_GAIN);
            if (granted != AudioManager.AUDIOFOCUS_REQUEST_GRANTED) throw new IllegalStateException("AUDIO_FOCUS_UNAVAILABLE");
            focusHeld = true; wakeLock.acquire(7200000L);
            tts.setSpeechRate((float) queue.rate); queue.play();
            try { persist(); } catch (Exception error) { throw new IllegalStateException("SPEECH_POSITION_SAVE_FAILED"); }
            publish(); speakNext();
        } catch (Exception error) { fail(safeCode(error)); throw error; }
    }
    private void guardedPlay() { try { play(); } catch (Exception error) { fail(safeCode(error)); } }
    private void guardedStop() { try { stop(false); } catch (Exception error) { fail(safeCode(error)); } }
    private void speakNext() {
        if (!queue.wantsPlayback()) return;
        if (sleepDeadline != 0 && sleepDeadline <= SystemClock.elapsedRealtime()) { pause("sleep-timer"); return; }
        // Bounded synthesis avoids one enormous transient PCM file or a long
        // initial wait. The queue still completes only whole original paragraphs.
        ReaderSpeechQueue.Utterance utterance = queue.beginUtterance(Math.min(512, TextToSpeech.getMaxSpeechInputLength()));
        if (utterance == null) return;
        if (!cancelAudio()) { fail("SPEECH_AUDIO_CLEAR_FAILED"); return; }
        final AudioAuthority captured = new AudioAuthority(utterance.ticket, engineGeneration, accountLease,
            resetGeneration, mediaIdentity, speechOwner, speechOrigin);
        audioAuthority = captured;
        try {
            if (audioDirectory == null || !audioDirectory.isDirectory()) throw new IOException("Private audio directory unavailable");
            audioFile = new File(audioDirectory, "speech-" + UUID.randomUUID().toString().replace("-", "") + ".wav");
            if (!audioFile.createNewFile()) throw new IOException("Private audio file unavailable");
            ownedAudioFiles.add(audioFile);
        } catch (Exception error) { fail("SYSTEM_SPEECH_FAILED"); return; }
        clearStartTimeout();
        startTimeout = () -> { if (currentAudio(captured, utterance.ticket)) fail("VOICE_ENGINE_UNAVAILABLE"); };
        main.postDelayed(startTimeout, 15000);
        if (tts.synthesizeToFile(utterance.text, null, audioFile, utterance.ticket) != TextToSpeech.SUCCESS) fail("SYSTEM_SPEECH_FAILED");
    }
    private boolean currentAudio(AudioAuthority captured, String id) {
        return audioAuthority == captured && captured != null && captured.current(id, engineGeneration, accountLease,
            resetGeneration, mediaIdentity, speechOwner, speechOrigin, instance == this && accountReady && !clearPending,
            queue.accepts(id));
    }
    private void synthesized(String id) {
        final AudioAuthority captured = audioAuthority;
        if (!currentAudio(captured, id) || !captured.synthesized()) return;
        try {
            final File file = audioFile;
            if (file == null || !file.getCanonicalFile().getParentFile().equals(audioDirectory)
                    || !file.isFile() || file.length() < 44 || file.length() > MAX_AUDIO_BYTES) throw new IOException("Invalid synthesized audio");
            final MediaPlayer next = new MediaPlayer(); player = next;
            next.setAudioAttributes(speechAttributes);
            // MediaPlayer plays real generated PCM under the Reader UID. A TTS
            // engine's own speak() audio UID cannot select our MediaSession.
            try (FileInputStream input = new FileInputStream(file)) { next.setDataSource(input.getFD()); }
            next.setOnPreparedListener(ready -> {
                if (player != ready || !currentAudio(captured, id)) return;
                try {
                    ready.start();
                    if (!ready.isPlaying() || !captured.playbackStarted()) throw new IllegalStateException("SYSTEM_SPEECH_FAILED");
                    if (queue.started(id)) { clearStartTimeout(); publish(); }
                } catch (Exception error) { fail("SYSTEM_SPEECH_FAILED"); }
            });
            next.setOnCompletionListener(done -> {
                if (player != done || !currentAudio(captured, id) || !captured.playbackCompleted()) return;
                if (!cancelAudio()) { fail("SPEECH_AUDIO_CLEAR_FAILED"); return; }
                complete(id); // Only real playback completion reaches this point.
            });
            next.setOnErrorListener((failed, what, extra) -> {
                if (player == failed && currentAudio(captured, id)) fail("SYSTEM_SPEECH_FAILED");
                return true;
            });
            next.prepareAsync();
        } catch (Exception error) { fail("SYSTEM_SPEECH_FAILED"); }
    }
    private boolean clearOwnedAudio() {
        for (File file : new LinkedHashSet<>(ownedAudioFiles)) {
            try {
                if (audioDirectory == null || !file.getCanonicalFile().getParentFile().equals(audioDirectory)
                        || !file.getName().matches("speech-[a-f0-9]{32}\\.wav")) continue;
                if (!file.exists() || file.isFile() && file.delete()) ownedAudioFiles.remove(file);
            } catch (Exception ignored) { /* Fail closed; retain the exact pending path. */ }
        }
        audioClearPending = audioDirectory == null || !audioDirectory.isDirectory() || !ownedAudioFiles.isEmpty();
        return !audioClearPending;
    }
    private boolean cancelAudio() {
        audioAuthority = null; audioFile = null;
        MediaPlayer previous = player; player = null;
        if (previous != null) {
            previous.setOnPreparedListener(null); previous.setOnCompletionListener(null); previous.setOnErrorListener(null);
            try { previous.release(); } catch (Exception ignored) { }
        }
        return clearOwnedAudio();
    }
    private void clearStartTimeout() { if (startTimeout != null) main.removeCallbacks(startTimeout); startTimeout = null; }
    private void complete(String id) {
        if (!queue.accepts(id)) return;
        clearStartTimeout();
        boolean paragraph = queue.finished(id);
        try { if (paragraph) persist(); } catch (Exception error) { fail("SPEECH_POSITION_SAVE_FAILED"); return; }
        publish();
        if (queue.wantsPlayback()) { wakeLock.acquire(7200000L); speakNext(); }
        else releasePlayback();
    }
    void pause(String reason) {
        clearStartTimeout(); queue.pause(reason); if (tts != null) tts.stop();
        if (!cancelAudio()) queue.error("SPEECH_AUDIO_CLEAR_FAILED");
        releasePlayback();
        try { if (!queue.scopeKey.isEmpty()) persist(); } catch (Exception error) { queue.error("SPEECH_POSITION_SAVE_FAILED"); }
        publish();
    }
    void skip(int direction) {
        boolean wasPlaying = queue.wantsPlayback(); pause("selected");
        try { queue.move(direction); persist(); if (wasPlaying) play(); else publish(); }
        catch (Exception error) { fail(safeCode(error)); }
    }
    void stop(boolean clearPosition) throws Exception {
        interruptCommands();
        pause("stopped"); queue.clear(); main.removeCallbacks(sleep); sleepDeadline = 0;
        boolean clearFailed = audioClearPending;
        try { if (clearPosition) store.clear(); } catch (Exception error) { clearFailed = true; queue.error("SPEECH_POSITION_CLEAR_FAILED"); }
        session.setActive(false); session.setMetadata(null); ((NotificationManager) getSystemService(NOTIFICATION_SERVICE)).cancel(NOTIFICATION);
        stopSelf(); publish();
        if (clearFailed) throw new IllegalStateException(audioClearPending ? "SPEECH_AUDIO_CLEAR_FAILED" : "SPEECH_POSITION_CLEAR_FAILED");
    }
    private void fail(String code) {
        clearStartTimeout(); queue.error(code); if (tts != null) tts.stop();
        if (!cancelAudio()) queue.error("SPEECH_AUDIO_CLEAR_FAILED");
        releasePlayback();
        try { if (!queue.scopeKey.isEmpty()) persist(); } catch (Exception ignored) { queue.error("SPEECH_POSITION_SAVE_FAILED"); }
        publish();
    }
    static String safeCode(Exception error) {
        String value = error.getMessage();
        return value != null && value.matches("[A-Z_]{3,80}") ? value : "SYSTEM_SPEECH_FAILED";
    }
    JSObject state(String scope) throws Exception {
        requireAccount();
        if (!queue.scopeKey.isEmpty() && !queue.scopeKey.equals(scope)) { stop(true); return emptyState(); }
        if (queue.scopeKey.isEmpty()) {
            JSONObject marker;
            try { marker = store.read(scope, speechOwner, speechOrigin); } catch (Exception error) { throw new IllegalStateException("SPEECH_POSITION_READ_FAILED"); }
            if (marker == null) return emptyState();
            JSObject result = new JSObject(marker.toString());
            if (!"ended".equals(result.optString("status"))) result.put("status", "paused");
            result.put("reason", "process-restored"); result.put("queueLoaded", false); return result;
        }
        return snapshot();
    }
    private JSObject emptyState() { JSObject value = new JSObject(); value.put("status", "idle"); value.put("queueLoaded", false); return value; }
    private JSObject snapshot() {
        JSObject value = new JSObject(); value.put("scopeKey", queue.scopeKey); value.put("title", queue.title); value.put("status", queue.status);
        value.put("reason", queue.reason); value.put("index", queue.index); value.put("count", queue.count()); value.put("eventVersion", queue.eventVersion);
        value.put("rate", queue.rate); value.put("voiceId", queue.voiceId); value.put("stopAtChapterEnd", queue.stopAtChapterEnd); value.put("queueLoaded", queue.count() > 0);
        value.put("resolvedVoiceId", resolvedVoiceId); value.put("voiceFallback", voiceFallback);
        value.put("sleepRemainingMs", sleepDeadline == 0 ? 0 : Math.max(0, sleepDeadline - SystemClock.elapsedRealtime()));
        ReaderSpeechQueue.Segment current = queue.current(), completed = queue.lastCompleted();
        if (current != null) { value.put("currentId", current.id); value.put("chapterId", current.chapterId); value.put("chapterTitle", current.chapterTitle); try { value.put("currentAnchor", new JSONObject(current.anchor)); } catch (Exception ignored) { } }
        if (completed != null) { value.put("lastCompletedId", completed.id); try { value.put("lastCompletedAnchor", new JSONObject(completed.anchor)); } catch (Exception ignored) { } }
        return value;
    }
    private void persist() throws Exception {
        if (!accountReady || clearPending) return;
        JSObject value = snapshot(); value.put("queueLoaded", false);
        try { store.save(value, speechOwner, speechOrigin); } catch (Exception error) { throw new IllegalStateException("SPEECH_POSITION_SAVE_FAILED"); }
    }
    private void publish() {
        updateMedia(); JSObject value = snapshot();
        for (Listener listener : new LinkedHashSet<>(listeners)) listener.changed(value, speechOwner, speechOrigin);
    }
    private void ensureForeground() {
        // A started service survives destruction of the activity and its plugin binding.
        // This method is reached only by an explicit app or media-control play action.
        startService(new Intent(this, ReaderSpeechService.class));
        if (Build.VERSION.SDK_INT >= 29) startForeground(NOTIFICATION, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
        else startForeground(NOTIFICATION, notification());
        foreground = true; session.setActive(true);
    }
    private void releasePlayback() {
        abandonFocus();
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        if (foreground) { stopForeground(false); foreground = false; }
    }
    private void abandonFocus() {
        focusEpoch++;
        boolean held = focusHeld; focusHeld = false;
        if (held) {
            if (Build.VERSION.SDK_INT >= 26) audio.abandonAudioFocusRequest(focusRequest);
            else audio.abandonAudioFocus(focusChanged);
        }
    }
    private PendingIntent action(String action) {
        Intent intent = new Intent(this, ReaderSpeechService.class).setAction(action)
            .setData(Uri.parse("reader-speech://control/" + mediaIdentity + "/" + action))
            .putExtra("speechMediaId", mediaIdentity).putExtra("speechLease", accountLease)
            .putExtra("speechOwner", speechOwner).putExtra("speechOrigin", speechOrigin);
        return PendingIntent.getService(this, action.hashCode(), intent, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }
    private Notification notification() {
        ReaderSpeechQueue.Segment current = queue.current();
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(this, CHANNEL) : new Notification.Builder(this);
        Intent launch = new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return builder.setSmallIcon(android.R.drawable.ic_media_play).setContentTitle(queue.title.isEmpty() ? "故事阅读" : queue.title)
            .setContentText(current == null ? "听书已结束" : current.chapterTitle).setOnlyAlertOnce(true).setShowWhen(false)
            .setContentIntent(PendingIntent.getActivity(this, 47041, launch, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE))
            .addAction(new Notification.Action.Builder(android.R.drawable.ic_media_previous, "上一段", action("previous")).build())
            .addAction(new Notification.Action.Builder(queue.wantsPlayback() ? android.R.drawable.ic_media_pause : android.R.drawable.ic_media_play, queue.wantsPlayback() ? "暂停" : "继续", action(queue.wantsPlayback() ? "pause" : "play")).build())
            .addAction(new Notification.Action.Builder(android.R.drawable.ic_media_next, "下一段", action("next")).build())
            .setDeleteIntent(action("stop")).setStyle(new Notification.MediaStyle().setMediaSession(session.getSessionToken()).setShowActionsInCompactView(0, 1, 2)).build();
    }
    private void updateMedia() {
        if (session == null) return;
        ReaderSpeechQueue.Segment current = queue.current();
        if (queue.count() == 0) session.setMetadata(null);
        else session.setMetadata(new MediaMetadata.Builder().putString(MediaMetadata.METADATA_KEY_TITLE, queue.title)
            .putString(MediaMetadata.METADATA_KEY_ARTIST, current == null ? "听书已结束" : current.chapterTitle).build());
        int status = "playing".equals(queue.status) ? PlaybackState.STATE_PLAYING : queue.count() == 0 ? PlaybackState.STATE_STOPPED : PlaybackState.STATE_PAUSED;
        session.setPlaybackState(new PlaybackState.Builder().setActions(PlaybackState.ACTION_PLAY | PlaybackState.ACTION_PAUSE | PlaybackState.ACTION_STOP | PlaybackState.ACTION_SKIP_TO_NEXT | PlaybackState.ACTION_SKIP_TO_PREVIOUS)
            .setState(status, PlaybackState.PLAYBACK_POSITION_UNKNOWN, queue.wantsPlayback() ? (float) queue.rate : 0).build());
        if (queue.count() > 0) ((NotificationManager) getSystemService(NOTIFICATION_SERVICE)).notify(NOTIFICATION, notification());
    }
    @Override public void onDestroy() {
        boolean current = instance == this;
        if (!current) accountReady = false;
        engineGeneration++;
        pause("service-stopped"); main.removeCallbacksAndMessages(null); unregisterReceiver(noisy);
        if (current) instance = null;
        if (tts != null) tts.shutdown(); session.release(); listeners.clear(); super.onDestroy();
    }
}
