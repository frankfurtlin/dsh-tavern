import java.io.File;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * Host-only probe of two exact production source blocks. The public Python
 * runner injects those blocks into the marked slots before compiling. This
 * exercises authority and real private-fixture files, not Android playback.
 */
public final class ReaderSpeechAudioAuthorityProbe {
    /* __ACTUAL_AUDIO_AUTHORITY__ */

    private File audioDirectory;
    private final Set<File> ownedAudioFiles = new LinkedHashSet<>();
    private boolean audioClearPending;
    /* __ACTUAL_CLEAR_OWNED_AUDIO__ */

    private static final List<String> passed = new ArrayList<>();
    private static void check(boolean value, String name) {
        if (!value) throw new AssertionError(name);
        passed.add(name);
    }
    private static void fixture(boolean value, String name) {
        if (!value) throw new AssertionError("Fixture preparation: " + name);
    }
    private static AudioAuthority authority() {
        return new AudioAuthority("ticket", 1, 2, 3, "media", "owner", "origin");
    }
    public static void main(String[] args) throws Exception {
        if (args.length != 1) throw new IllegalArgumentException("Owned fixture directory required");
        AudioAuthority a = authority();
        check(a.current("ticket",1,2,3,"media","owner","origin",true,true), "exact-authority");
        check(!a.current("other",1,2,3,"media","owner","origin",true,true), "cross-ticket-rejected");
        check(!a.current("ticket",9,2,3,"media","owner","origin",true,true), "old-engine-rejected");
        check(!a.current("ticket",1,9,3,"media","owner","origin",true,true), "old-account-lease-rejected");
        check(!a.current("ticket",1,2,9,"media","owner","origin",true,true), "old-document-reset-rejected");
        check(!a.current("ticket",1,2,3,"old-media","owner","origin",true,true), "old-media-token-rejected");
        check(!a.current("ticket",1,2,3,"media","other-owner","origin",true,true), "cross-owner-rejected");
        check(!a.current("ticket",1,2,3,"media","owner","other-origin",true,true), "cross-origin-rejected");
        check(!a.current("ticket",1,2,3,"media","owner","origin",false,true), "revoked-account-rejected");
        check(!a.current("ticket",1,2,3,"media","owner","origin",true,false), "invalidated-queue-ticket-rejected");
        check(!a.playbackCompleted() && !a.playbackStarted(), "synthesis-required-before-playback");
        check(a.synthesized() && !a.synthesized() && !a.playbackCompleted(), "synthesis-does-not-complete-playback");
        check(a.playbackStarted() && !a.playbackStarted(), "actual-playback-start-once");
        check(a.playbackCompleted() && !a.playbackCompleted()
                && !a.current("ticket",1,2,3,"media","owner","origin",true,true), "completion-once-invalidates-authority");

        ReaderSpeechAudioAuthorityProbe s = new ReaderSpeechAudioAuthorityProbe();
        File root = new File(args[0]).getCanonicalFile();
        fixture(root.mkdir(), "private root");
        s.audioDirectory = root;
        File own = new File(root, "speech-11111111111111111111111111111111.wav");
        File unrelated = new File(root, "unrelated-other-file.wav");
        fixture(own.createNewFile() && unrelated.createNewFile(), "exact and unrelated files");
        s.ownedAudioFiles.add(own);
        check(s.clearOwnedAudio() && !own.exists() && unrelated.exists(), "only-exact-owned-file-cleared");
        File outside = new File(root.getParentFile(), "speech-22222222222222222222222222222222.wav");
        fixture(outside.createNewFile(), "outside private root, inside owned test workspace");
        s.ownedAudioFiles.add(outside);
        check(!s.clearOwnedAudio() && outside.exists() && s.audioClearPending, "outside-path-retained-and-blocked");
        s.ownedAudioFiles.clear();
        File directory = new File(root, "speech-33333333333333333333333333333333.wav");
        fixture(directory.mkdir(), "directory resembling filename");
        s.ownedAudioFiles.add(directory);
        check(!s.clearOwnedAudio() && directory.isDirectory(), "no-recursive-directory-delete");
        s.ownedAudioFiles.clear(); s.audioDirectory = null;
        check(!s.clearOwnedAudio(), "unavailable-private-root-fails-closed");
        System.out.print("{\"authorityCases\":14,\"fileCases\":4,\"cases\":[");
        for (int i = 0; i < passed.size(); i++) {
            if (i != 0) System.out.print(",");
            System.out.print("\"" + passed.get(i) + "\"");
        }
        System.out.println("],\"failures\":0,\"actualAndroidFramework\":false,\"audioPlaybackCertified\":false}");
    }
}
