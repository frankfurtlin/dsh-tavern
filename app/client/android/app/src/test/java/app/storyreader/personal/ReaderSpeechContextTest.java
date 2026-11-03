package app.storyreader.personal;

import java.util.Arrays;
import org.junit.Test;
import static org.junit.Assert.*;

public class ReaderSpeechContextTest {
    private static final String A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    interface Checked { void run(); }
    void rejects(String code, Checked operation) {
        try { operation.run(); fail("must reject"); } catch (IllegalArgumentException | IllegalStateException error) { assertEquals(code, error.getMessage()); }
    }
    @Test public void exactSameHandshakeKeepsCapturedCommandValid() {
        ReaderSpeechContextPolicy policy = new ReaderSpeechContextPolicy();
        assertTrue(policy.select(A, "https://EXAMPLE.invalid:443/", 0, false)); policy.markReady();
        ReaderSpeechContextPolicy.Ticket first = policy.capture();
        assertFalse(policy.select(A, "https://example.invalid", 0, false)); policy.markReady();
        assertTrue(policy.valid(first, 0));
    }
    @Test public void accountChangeFencesCapturedCommandBeforeReady() {
        ReaderSpeechContextPolicy policy = new ReaderSpeechContextPolicy(); policy.select(A, "https://a.invalid", 0, false); policy.markReady();
        ReaderSpeechContextPolicy.Ticket previous = policy.capture();
        policy.select(B, "https://a.invalid", 1, false);
        assertFalse(policy.valid(previous, 0)); assertNull(policy.capture());
        policy.markReady(); assertTrue(policy.valid(policy.capture(), 1));
    }
    @Test public void staleOrSameEpochDifferentIdentityCannotMutateCurrent() {
        ReaderSpeechContextPolicy policy = new ReaderSpeechContextPolicy(); policy.select(A, "https://a.invalid", 8, false); policy.markReady();
        ReaderSpeechContextPolicy.Ticket first = policy.capture();
        rejects("SPEECH_CONTEXT_CHANGED", () -> policy.select(B, "https://a.invalid", 8, false));
        rejects("SPEECH_CONTEXT_CHANGED", () -> policy.select(A, "https://b.invalid", 8, false));
        rejects("SPEECH_CONTEXT_CHANGED", () -> policy.select(A, "https://a.invalid", 7, false));
        assertTrue(policy.valid(first, 8));
    }
    @Test public void revokeSealsEpochAndCannotReactivateSameIdentity() {
        ReaderSpeechContextPolicy policy = new ReaderSpeechContextPolicy(); policy.select(A, "https://a.invalid", 0, false); policy.markReady();
        ReaderSpeechContextPolicy.Ticket first = policy.capture();
        rejects("SPEECH_CONTEXT_CHANGED", () -> policy.revoke(0));
        policy.revoke(1); policy.revoke(1); assertFalse(policy.valid(first, 0)); assertNull(policy.capture());
        rejects("SPEECH_CONTEXT_CHANGED", () -> policy.select(A, "https://a.invalid", 1, false));
        policy.select(A, "https://a.invalid", 2, false); policy.markReady(); assertTrue(policy.valid(policy.capture(), 2));
    }
    @Test public void newDocumentEpochZeroDoesNotAuthorizeRetiredTicket() {
        ReaderSpeechContextPolicy policy = new ReaderSpeechContextPolicy(); policy.select(A, "https://a.invalid", 9, false); policy.markReady();
        ReaderSpeechContextPolicy.Ticket first = policy.capture(); policy.resetDocument();
        policy.select(A, "https://a.invalid", 0, false); policy.markReady();
        assertFalse(policy.valid(first, 9)); assertTrue(policy.valid(policy.capture(), 0));
    }
    @Test public void storageSetupFailureCanRetryExactUnreadyCandidate() {
        ReaderSpeechContextPolicy policy = new ReaderSpeechContextPolicy(); policy.select(A, "https://a.invalid", 0, false);
        assertNull(policy.capture()); assertFalse(policy.select(A, "https://a.invalid", 0, false));
        policy.markReady(); assertTrue(policy.valid(policy.capture(), 0));
    }
    @Test public void originsRejectCredentialsTraversalAndLookalikeLoopback() {
        for (String origin : Arrays.asList("https://u:p@a.invalid", "https://a.invalid/path", "https://a.invalid/%2e", "https://a.invalid/?x", "https://a.invalid/#x", "https://a.invalid:0", "https://a.invalid:65536", "http://localhost.evil", "http://127.0.0.2", "http://127.1", "http://10.0.2.20"))
            rejects("INVALID_SPEECH_CONTEXT", () -> ReaderSpeechContextPolicy.origin(origin, true));
        rejects("INVALID_SPEECH_CONTEXT", () -> ReaderSpeechContextPolicy.origin("http://localhost", false));
    }
    @Test public void debugOriginGateSupportsExactIpv6AndLoopbacks() {
        for (String origin : Arrays.asList("http://localhost:1234", "http://127.0.0.1:1234", "http://10.0.2.2:1234", "http://[::1]:1234")) assertEquals(origin, ReaderSpeechContextPolicy.origin(origin, true));
    }
    @Test public void nonceAndEpochRequireExactBoundedTypes() {
        assertTrue(ReaderSpeechContextPolicy.sameDocument(A, A)); assertFalse(ReaderSpeechContextPolicy.sameDocument(A, B));
        assertFalse(ReaderSpeechContextPolicy.validNonce(A.toUpperCase())); assertFalse(ReaderSpeechContextPolicy.sameDocument(A, new Object()));
        assertEquals(ReaderSpeechContextPolicy.MAX_EPOCH, ReaderSpeechContextPolicy.epoch(ReaderSpeechContextPolicy.MAX_EPOCH));
        for (Object value : Arrays.asList("1", -1, .5, Double.NaN, Double.POSITIVE_INFINITY, 9007199254740992d, true)) rejects("INVALID_SPEECH_CONTEXT", () -> ReaderSpeechContextPolicy.epoch(value));
    }
    @Test public void malformedUtf16ReplacementKeepsOriginalQueueAndEmojiChunks() {
        ReaderSpeechQueue queue = new ReaderSpeechQueue();
        ReaderSpeechQueue.Segment valid = new ReaderSpeechQueue.Segment("p", "😀中文", "c", "章", "{\"start\":0,\"end\":4,\"offsetUnit\":\"utf16\"}");
        queue.load("original", "title", Arrays.asList(valid), null, 1, "", false);
        for (String text : Arrays.asList("\ud800x", "x\udc00", "\ud800")) {
            ReaderSpeechQueue.Segment broken = new ReaderSpeechQueue.Segment("b", text, "c", "章", "{\"start\":0,\"end\":" + text.length() + ",\"offsetUnit\":\"utf16\"}");
            rejects("INVALID_SPEECH_SEGMENT", () -> queue.load("replacement", "title", Arrays.asList(broken), null, 1, "", false));
            assertEquals("original", queue.scopeKey); assertEquals("p", queue.current().id);
        }
        queue.play(); assertEquals("😀", queue.beginUtterance(2).text);
    }
}
